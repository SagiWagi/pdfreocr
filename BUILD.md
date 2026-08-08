# Budowanie pliku EXE — pdfreocr

Instrukcja tworzenia przenośnego pliku `.exe` z `app.py` przy użyciu **PyInstaller**.

---

## Historia wersji

| Wersja | Zmiany |
|---|---|
| **1.0** | Pierwsze wydanie — OCR pojedynczych plików PDF |
| **1.1** | Dodano: skanowanie folderów z podfolderami, wykrywanie PDF bez grafiki rastrowej, kopiowanie plików tekstowych ze znacznikiem |
| **1.2** | Dodano: dialog wykluczania podfolderów przy skanowaniu, globalny folder wyjściowy z zachowaniem struktury drzewa katalogów |

---

## Wymagania wstępne

- Python **3.9 lub nowszy** (wymagane przez `Path.is_relative_to()` użyte w v1.2)
- Zainstalowane zależności projektu:

```bash
pip install -r requirements.txt
```

- PyInstaller:

```bash
pip install pyinstaller
```

---

## Zależności Python (`requirements.txt`)

| Pakiet | Wersja min. | Zastosowanie |
|---|---|---|
| `customtkinter` | 5.2.0 | Interfejs graficzny |
| `pikepdf` | 8.0.0 | Odczyt stron PDF, wykrywanie grafiki rastrowej (v1.1+), zliczanie stron |
| `ocrmypdf` | 17.0.0 | Silnik OCR (wywołanie jako subprocess) |

---

## Szybkie budowanie (jeden plik EXE)

```bash
pyinstaller --onefile --windowed --collect-all customtkinter --collect-all pikepdf --name pdfreocr app.py
```

Parametry:

| Parametr | Opis |
|---|---|
| `--onefile` | Pakuje wszystko w jeden plik `.exe` |
| `--windowed` | Brak okna konsoli (aplikacja GUI) |
| `--collect-all customtkinter` | Dołącza motywy i zasoby customtkinter |
| `--collect-all pikepdf` | Dołącza natywne biblioteki i rozszerzenia C pikepdf |
| `--name pdfreocr` | Nazwa pliku wyjściowego |

Plik `pdfreocr.exe` pojawi się w folderze `dist/`.

---

## Budowanie ze spec-file (zalecane)

Spec-file daje pełną kontrolę nad procesem budowania. Utwórz plik `pdfreocr.spec`:

```python
# pdfreocr.spec
from PyInstaller.utils.hooks import collect_all

block_cipher = None

customtkinter_datas, customtkinter_binaries, customtkinter_hiddenimports = collect_all('customtkinter')
pikepdf_datas, pikepdf_binaries, pikepdf_hiddenimports = collect_all('pikepdf')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=customtkinter_binaries + pikepdf_binaries,
    datas=customtkinter_datas + pikepdf_datas,
    hiddenimports=customtkinter_hiddenimports + pikepdf_hiddenimports + [
        'PIL', 'PIL.Image', 'lxml', 'lxml.etree',
        # v1.2: moduły stdlib używane w ExcludeDialog i operacjach na drzewie folderów
        'tkinter', 'tkinter.filedialog', 'tkinter.messagebox',
        'pathlib', 'shutil', 'threading',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='pdfreocr',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # brak okna konsoli
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

Uruchom budowanie spec-file:

```bash
pyinstaller pdfreocr.spec
```

---

## Dodawanie ikony (opcjonalnie)

1. Przygotuj plik ikony w formacie `.ico` (np. `icon.ico`)
2. Dodaj parametr do komendy lub do spec-file:

```bash
# Komenda:
pyinstaller --onefile --windowed --collect-all customtkinter --collect-all pikepdf --icon=icon.ico --name pdfreocr app.py

# Spec-file — w sekcji EXE:
icon='icon.ico',
```

---

## Ważna uwaga — zależności zewnętrzne

Plik `.exe` zawiera **tylko kod Python**. Na maszynie docelowej muszą być zainstalowane:

| Zależność | Wymagana przez | Gdzie pobrać / zainstalować |
|---|---|---|
| **Tesseract OCR** (`tesseract.exe`) | OCR wszystkich wersji | https://github.com/UB-Mannheim/tesseract/wiki |
| **pol.traineddata** | Model języka polskiego | https://github.com/tesseract-ocr/tessdata |
| **ocrmypdf** | OCR wszystkich wersji | `pip install ocrmypdf` (wymaga Pythona) |

`ocrmypdf` jest paczką Pythona wywoływaną jako subprocess i **nie może być spakowany razem z EXE** — musi być dostępny w PATH systemu lub wirtualnym środowisku.

> **Uwaga v1.2:** Funkcje wykrywania grafiki rastrowej i wykluczania folderów działają **wyłącznie w ramach EXE** (używają tylko `pikepdf` i modułów stdlib). Nie wymagają dodatkowych zależności zewnętrznych.

### Alternatywa dla dystrybucji bez Pythona

Jeśli EXE ma działać na maszynach bez Pythona, zamiast `ocrmypdf` CLI można rozważyć wywołanie `ocrmypdf` jako modułu z bundlowanego Pythona. Jest to zaawansowana konfiguracja wykraczająca poza zakres tej instrukcji.

---

## Struktura katalogów po budowaniu

```
pdfreocr/
├── app.py
├── requirements.txt
├── BUILD.md
├── pdfreocr.spec          # po wygenerowaniu
├── build/                 # pliki tymczasowe (można usunąć)
└── dist/
    └── pdfreocr.exe       # gotowy plik do dystrybucji
```

---

## Typowe problemy

### EXE uruchamia się, ale customtkinter nie wyświetla motywów

Brakuje zasobów customtkinter. Upewnij się, że używasz `--collect-all customtkinter`.

### Błąd "Failed to execute script app"

Uruchom tymczasowo z `--console` zamiast `--windowed`, aby zobaczyć szczegóły błędu w oknie konsoli.

### Duży rozmiar pliku EXE

Normalny rozmiar to 30–80 MB. Można zmniejszyć, wykluczając nieużywane pakiety w spec-file (sekcja `excludes`).

### Pikepdf nie działa w EXE

Upewnij się, że używasz `--collect-all pikepdf`. Pikepdf zawiera skompilowane rozszerzenia C, które muszą być jawnie dołączone.

### Dialog wykluczania folderów nie pojawia się (v1.2)

Objaw: kliknięcie "Dodaj folder" natychmiast dodaje pliki bez dialogu. Przyczyna: folder nie ma podfolderów zawierających PDF-y — to zachowanie poprawne. Dialog pojawia się tylko gdy istnieją podfoldery z PDF-ami.

### Błąd `AttributeError: 'WindowsPath' object has no attribute 'is_relative_to'`

Używana jest wersja Pythona starsza niż 3.9. Metoda `Path.is_relative_to()` została dodana w Pythonie 3.9. Zaktualizuj Pythona do wersji 3.9+.

### Globalny folder wyjściowy — brak struktury podfolderów dla plików dodanych ręcznie

Pliki dodane przez "Dodaj pliki PDF" (nie przez "Dodaj folder") nie mają zdefiniowanego folderu źródłowego, więc trafiają bezpośrednio do folderu wyjściowego bez podkatalogów. Jest to zachowanie celowe. Aby zachować strukturę, używaj "Dodaj folder".
