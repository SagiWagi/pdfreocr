# pdfreocr

GUI do naprawiania warstwy tekstowej w skanach PDF — zastępuje uszkodzony OCR
(np. z aplikacji Genius Scan, gdzie polskie znaki diakrytyczne są tracone)
nowym, wygenerowanym przez Tesseract z modelem językowym `pol`.

Oryginalne obrazy stron pozostają nienaruszone. Wynikowy plik jest przeszukiwalnym
PDF-em z poprawną warstwą tekstową — możesz zaznaczać, kopiować i wyszukiwać tekst,
a każdy cytat możesz zweryfikować wzrokiem na obrazie strony.

## Dlaczego polskie znaki są tracone?

Aplikacje mobilne jak Genius Scan zapisują tekst OCR w kodowaniu Latin-1 (ISO-8859-1),
które nie zawiera polskich znaków diakrytycznych (ł, ź, ę, ą, ś, ć, ń, ż).
Znaki te są zastępowane przez `?`. pdfreocr usuwa tę warstwę i tworzy nową
z użyciem Tesseract + polskiego modelu językowego.

## Wymagania

### Python
```
pip install -r requirements.txt
```

### Tesseract OCR
Pobierz instalator dla Windows: https://github.com/UB-Mannheim/tesseract/wiki

### Model języka polskiego
Pobierz `pol.traineddata` z https://github.com/tesseract-ocr/tessdata
i umieść w folderze `tessdata` instalacji Tesseract.

Lub użyj własnego folderu i ustaw zmienną środowiskową:
```
set TESSDATA_PREFIX=C:\Users\<nazwa>\tessdata
```

## Uruchomienie

```
python app.py
```

## Użycie

1. Kliknij **＋ Dodaj pliki PDF** lub przeciągnij pliki do okna
2. Wybierz język OCR (domyślnie `pol`)
3. Kliknij **▶ Start**
4. Wynikowe pliki mają suffix `_ocr.pdf` i są zapisywane obok oryginałów

## Funkcje

- Kolejka wielu plików
- Pasek postępu per plik (strona po stronie)
- Szacowany czas do końca operacji
- Całościowy pasek postępu
- Przycisk Stop (bezpieczne zatrzymanie po bieżącej stronie)
- Automatyczne wykrywanie instalacji Tesseract

## Technologie

- [customtkinter](https://github.com/TomSchimansky/CustomTkinter) — nowoczesny GUI
- [ocrmypdf](https://github.com/ocrmypdf/OCRmyPDF) — silnik re-OCR
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) — rozpoznawanie tekstu
- [pikepdf](https://github.com/pikepdf/pikepdf) — odczyt liczby stron PDF
