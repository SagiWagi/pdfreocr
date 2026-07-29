#!/usr/bin/env python3
"""
pdfreocr — GUI do ponownego OCR plików PDF z obsługą polskich znaków.
Zastępuje złą warstwę tekstową (np. z Genius Scan) nową, wygenerowaną
przez Tesseract z modelem językowym 'pol'. Oryginalne obrazy stron pozostają.
"""

import os
import re
import sys
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

# ─── Stałe ────────────────────────────────────────────────────────────────────

APP_TITLE   = "pdfreocr — Naprawa warstwy tekstowej PDF"
APP_VERSION = "1.0.0"
DEFAULT_LANG = "pol"

TESSERACT_SEARCH_PATHS = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Program Files (x86)\Tesseract-OCR",
    r"C:\Users\piotr\AppData\Local\Programs\Tesseract-OCR",
]

TESSDATA_SEARCH_PATHS = [
    r"C:\Users\piotr\tessdata",
    r"C:\Program Files\Tesseract-OCR\tessdata",
    r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
]

# Wzorzec linii postępu w stderr ocrmypdf (np. "    3 page already has text!")
PAGE_PATTERN = re.compile(r"^\s+(\d+)\s+page", re.MULTILINE)


# ─── Helpery ──────────────────────────────────────────────────────────────────

def find_tesseract() -> Optional[str]:
    for p in TESSERACT_SEARCH_PATHS:
        if (Path(p) / "tesseract.exe").exists():
            return p
    return None


def find_tessdata() -> Optional[str]:
    for p in TESSDATA_SEARCH_PATHS:
        if (Path(p) / "pol.traineddata").exists():
            return p
    return None


def find_ocrmypdf() -> Optional[str]:
    """Szuka ocrmypdf w PATH i w folderze Scripts Pythona."""
    found = shutil.which("ocrmypdf")
    if found:
        return found
    scripts = Path(sys.executable).parent / "Scripts" / "ocrmypdf.exe"
    if scripts.exists():
        return str(scripts)
    return None


def get_pdf_pages(path: Path) -> int:
    try:
        import pikepdf
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


def format_time(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s} sek"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s:02d} sek"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d} min"


# ─── Model danych ─────────────────────────────────────────────────────────────

class FileStatus(Enum):
    PENDING    = "Oczekuje"
    PROCESSING = "Przetwarzanie"
    DONE       = "Gotowe"
    ERROR      = "Błąd"


@dataclass
class QueueItem:
    path: Path
    output_path: Path
    total_pages: int = 0
    processed_pages: int = 0
    status: FileStatus = FileStatus.PENDING
    error_msg: str = ""
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def progress(self) -> float:
        if self.total_pages == 0:
            return 0.0
        return min(self.processed_pages / self.total_pages, 1.0)

    @property
    def elapsed(self) -> float:
        if self.start_time == 0:
            return 0.0
        return (self.end_time or time.time()) - self.start_time

    @property
    def eta_seconds(self) -> Optional[float]:
        if self.processed_pages == 0 or self.elapsed == 0:
            return None
        rate = self.processed_pages / self.elapsed
        if rate == 0:
            return None
        return (self.total_pages - self.processed_pages) / rate


# ─── Wiersz kolejki ───────────────────────────────────────────────────────────

STATUS_ICONS = {
    FileStatus.PENDING:    ("○", "#6b7280"),
    FileStatus.PROCESSING: ("⟳", "#3b82f6"),
    FileStatus.DONE:       ("✓", "#22c55e"),
    FileStatus.ERROR:      ("✗", "#ef4444"),
}


