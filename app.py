#!/usr/bin/env python3
"""
pdfreocr — GUI do ponownego OCR plików PDF z obsługą polskich znaków.
v1.2 — dodano: wykluczanie podfolderów, globalny folder wyjściowy z drzewem.
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
from typing import Callable, List, Optional, Set
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

# ─── Stałe ────────────────────────────────────────────────────────────────────

APP_TITLE    = "pdfreocr — Naprawa warstwy tekstowej PDF"
APP_VERSION  = "1.2.0"
DEFAULT_LANG = "pol"
DEFAULT_TEXT_MARKER = "[tekst]"

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

PAGE_PATTERN   = re.compile(r"^\s+(\d+)\s+page", re.MULTILINE)
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


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


def _obj_has_image(obj) -> bool:
    """Sprawdza rekurencyjnie czy obiekt pikepdf zawiera grafikę rastrową."""
    import pikepdf
    try:
        resources = obj.get("/Resources")
        if resources is None:
            return False
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return False
        for key in xobjects.keys():
            try:
                xobj = xobjects[key]
                subtype = xobj.get("/Subtype")
                if subtype == pikepdf.Name("/Image"):
                    return True
                if subtype == pikepdf.Name("/Form"):
                    if _obj_has_image(xobj):
                        return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def has_raster_images(path: Path) -> bool:
    """Zwraca True jeśli PDF zawiera grafikę rastrową.
    W razie błędu zwraca True (bezpieczniej zakwalifikować do OCR)."""
    try:
        import pikepdf
        with pikepdf.open(path) as pdf:
            for page in pdf.pages:
                if _obj_has_image(page):
                    return True
        return False
    except Exception:
        return True


def collect_pdf_subdirs(root: Path, pdf_paths: List[Path]) -> List[Path]:
    """Zwraca posortowaną listę wszystkich podfolderów (nie licząc root),
    które są przodkami co najmniej jednego pliku PDF."""
    dirs: Set[Path] = set()
    for p in pdf_paths:
        try:
            rel = p.parent.relative_to(root)
        except ValueError:
            continue
        for i in range(1, len(rel.parts) + 1):
            dirs.add(root / Path(*rel.parts[:i]))
    return sorted(dirs)


# ─── Dialog wykluczenia folderów ──────────────────────────────────────────────

class ExcludeDialog(tk.Toplevel):
    """Modal z listą podfolderów; zaznaczone zostaną POMINIĘTE.

    Używa tk.Toplevel (nie CTkToplevel), ponieważ CTkToplevel ma znane problemy
    z grab_set() i wait_window() wynikające z wewnętrznych wątków customtkinter.
    Widżety CTK działają normalnie wewnątrz tk.Toplevel.
    """

    def __init__(self, parent, root: Path, subdirs: List[Path], pdf_paths: List[Path]):
        super().__init__(parent)
        self.configure(bg="#2b2b2b")
        self.title("Wyklucz podfoldery")
        self.geometry("620x480")
        self.resizable(True, True)
        self.transient(parent)

        self._result: Optional[Set[Path]] = None
        self._checkboxes: List[tuple] = []   # [(Path, CTkCheckBox), …]

        self._counts = {
            d: sum(1 for p in pdf_paths if p.is_relative_to(d))
            for d in subdirs
        }
        self._root = root
        self._build(subdirs)

        self.update_idletasks()
        self.grab_set()
        self.lift()
        self.focus_force()

    def _build(self, subdirs: List[Path]):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Nagłówek ──────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        ctk.CTkLabel(
            hdr, text=f"Folder źródłowy:  {self._root}",
            font=("", 12, "bold"), anchor="w"
        ).pack(side="left")

        # ── Pasek narzędziowy ─────────────────────────────────────────────────
        tb = ctk.CTkFrame(self, fg_color="transparent")
        tb.grid(row=1, column=0, sticky="ew", padx=16, pady=(6, 2))
        ctk.CTkLabel(
            tb, text="Zaznaczone foldery zostaną POMINIĘTE przy dodawaniu:",
            font=("", 12), text_color="#9ca3af"
        ).pack(side="left")
        ctk.CTkButton(
            tb, text="Odznacz wszystkie", width=150,
            fg_color="transparent", border_width=1, height=28,
            command=lambda: self._set_all(False)
        ).pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            tb, text="Zaznacz wszystkie", width=150,
            fg_color="transparent", border_width=1, height=28,
            command=lambda: self._set_all(True)
        ).pack(side="right")

        # ── Lista folderów ────────────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(self)
        scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=4)
        scroll.grid_columnconfigure(0, weight=1)

        for i, d in enumerate(subdirs):
            rel   = d.relative_to(self._root)
            depth = len(rel.parts) - 1
            count = self._counts[d]
            indent = "    " * depth
            cb = ctk.CTkCheckBox(
                scroll,
                text=f"{indent}{rel}   ({count} PDF)",
                font=("", 12),
            )
            cb.grid(row=i, column=0, sticky="w", padx=8, pady=2)
            self._checkboxes.append((d, cb))

        # ── Przyciski ─────────────────────────────────────────────────────────
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 12))
        ctk.CTkButton(
            btns, text="OK", width=120, font=("", 13, "bold"),
            command=self._ok
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="Anuluj", width=100,
            fg_color="transparent", border_width=1,
            command=self._cancel
        ).pack(side="left")
        ctk.CTkLabel(
            btns,
            text="Anuluj = rezygnacja z dodawania całego folderu",
            font=("", 11), text_color="#6b7280"
        ).pack(side="right", padx=8)

    def _set_all(self, value: bool):
        for _, cb in self._checkboxes:
            cb.select() if value else cb.deselect()

    def _ok(self):
        self._result = {d for d, cb in self._checkboxes if cb.get()}
        self.grab_release()
        self.destroy()

    def _cancel(self):
        self._result = None
        self.grab_release()
        self.destroy()

    def wait_result(self) -> Optional[Set[Path]]:
        self.wait_window(self)
        return self._result


# ─── Model danych ─────────────────────────────────────────────────────────────

class FileStatus(Enum):
    SCANNING   = "Skanowanie…"
    PENDING    = "Oczekuje"
    PROCESSING = "Przetwarzanie"
    DONE       = "Gotowe"
    SKIPPED    = "Pominięto"
    ERROR      = "Błąd"


@dataclass
class QueueItem:
    path: Path
    output_path: Path
    source_root: Optional[Path] = None   # folder dodany przez "Dodaj folder"
    is_text_only: bool = False
    total_pages: int = 0
    processed_pages: int = 0
    status: FileStatus = FileStatus.SCANNING
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
    FileStatus.SCANNING:   ("◌", "#9ca3af"),
    FileStatus.PENDING:    ("○", "#6b7280"),
    FileStatus.PROCESSING: ("⟳", "#3b82f6"),
    FileStatus.DONE:       ("✓", "#22c55e"),
    FileStatus.SKIPPED:    ("⊘", "#f59e0b"),
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
        self.lbl_icon = ctk.CTkLabel(self, text="◌", width=26, font=("", 16))
        self.lbl_icon.grid(row=0, column=0, padx=(4, 4), pady=6)

        self.lbl_name = ctk.CTkLabel(
            self, text=self.item.path.name, anchor="w", font=("", 13)
        )
        self.lbl_name.grid(row=0, column=1, sticky="w", padx=(0, 8))

        self.pb = ctk.CTkProgressBar(self, width=160, height=8)
        self.pb.set(0)
        self.pb.grid(row=0, column=2, sticky="ew", padx=8)

        self.lbl_info = ctk.CTkLabel(
            self, text="Skanowanie…", width=140, anchor="e",
            font=("", 12), text_color="#9ca3af"
        )
        self.lbl_info.grid(row=0, column=3, padx=8)

        self.btn_rm = ctk.CTkButton(
            self, text="✕", width=28, height=28,
            fg_color="transparent", hover_color="#374151",
            command=lambda: self.on_remove(self.item)
        )
        self.btn_rm.grid(row=0, column=4, padx=(0, 4))

        # Podwójne kliknięcie → otwórz plik w domyślnej aplikacji
        for w in (self, self.lbl_icon, self.lbl_name, self.lbl_info):
            w.bind("<Double-Button-1>", self._open_file)

    def _open_file(self, event=None):
        """Otwiera plik wyjściowy (jeśli gotowy) lub wejściowy w domyślnej aplikacji."""
        if self.item.status in (FileStatus.DONE, FileStatus.SKIPPED):
            path = self.item.output_path
        else:
            path = self.item.path
        if path and path.exists():
            os.startfile(str(path))

    def refresh(self):
        item = self.item
        icon, color = STATUS_ICONS[item.status]
        self.lbl_icon.configure(text=icon, text_color=color)

        if item.status == FileStatus.SCANNING:
            self.lbl_name.configure(text=item.path.name)
            self.lbl_info.configure(text="Skanowanie…", text_color="#9ca3af")

        elif item.status == FileStatus.PENDING:
            if item.is_text_only:
                self.lbl_name.configure(text=item.path.name + "  [TEKST]")
                self.lbl_info.configure(text="tylko tekst", text_color="#f59e0b")
            else:
                self.lbl_name.configure(text=item.path.name)
                pages = f"{item.total_pages} str." if item.total_pages else "… str."
                self.lbl_info.configure(text=pages, text_color="#9ca3af")

        elif item.status == FileStatus.PROCESSING:
            if item.is_text_only:
                self.lbl_info.configure(text="Kopiowanie…", text_color=color)
            else:
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

        elif item.status == FileStatus.SKIPPED:
            self.lbl_info.configure(text="Skopiowano ⊘", text_color=color)

        elif item.status == FileStatus.ERROR:
            self.pb.set(0)
            self.lbl_info.configure(text="Błąd", text_color=color)

        self.btn_rm.configure(
            state="disabled" if item.status in (
                FileStatus.PROCESSING, FileStatus.SCANNING
            ) else "normal"
        )


# ─── Główna aplikacja ─────────────────────────────────────────────────────────

class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.geometry("960x740")
        self.minsize(780, 600)

        self.queue_items: List[QueueItem] = []
        self.queue_rows:  List[QueueRow]  = []
        self.is_running     = False
        self.stop_requested = False
        self._scan_lock     = threading.Lock()
        self._scan_thread: Optional[threading.Thread] = None
        self._spinner_idx   = 0
        self._spinner_id: Optional[str] = None

        self.tesseract_path = find_tesseract()
        self.tessdata_path  = find_tessdata()
        self.ocrmypdf_path  = find_ocrmypdf()

        self._build_ui()
        self._sync_buttons()

        if not self.tesseract_path or not self.tessdata_path:
            self.after(200, self._warn_missing_deps)

    # ── Diagnostyka ──────────────────────────────────────────────────────────

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

    # ── Budowa UI ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))

        ctk.CTkButton(
            bar, text="＋  Dodaj pliki PDF",
            command=self._add_files, width=160, height=36
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            bar, text="＋  Dodaj folder",
            command=self._add_folder, width=140, height=36,
            fg_color="#1d4ed8", hover_color="#1e40af"
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            bar, text="Wyczyść kolejkę",
            command=self._clear_queue,
            fg_color="transparent", border_width=1,
            width=130, height=36
        ).pack(side="left")

        ctk.CTkLabel(bar, text="Język OCR:", font=("", 13)).pack(
            side="right", padx=(8, 4)
        )
        self.lang_var = ctk.StringVar(value=DEFAULT_LANG)
        ctk.CTkOptionMenu(
            bar, values=["pol", "eng", "pol+eng"],
            variable=self.lang_var, width=110
        ).pack(side="right")

        # ── Panel ustawień ────────────────────────────────────────────────────
        settings = ctk.CTkFrame(self)
        settings.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        settings.grid_columnconfigure(3, weight=1)
        settings.grid_columnconfigure(6, weight=2)

        # Wiersz 0: znacznik + folder dla plików tekstowych
        ctk.CTkLabel(
            settings, text="Znacznik pliku tekstowego:", font=("", 12)
        ).grid(row=0, column=0, padx=(12, 4), pady=(8, 4), sticky="w")

        self.text_marker_var = ctk.StringVar(value=DEFAULT_TEXT_MARKER)
        ctk.CTkEntry(
            settings, textvariable=self.text_marker_var, width=100, font=("", 12)
        ).grid(row=0, column=1, padx=(0, 12), pady=(8, 4))

        ctk.CTkLabel(
            settings, text="Folder dla plików tekstowych:", font=("", 12)
        ).grid(row=0, column=2, padx=(0, 4), pady=(8, 4), sticky="w")

        self.text_output_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            settings, textvariable=self.text_output_var, font=("", 12),
            placeholder_text="(obok oryginału)"
        ).grid(row=0, column=3, sticky="ew", padx=(0, 4), pady=(8, 4))

        ctk.CTkButton(
            settings, text="Przeglądaj…", width=100,
            command=self._browse_text_folder
        ).grid(row=0, column=4, padx=(0, 12), pady=(8, 4))

        # Wiersz 1: globalny folder wyjściowy z drzewem
        ctk.CTkLabel(
            settings, text="Folder wyjściowy (drzewo):", font=("", 12)
        ).grid(row=1, column=0, padx=(12, 4), pady=(4, 8), sticky="w")

        self.output_root_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            settings, textvariable=self.output_root_var, font=("", 12),
            placeholder_text="(jeśli ustawiony — wszystkie wyniki trafiają tu ze strukturą podfolderów)"
        ).grid(row=1, column=1, columnspan=3, sticky="ew", padx=(0, 4), pady=(4, 8))

        ctk.CTkButton(
            settings, text="Przeglądaj…", width=100,
            command=self._browse_output_root
        ).grid(row=1, column=4, padx=(0, 12), pady=(4, 8))

        # ── Lista plików ──────────────────────────────────────────────────────
        list_outer = ctk.CTkFrame(self)
        list_outer.grid(row=2, column=0, sticky="nsew", padx=16, pady=4)
        list_outer.grid_columnconfigure(0, weight=1)
        list_outer.grid_rowconfigure(0, weight=1)

        self.scroll = ctk.CTkScrollableFrame(
            list_outer,
            label_text="Kolejka plików",
            label_font=("", 13, "bold"),
        )
        self.scroll.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        self.scroll.grid_columnconfigure(0, weight=1)

        # Przyciski przewijania listy
        scroll_btns = ctk.CTkFrame(list_outer, fg_color="transparent", width=36)
        scroll_btns.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)
        ctk.CTkButton(
            scroll_btns, text="▲", width=32, height=32,
            fg_color="transparent", border_width=1,
            command=self._scroll_up
        ).pack(side="top", pady=(6, 2))
        ctk.CTkButton(
            scroll_btns, text="▼", width=32, height=32,
            fg_color="transparent", border_width=1,
            command=self._scroll_down
        ).pack(side="bottom", pady=(2, 6))

        self.empty_lbl = ctk.CTkLabel(
            self.scroll,
            text='Kliknij "＋ Dodaj pliki PDF" lub "＋ Dodaj folder"',
            text_color="#6b7280", font=("", 13)
        )
        self.empty_lbl.grid(row=0, column=0, pady=48)

        # Scroll myszką: aktywny gdy kursor nad listą
        self.scroll.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._on_mousewheel))
        self.scroll.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

        # ── Panel postępu ─────────────────────────────────────────────────────
        prog = ctk.CTkFrame(self)
        prog.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 2))
        prog.grid_columnconfigure(2, weight=1)

        # Spinner (animacja) + "Aktualny plik:"
        self.lbl_spinner = ctk.CTkLabel(prog, text="", width=22, font=("", 15))
        self.lbl_spinner.grid(row=0, column=0, padx=(12, 0), pady=(10, 2))
        ctk.CTkLabel(prog, text="Aktualny plik:", font=("", 12, "bold")).grid(
            row=0, column=1, sticky="w", padx=(4, 4), pady=(10, 2))
        self.lbl_cur_name = ctk.CTkLabel(prog, text="—", font=("", 12), anchor="w")
        self.lbl_cur_name.grid(row=0, column=2, sticky="w", padx=4)
        self.lbl_eta = ctk.CTkLabel(prog, text="", font=("", 11), text_color="#9ca3af")
        self.lbl_eta.grid(row=0, column=3, sticky="e", padx=14)

        self.pb_cur = ctk.CTkProgressBar(prog, height=14)
        self.pb_cur.set(0)
        self.pb_cur.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 4))

        self.lbl_cur_pages = ctk.CTkLabel(
            prog, text="", font=("", 12), text_color="#9ca3af"
        )
        self.lbl_cur_pages.grid(row=2, column=0, columnspan=3, sticky="w", padx=14)

        ctk.CTkLabel(prog, text="Całość:", font=("", 12, "bold")).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(6, 2))
        self.lbl_overall = ctk.CTkLabel(prog, text="0 / 0 plików", font=("", 12))
        self.lbl_overall.grid(row=3, column=2, sticky="w", padx=4)

        self.pb_all = ctk.CTkProgressBar(prog, height=10)
        self.pb_all.set(0)
        self.pb_all.grid(row=4, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 10))

        # ── Przyciski Start / Stop ────────────────────────────────────────────
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 14))

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

    # ── Przeglądanie folderów ─────────────────────────────────────────────────

    def _browse_text_folder(self):
        folder = filedialog.askdirectory(
            title="Folder docelowy dla plików PDF z samym tekstem"
        )
        if folder:
            self.text_output_var.set(folder)

    def _browse_output_root(self):
        folder = filedialog.askdirectory(
            title="Globalny folder wyjściowy (zachowa strukturę podfolderów)"
        )
        if folder:
            self.output_root_var.set(folder)

    # ── Scroll listy ─────────────────────────────────────────────────────────

    def _on_mousewheel(self, event):
        self.scroll._parent_canvas.yview_scroll(-int(event.delta / 120), "units")

    def _scroll_up(self):
        self.scroll._parent_canvas.yview_scroll(-4, "units")

    def _scroll_down(self):
        self.scroll._parent_canvas.yview_scroll(4, "units")

    # ── Animacja spinnera ─────────────────────────────────────────────────────

    def _start_spinner(self):
        self._spinner_idx = 0
        self._tick_spinner()

    def _tick_spinner(self):
        self.lbl_spinner.configure(
            text=SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        )
        self._spinner_idx += 1
        self._spinner_id = self.after(100, self._tick_spinner)

    def _stop_spinner(self):
        if self._spinner_id:
            self.after_cancel(self._spinner_id)
            self._spinner_id = None
        self.lbl_spinner.configure(text="")

    # ── Zarządzanie kolejką ───────────────────────────────────────────────────

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Wybierz pliki PDF",
            filetypes=[("Pliki PDF", "*.pdf"), ("Wszystkie pliki", "*.*")]
        )
        if paths:
            self._enqueue([Path(p) for p in paths], source_root=None)

    def _add_folder(self):
        folder = filedialog.askdirectory(
            title="Wybierz folder z plikami PDF (przeszuka podfoldery)"
        )
        if not folder:
            return

        root = Path(folder)
        all_pdfs = sorted(root.rglob("*.pdf"))

        if not all_pdfs:
            messagebox.showinfo(
                "Brak plików PDF",
                "Nie znaleziono żadnych plików PDF w wybranym folderze ani jego podfolderach."
            )
            return

        # Dialog wykluczeń — tylko jeśli są podfoldery z PDF-ami
        subdirs = collect_pdf_subdirs(root, all_pdfs)
        excluded: Set[Path] = set()

        if subdirs:
            dlg = ExcludeDialog(self, root, subdirs, all_pdfs)
            result = dlg.wait_result()
            if result is None:   # użytkownik kliknął Anuluj
                return
            excluded = result

        # Filtruj PDF-y wykluczone przez wskazane foldery
        if excluded:
            paths = [
                p for p in all_pdfs
                if not any(p.is_relative_to(excl) for excl in excluded)
            ]
        else:
            paths = all_pdfs

        if not paths:
            messagebox.showinfo(
                "Brak plików",
                "Po wykluczeniu wybranych folderów nie pozostały żadne pliki PDF."
            )
            return

        self._enqueue(paths, source_root=root)

    def _enqueue(self, paths: List[Path], source_root: Optional[Path]):
        existing = {item.path for item in self.queue_items}
        added = False
        for path in paths:
            if path in existing or path.suffix.lower() != ".pdf":
                continue
            out = path.parent / (path.stem + "_ocr" + path.suffix)
            item = QueueItem(path=path, output_path=out, source_root=source_root)
            self.queue_items.append(item)
            self._add_row(item)
            existing.add(path)
            added = True

        if self.queue_items:
            self.empty_lbl.grid_remove()
        if added:
            self._start_scanner()
        self._sync_buttons()

    def _add_row(self, item: QueueItem):
        row = QueueRow(self.scroll, item, on_remove=self._remove_item)
        row.grid(row=len(self.queue_rows), column=0, sticky="ew", padx=4, pady=2)
        self.queue_rows.append(row)

    def _remove_item(self, item: QueueItem):
        if item.status in (FileStatus.PROCESSING, FileStatus.SCANNING):
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

    # ── Wątek skanowania ──────────────────────────────────────────────────────

    def _start_scanner(self):
        with self._scan_lock:
            if self._scan_thread and self._scan_thread.is_alive():
                return
            self._scan_thread = threading.Thread(
                target=self._scanner_worker, daemon=True
            )
            self._scan_thread.start()

    def _scanner_worker(self):
        """Sprawdza kolejno każdy plik SCANNING pod kątem grafiki rastrowej."""
        while True:
            item = next(
                (i for i in self.queue_items if i.status == FileStatus.SCANNING),
                None
            )
            if item is None:
                break
            item.total_pages  = get_pdf_pages(item.path)
            item.is_text_only = not has_raster_images(item.path)
            item.status       = FileStatus.PENDING

        self.after(0, self._sync_buttons)

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _start(self):
        scanning = [i for i in self.queue_items if i.status == FileStatus.SCANNING]
        if scanning:
            messagebox.showinfo(
                "Skanowanie w toku",
                f"Trwa skanowanie plików (pozostało: {len(scanning)}).\n"
                "Poczekaj chwilę i spróbuj ponownie."
            )
            return

        pending = [i for i in self.queue_items if i.status == FileStatus.PENDING]
        if not pending:
            messagebox.showinfo("Brak zadań", "Dodaj pliki PDF do kolejki.")
            return

        # Walidacja globalnego folderu wyjściowego
        out_root_str = self.output_root_var.get().strip()
        if out_root_str:
            try:
                Path(out_root_str).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Błąd folderu wyjściowego",
                                     f"Nie można utworzyć folderu wyjściowego:\n{e}")
                return

        # Walidacja folderu dla plików tekstowych (tylko gdy nie ma globalnego)
        text_folder_str = self.text_output_var.get().strip()
        if text_folder_str and not out_root_str:
            try:
                Path(text_folder_str).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Błąd folderu dla plików tekstowych",
                                     f"Nie można utworzyć folderu docelowego:\n{e}")
                return

        # Weryfikacja OCR tylko gdy są pliki z grafiką
        if any(not i.is_text_only for i in pending):
            if not self.tesseract_path or not self.tessdata_path:
                self._warn_missing_deps()
                return
            if not self.ocrmypdf_path:
                messagebox.showerror(
                    "Brak ocrmypdf",
                    "ocrmypdf nie jest zainstalowany.\nUruchom: pip install ocrmypdf"
                )
                return

        self.is_running = True
        self.stop_requested = False
        self.lbl_result.configure(text="")
        self._sync_buttons()

        threading.Thread(target=self._worker, daemon=True).start()
        self._start_spinner()
        self._poll()

    def _stop(self):
        self.stop_requested = True
        self.btn_stop.configure(state="disabled", text="Zatrzymywanie…")

    # ── Wątek roboczy ─────────────────────────────────────────────────────────

    def _worker(self):
        env = os.environ.copy()
        if self.tesseract_path:
            env["PATH"] = self.tesseract_path + os.pathsep + env.get("PATH", "")
        if self.tessdata_path:
            env["TESSDATA_PREFIX"] = self.tessdata_path

        marker       = self.text_marker_var.get().strip() or DEFAULT_TEXT_MARKER
        text_folder  = Path(s) if (s := self.text_output_var.get().strip()) else None
        out_root     = Path(s) if (s := self.output_root_var.get().strip()) else None

        for item in self.queue_items:
            if self.stop_requested:
                break
            if item.status != FileStatus.PENDING:
                continue

            item.status = FileStatus.PROCESSING
            item.start_time = time.time()
            item.processed_pages = 0

            try:
                if item.is_text_only:
                    self._process_text_only(item, marker, text_folder, out_root)
                    item.status = FileStatus.SKIPPED
                else:
                    self._process_ocr(item, marker, out_root, env)
                    if not self.stop_requested:
                        item.status = FileStatus.DONE
                        item.processed_pages = item.total_pages
            except Exception as exc:
                item.status = FileStatus.ERROR
                item.error_msg = str(exc)
            finally:
                item.end_time = time.time()

        self.is_running = False

    def _build_tree_dest(self, item: QueueItem, out_root: Path,
                         marker: str, is_text: bool) -> Path:
        """Ścieżka wyjściowa zachowująca strukturę drzewa względem source_root."""
        if item.source_root:
            try:
                rel = item.path.relative_to(item.source_root)
            except ValueError:
                rel = Path(item.path.name)
        else:
            rel = Path(item.path.name)

        suffix = marker if is_text else "_ocr"
        new_name = rel.stem + suffix + rel.suffix
        return out_root / rel.parent / new_name

    def _process_text_only(self, item: QueueItem, marker: str,
                           text_folder: Optional[Path], out_root: Optional[Path]):
        """Kopiuje plik tekstowy ze znacznikiem — do out_root (drzewo) lub text_folder."""
        if out_root:
            dest = self._build_tree_dest(item, out_root, marker, is_text=True)
        elif text_folder:
            dest = text_folder / (item.path.stem + marker + item.path.suffix)
        else:
            dest = item.path.parent / (item.path.stem + marker + item.path.suffix)

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.path, dest)
        item.output_path = dest

    def _process_ocr(self, item: QueueItem, marker: str,
                     out_root: Optional[Path], env: dict):
        """Uruchamia OCR; jeśli out_root ustawiony, zmienia output_path na drzewo."""
        if out_root:
            item.output_path = self._build_tree_dest(item, out_root, marker, is_text=False)
            item.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._run_ocr(item, env)

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

    # ── Odświeżanie UI ────────────────────────────────────────────────────────

    def _poll(self):
        """Co 250 ms aktualizuje UI na podstawie stanu queue_items."""
        for row in self.queue_rows:
            row.refresh()

        current = next(
            (i for i in self.queue_items if i.status == FileStatus.PROCESSING), None
        )

        if current:
            self.lbl_cur_name.configure(text=current.path.name)
            elapsed_txt = format_time(current.elapsed)
            if current.is_text_only:
                self.lbl_cur_pages.configure(text="Kopiowanie pliku tekstowego…")
                self.pb_cur.set(0.5)
                self.lbl_eta.configure(text=f"upłynęło {elapsed_txt}")
            else:
                self.lbl_cur_pages.configure(
                    text=f"Strona {current.processed_pages} / {current.total_pages}"
                )
                self.pb_cur.set(current.progress)
                eta = current.eta_seconds
                if eta is not None:
                    time_txt = f"upłynęło {elapsed_txt}  |  pozostało ~{format_time(eta)}"
                else:
                    time_txt = f"upłynęło {elapsed_txt}"
                self.lbl_eta.configure(text=time_txt)
        elif not self.is_running:
            self._on_all_done()
            return

        done  = sum(1 for i in self.queue_items if i.status in (
            FileStatus.DONE, FileStatus.ERROR, FileStatus.SKIPPED
        ))
        total = len(self.queue_items)
        self.lbl_overall.configure(text=f"{done} / {total} plików")
        self.pb_all.set(done / total if total else 0)

        self.after(250, self._poll)

    def _on_all_done(self):
        for row in self.queue_rows:
            row.refresh()

        done    = sum(1 for i in self.queue_items if i.status == FileStatus.DONE)
        skipped = sum(1 for i in self.queue_items if i.status == FileStatus.SKIPPED)
        errors  = sum(1 for i in self.queue_items if i.status == FileStatus.ERROR)
        total   = len(self.queue_items)
        finished = done + skipped

        self.lbl_overall.configure(text=f"{finished} / {total} plików")
        self.pb_all.set(finished / total if total else 0)
        self._stop_spinner()
        self.pb_cur.set(1.0 if not errors else finished / total)
        self.lbl_cur_name.configure(text="—")
        self.lbl_cur_pages.configure(text="")
        self.lbl_eta.configure(text="")

        if self.stop_requested:
            self.lbl_result.configure(
                text="Zatrzymano przez użytkownika.", text_color="#f59e0b"
            )
        else:
            parts = []
            if done:
                parts.append(f"{done} plik{'ów' if done != 1 else ''} OCR")
            if skipped:
                parts.append(f"{skipped} tekstowych skopiowano")
            if errors:
                parts.append(f"{errors} błąd{'ów' if errors > 1 else ''}")
            color = "#ef4444" if errors else "#22c55e"
            self.lbl_result.configure(
                text="Gotowe: " + ", ".join(parts) + ".", text_color=color
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