class QueueRow(ctk.CTkFrame):

    def __init__(self, parent, item: QueueItem, on_remove: Callable, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        self.item = item
        self.on_remove = on_remove
        self.columnconfigure(2, weight=1)
        self._build()

    def _build(self):
        # Ikona
        self.lbl_icon = ctk.CTkLabel(self, text="○", width=26, font=("", 16))
        self.lbl_icon.grid(row=0, column=0, padx=(4, 4), pady=6)

        # Nazwa pliku
        name = self.item.path.name
        self.lbl_name = ctk.CTkLabel(self, text=name, anchor="w", font=("", 13))
        self.lbl_name.grid(row=0, column=1, sticky="w", padx=(0, 8))

        # Pasek postępu (ukryty dla PENDING)
        self.pb = ctk.CTkProgressBar(self, width=160, height=8)
        self.pb.set(0)
        self.pb.grid(row=0, column=2, sticky="ew", padx=8)

        # Strony / status
        pages = f"{self.item.total_pages} str." if self.item.total_pages else "… str."
        self.lbl_info = ctk.CTkLabel(
            self, text=pages, width=90, anchor="e",
            font=("", 12), text_color="#9ca3af"
        )
        self.lbl_info.grid(row=0, column=3, padx=8)

        # Przycisk usuń
        self.btn_rm = ctk.CTkButton(
            self, text="✕", width=28, height=28,
            fg_color="transparent", hover_color="#374151",
            command=lambda: self.on_remove(self.item)
        )
        self.btn_rm.grid(row=0, column=4, padx=(0, 4))

    def refresh(self):
        item = self.item
        icon, color = STATUS_ICONS[item.status]
        self.lbl_icon.configure(text=icon, text_color=color)

        if item.total_pages:
            self.lbl_name.configure(text=item.path.name)

        if item.status == FileStatus.PROCESSING:
            pct = int(item.progress * 100)
            self.pb.set(item.progress)
            self.lbl_info.configure(
                text=f"str. {item.processed_pages}/{item.total_pages}  {pct}%",
                text_color=color
            )
        elif item.status == FileStatus.DONE:
            self.pb.set(1.0)
            self.lbl_info.configure(
                text=f"✓ {format_time(item.elapsed)}", text_color=color
            )
        elif item.status == FileStatus.ERROR:
            self.pb.set(0)
            self.lbl_info.configure(text="Błąd", text_color=color)
        else:
            pages = f"{item.total_pages} str." if item.total_pages else "… str."
            self.lbl_info.configure(text=pages, text_color="#9ca3af")

        self.btn_rm.configure(
            state="disabled" if item.status == FileStatus.PROCESSING else "normal"
        )


# ─── Główna aplikacja ─────────────────────────────────────────────────────────

class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.geometry("820x620")
        self.minsize(700, 520)

        self.queue_items: List[QueueItem] = []
        self.queue_rows:  List[QueueRow]  = []
        self.is_running      = False
        self.stop_requested  = False

        self.tesseract_path = find_tesseract()
        self.tessdata_path  = find_tessdata()
        self.ocrmypdf_path  = find_ocrmypdf()

        self._build_ui()
        self._sync_buttons()

        # Informacja diagnostyczna
        if not self.tesseract_path or not self.tessdata_path:
            self.after(200, self._warn_missing_deps)

    # ── Diagnostyka ─────────────────────────────────────────────────────────

    def _warn_missing_deps(self):
        missing = []
        if not self.tesseract_path:
            missing.append("• Tesseract OCR (tesseract.exe nie znaleziony)")
        if not self.tessdata_path:
            missing.append("• Model języka polskiego (pol.traineddata)")
        if missing:
            messagebox.showwarning(
                "Brakujące zależności",
                "Nie znaleziono:\n" + "\n".join(missing) +
                "\n\nZainstaluj Tesseract OCR i pobierz pol.traineddata do folderu tessdata."
            )

    # ── Budowa UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Toolbar ─────────────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))

        ctk.CTkButton(
            bar, text="＋  Dodaj pliki PDF",
            command=self._add_files, width=160, height=36
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            bar, text="Wyczyść kolejkę",
            command=self._clear_queue,
            fg_color="transparent", border_width=1,
            width=130, height=36
        ).pack(side="left")

        ctk.CTkLabel(bar, text="Język OCR:", font=("", 13)).pack(side="right", padx=(8, 4))
        self.lang_var = ctk.StringVar(value=DEFAULT_LANG)
        ctk.CTkOptionMenu(
            bar, values=["pol", "eng", "pol+eng"],
            variable=self.lang_var, width=110
        ).pack(side="right")

        # ── Lista plików ─────────────────────────────────────────────────────
        list_outer = ctk.CTkFrame(self)
        list_outer.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        list_outer.grid_columnconfigure(0, weight=1)
        list_outer.grid_rowconfigure(0, weight=1)

        self.scroll = ctk.CTkScrollableFrame(
            list_outer,
            label_text="Kolejka plików",
            label_font=("", 13, "bold"),
        )
        self.scroll.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.scroll.grid_columnconfigure(0, weight=1)

        self.empty_lbl = ctk.CTkLabel(
            self.scroll,
            text='Kliknij "＋ Dodaj pliki PDF" lub przeciągnij pliki tutaj',
            text_color="#6b7280", font=("", 13)
        )
        self.empty_lbl.grid(row=0, column=0, pady=48)

        # ── Panel postępu ────────────────────────────────────────────────────
        prog = ctk.CTkFrame(self)
        prog.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 2))
        prog.grid_columnconfigure(1, weight=1)

        # Aktualny plik
        ctk.CTkLabel(prog, text="Aktualny plik:", font=("", 12, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 2))
        self.lbl_cur_name = ctk.CTkLabel(prog, text="—", font=("", 12), anchor="w")
        self.lbl_cur_name.grid(row=0, column=1, sticky="w", padx=4)
        self.lbl_eta = ctk.CTkLabel(prog, text="", font=("", 12), text_color="#9ca3af")
        self.lbl_eta.grid(row=0, column=2, sticky="e", padx=14)

        self.pb_cur = ctk.CTkProgressBar(prog, height=14)
        self.pb_cur.set(0)
        self.pb_cur.grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 6))

        self.lbl_cur_pages = ctk.CTkLabel(prog, text="", font=("", 12), text_color="#9ca3af")
        self.lbl_cur_pages.grid(row=2, column=0, columnspan=2, sticky="w", padx=14)

        # Całkowity postęp
        ctk.CTkLabel(prog, text="Całość:", font=("", 12, "bold")).grid(
            row=3, column=0, sticky="w", padx=12, pady=(6, 2))
        self.lbl_overall = ctk.CTkLabel(prog, text="0 / 0 plików", font=("", 12))
        self.lbl_overall.grid(row=3, column=1, sticky="w", padx=4)

        self.pb_all = ctk.CTkProgressBar(prog, height=10)
        self.pb_all.set(0)
        self.pb_all.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 10))

        # ── Przyciski Start / Stop ───────────────────────────────────────────
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 14))

        self.btn_start = ctk.CTkButton(
            btns, text="▶  Start",
            command=self._start,
            width=140, height=42, font=("", 15, "bold")
        )
        self.btn_start.pack(side="left", padx=(0, 10))

        self.btn_stop = ctk.CTkButton(
            btns, text="■  Stop",
            command=self._stop,
            width=120, height=42,
            fg_color="#dc2626", hover_color="#b91c1c",
            state="disabled"
        )
        self.btn_stop.pack(side="left")

        self.lbl_result = ctk.CTkLabel(btns, text="", font=("", 13))
        self.lbl_result.pack(side="right", padx=8)

    # ── Zarządzanie kolejką ──────────────────────────────────────────────────

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Wybierz pliki PDF",
            filetypes=[("Pliki PDF", "*.pdf"), ("Wszystkie pliki", "*.*")]
        )
        if paths:
            self._enqueue([Path(p) for p in paths])

    def _enqueue(self, paths: List[Path]):
        existing = {item.path for item in self.queue_items}
        for path in paths:
            if path in existing or not path.suffix.lower() == ".pdf":
                continue
            out = path.parent / (path.stem + "_ocr" + path.suffix)
            pages = get_pdf_pages(path)
            item = QueueItem(path=path, output_path=out, total_pages=pages)
            self.queue_items.append(item)
            self._add_row(item)
        if self.queue_items:
            self.empty_lbl.grid_remove()
        self._sync_buttons()

    def _add_row(self, item: QueueItem):
        row = QueueRow(self.scroll, item, on_remove=self._remove_item)
        row.grid(row=len(self.queue_rows), column=0, sticky="ew", padx=4, pady=2)
        self.queue_rows.append(row)

    def _remove_item(self, item: QueueItem):
        if item.status == FileStatus.PROCESSING:
            return
        idx = self.queue_items.index(item)
        self.queue_items.pop(idx)
        self.queue_rows.pop(idx).destroy()
        for i, r in enumerate(self.queue_rows):
            r.grid(row=i)
        if not self.queue_items:
            self.empty_lbl.grid(row=0, column=0, pady=48)
        self._sync_buttons()

    def _clear_queue(self):
        if self.is_running:
            messagebox.showwarning("Uwaga", "Najpierw zatrzymaj przetwarzanie.")
            return
        for row in self.queue_rows:
            row.destroy()
        self.queue_rows.clear()
        self.queue_items.clear()
        self.empty_lbl.grid(row=0, column=0, pady=48)
        self._reset_progress_ui()
        self._sync_buttons()

    # ── Start / Stop ─────────────────────────────────────────────────────────

    def _start(self):
        pending = [i for i in self.queue_items if i.status == FileStatus.PENDING]
        if not pending:
            messagebox.showinfo("Brak zadań", "Dodaj pliki PDF do kolejki.")
            return
        if not self.tesseract_path or not self.tessdata_path:
            self._warn_missing_deps()
            return
        if not self.ocrmypdf_path:
            messagebox.showerror("Brak ocrmypdf", "ocrmypdf nie jest zainstalowany.\nUruchom: pip install ocrmypdf")
            return

        self.is_running = True
        self.stop_requested = False
        self.lbl_result.configure(text="")
        self._sync_buttons()

        threading.Thread(target=self._worker, daemon=True).start()
        self._poll()

    def _stop(self):
        self.stop_requested = True
        self.btn_stop.configure(state="disabled", text="Zatrzymywanie…")

    # ── Wątek roboczy ────────────────────────────────────────────────────────

    def _worker(self):
        env = os.environ.copy()
        env["PATH"] = self.tesseract_path + os.pathsep + env.get("PATH", "")
        env["TESSDATA_PREFIX"] = self.tessdata_path

        for item in self.queue_items:
            if self.stop_requested:
                break
            if item.status != FileStatus.PENDING:
                continue

            item.status = FileStatus.PROCESSING
            item.start_time = time.time()
            item.processed_pages = 0

            try:
                self._run_ocr(item, env)
                if not self.stop_requested:
                    item.status = FileStatus.DONE
                    item.processed_pages = item.total_pages
            except Exception as exc:
                item.status = FileStatus.ERROR
                item.error_msg = str(exc)
            finally:
                item.end_time = time.time()

        self.is_running = False

    def _run_ocr(self, item: QueueItem, env: dict):
        cmd = [
            self.ocrmypdf_path,
            "--language", self.lang_var.get(),
            "--force-ocr",
            "--jobs", "4",
            str(item.path),
            str(item.output_path),
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        for line in proc.stderr:
            if self.stop_requested:
                proc.terminate()
                break
            m = PAGE_PATTERN.match(line)
            if m:
                item.processed_pages = int(m.group(1))

        proc.wait()
        if proc.returncode not in (0, None) and not self.stop_requested:
            raise RuntimeError(f"ocrmypdf zakończył się kodem {proc.returncode}")

    # ── Odświeżanie UI ───────────────────────────────────────────────────────

    def _poll(self):
        """Co 250 ms aktualizuje UI na podstawie stanu queue_items."""
        for row in self.queue_rows:
            row.refresh()

        current = next(
            (i for i in self.queue_items if i.status == FileStatus.PROCESSING), None
        )

        if current:
            self.lbl_cur_name.configure(text=current.path.name)
            self.lbl_cur_pages.configure(
                text=f"Strona {current.processed_pages} / {current.total_pages}"
            )
            self.pb_cur.set(current.progress)
            eta = current.eta_seconds
            self.lbl_eta.configure(
                text=f"Pozostało: ~{format_time(eta)}" if eta is not None else ""
            )
        elif not self.is_running:
            self._on_all_done()
            return

        # Całościowy postęp
        done  = sum(1 for i in self.queue_items if i.status in (FileStatus.DONE, FileStatus.ERROR))
        total = len(self.queue_items)
        self.lbl_overall.configure(text=f"{done} / {total} plików")
        self.pb_all.set(done / total if total else 0)

        self.after(250, self._poll)

    def _on_all_done(self):
        for row in self.queue_rows:
            row.refresh()

        done   = sum(1 for i in self.queue_items if i.status == FileStatus.DONE)
        errors = sum(1 for i in self.queue_items if i.status == FileStatus.ERROR)
        total  = len(self.queue_items)

        self.lbl_overall.configure(text=f"{done} / {total} plików")
        self.pb_all.set(done / total if total else 0)
        self.pb_cur.set(1.0 if not errors else done / total)
        self.lbl_cur_name.configure(text="—")
        self.lbl_cur_pages.configure(text="")
        self.lbl_eta.configure(text="")

        if self.stop_requested:
            self.lbl_result.configure(text="Zatrzymano przez użytkownika.", text_color="#f59e0b")
        elif errors:
            self.lbl_result.configure(
                text=f"Gotowe: {done} OK, {errors} błąd{'ów' if errors > 1 else ''}.",
                text_color="#ef4444"
            )
        else:
            self.lbl_result.configure(
                text=f"Gotowe! Przetworzono {done} plik{'ów' if done != 1 else ''}.",
                text_color="#22c55e"
            )

        self._sync_buttons()

    def _reset_progress_ui(self):
        self.lbl_cur_name.configure(text="—")
        self.lbl_cur_pages.configure(text="")
        self.pb_cur.set(0)
        self.lbl_eta.configure(text="")
        self.lbl_overall.configure(text="0 / 0 plików")
        self.pb_all.set(0)
        self.lbl_result.configure(text="")

    def _sync_buttons(self):
        has_pending = any(i.status == FileStatus.PENDING for i in self.queue_items)
        self.btn_start.configure(
            state="normal" if (has_pending and not self.is_running) else "disabled"
        )
        self.btn_stop.configure(
            state="normal" if self.is_running else "disabled",
            text="■  Stop"
        )


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
