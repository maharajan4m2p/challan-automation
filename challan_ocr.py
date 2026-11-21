"""Field-specific OCR for Adishtam Ventures Line Issue Note images.

This module deliberately reads only the required form cells.  Tesseract is not
reliable for every handwriting style, so uncertain values are returned blank
rather than filling the workbook with unrelated OCR guesses.
"""
from __future__ import annotations

import io
import re
import shutil
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps

COLUMNS = ["Challan No", "Date", "Item No", "Style", "Item Code", "Color", "Size", "Qty"]

# Normalized against the correctly oriented 1600 x 900 landscape photo.
# Header cells are individually cropped; no page-wide OCR is performed.
HEADER_ROIS = {
    "Challan No": (0.725, 0.060, 0.910, 0.150),
    "Date": (0.270, 0.150, 0.625, 0.225),
    "Item No": (0.270, 0.195, 0.635, 0.265),
    "Style": (0.270, 0.260, 0.625, 0.345),
}

# The number written in the Color column's FIRST data row is the Item Code.
# The actual colour name is handwritten in the row immediately underneath.
# Keep these y-ranges separate; the previous project overlapped these rows.
GROUP_ROIS = [
    {"code": (0.135, 0.535, 0.290, 0.582), "color": (0.135, 0.580, 0.290, 0.635),
     "size": (0.280, 0.535, 0.345, 0.582), "qty": (0.330, 0.535, 0.395, 0.582)},
    {"code": (0.380, 0.535, 0.490, 0.582), "color": (0.380, 0.580, 0.490, 0.635),
     "size": (0.480, 0.535, 0.545, 0.582), "qty": (0.530, 0.535, 0.595, 0.582)},
    {"code": (0.585, 0.535, 0.700, 0.582), "color": (0.585, 0.580, 0.700, 0.635),
     "size": (0.690, 0.535, 0.755, 0.582), "qty": (0.740, 0.535, 0.805, 0.582)},
]

COLOR_WORDS = [
    "BLACK", "NAVY", "GREY MEL", "GREY", "GRAY", "WHITE", "RED", "BLUE",
    "GREEN", "MAROON", "YELLOW", "PINK", "BROWN", "BEIGE", "PURPLE",
    "ORANGE", "OLIVE", "CHARCOAL", "OFF WHITE", "CREAM", "WINE", "TEAL",
]
SIZE_WORDS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL", "4XL"]


def find_tesseract() -> str | None:
    path = shutil.which("tesseract")
    if path:
        return path
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def orient_image(pil_image: Image.Image) -> Image.Image:
    """Orient the portrait WhatsApp photo into the form's landscape direction."""
    image = ImageOps.exif_transpose(pil_image).convert("RGB")
    if image.height > image.width:
        image = image.rotate(90, expand=True)
    return image


def _crop(image: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    width, height = image.size
    x1, y1, x2, y2 = box
    # Small safety inset avoids the printed cell border without cutting the writing.
    px = max(1, int(width * 0.002))
    py = max(1, int(height * 0.003))
    left = max(0, int(x1 * width) + px)
    top = max(0, int(y1 * height) + py)
    right = min(width, int(x2 * width) - px)
    bottom = min(height, int(y2 * height) - py)
    return image.crop((left, top, right, bottom))


def _preprocess(crop: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    """Create two restrained OCR variants that preserve pencil/pen strokes."""
    rgb = np.asarray(crop.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Scale small cells up while retaining the original aspect ratio.
    scale = max(3.5, min(6.0, 260.0 / max(1, gray.shape[0])))
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    # Mild sharpening helps faint graphite, without hard thresholding away strokes.
    blurred = cv2.GaussianBlur(gray, (0, 0), 1.0)
    sharp = cv2.addWeighted(gray, 1.45, blurred, -0.45, 0)
    otsu = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return sharp, otsu


def _ocr_candidates(crop: Image.Image, field: str) -> list[str]:
    sharp, otsu = _preprocess(crop)
    if field in {"Challan No", "Item No", "Item Code", "Qty"}:
        whitelist = "0123456789"
        configs = [
            f"--oem 1 --psm 7 -c tessedit_char_whitelist={whitelist} -c classify_bln_numeric_mode=1",
            f"--oem 1 --psm 6 -c tessedit_char_whitelist={whitelist} -c classify_bln_numeric_mode=1",
        ]
    elif field == "Date":
        whitelist = "0123456789/.- "
        configs = [
            f"--oem 1 --psm 7 -c tessedit_char_whitelist={whitelist}",
            f"--oem 1 --psm 6 -c tessedit_char_whitelist={whitelist}",
        ]
    else:
        configs = ["--oem 1 --psm 7", "--oem 1 --psm 6"]

    candidates: list[str] = []
    for image, config in ((sharp, configs[0]), (otsu, configs[1])):
        try:
            value = pytesseract.image_to_string(image, config=config)
        except pytesseract.TesseractError:
            value = ""
        value = re.sub(r"\s+", " ", value).strip(" \n\r|:;_.,")
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _choose_candidate(candidates: list[str], field: str) -> str:
    if not candidates:
        return ""
    if field in {"Challan No", "Item No", "Item Code", "Qty"}:
        # Never choose between conflicting numeric readings. A short, partial
        # handwritten number is more dangerous than a blank cell.
        min_len = 5 if field == "Challan No" else (4 if field in {"Item No", "Item Code"} else 1)
        max_len = 6 if field in {"Challan No", "Item No", "Item Code"} else 4
        sequences = []
        for candidate in candidates:
            seq = _numbers(candidate, min_len, max_len)
            if seq:
                sequences.append((seq, candidate))
        if not sequences:
            return ""
        if field == "Challan No":
            all_numeric_tokens = [
                token for candidate in candidates
                for token in re.findall(r"\d{4,6}", candidate)
            ]
            valid_tokens = [token for token in all_numeric_tokens if len(token) >= 5]
            # If one variant sees only the last four digits while another adds
            # a leading digit, the leading digit is ambiguous: leave it blank.
            if any(len(token) == 4 and any(value.endswith(token) for value in valid_tokens)
                   for token in all_numeric_tokens):
                return ""
        unique = {tuple(seq) for seq, _ in sequences}
        if len(unique) != 1:
            return ""
        # Pick the clearest variant among equivalent digit readings.
        return max((candidate for _, candidate in sequences), key=len)
    if field == "Date":
        date_candidates = []
        for candidate in candidates:
            digits = re.sub(r"\D", "", candidate)
            if len(digits) in (6, 8):
                date_candidates.append((digits, candidate))
        if not date_candidates:
            return ""
        unique = {digits for digits, _ in date_candidates}
        if len(unique) != 1:
            return ""
        return date_candidates[0][1]
    return max(candidates, key=lambda s: (sum(ch.isalpha() for ch in s), len(s)))

def _numbers(value: str, min_len: int = 3, max_len: int = 5) -> list[str]:
    digits = re.sub(r"[^0-9]", " ", value)
    return [token for token in digits.split() if min_len <= len(token) <= max_len]


def _clean(field: str, raw: str) -> str:
    raw = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not raw:
        return ""
    if field == "Challan No":
        nums = _numbers(raw, 5, 6)
        return nums[0] if nums else ""
    if field in {"Item No", "Item Code"}:
        nums = _numbers(raw, 4, 6)
        return nums[0] if nums else ""
    if field == "Qty":
        nums = _numbers(raw, 1, 4)
        return nums[0] if nums else ""
    if field == "Date":
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 8:
            day, month, year = digits[:2], digits[2:4], digits[4:8]
        elif len(digits) == 6:
            day, month, year = digits[:2], digits[2:4], "20" + digits[4:6]
        else:
            return ""
        try:
            from datetime import datetime
            date_value = datetime(int(year), int(month), int(day))
        except ValueError:
            return ""
        return date_value.strftime("%d/%m/%Y")
    value = re.sub(r"[^A-Za-z0-9 -]", " ", raw).upper()
    value = re.sub(r"\s+", " ", value).strip()
    if field == "Size":
        tokens = value.split()
        for token in tokens:
            match = max(SIZE_WORDS, key=lambda candidate: SequenceMatcher(None, token, candidate).ratio())
            if SequenceMatcher(None, token, match).ratio() >= 0.62:
                return match
        return ""
    if field == "Color":
        # First try a known garment-colour phrase; otherwise keep a readable OCR value
        # so that the user can correct it in the review grid.
        compact = value.replace(" ", "")
        best = max(COLOR_WORDS, key=lambda candidate: SequenceMatcher(None, compact, candidate.replace(" ", "")).ratio())
        score = SequenceMatcher(None, compact, best.replace(" ", "")).ratio()
        if score >= 0.62:
            return best
        return ""
    if field == "Style":
        return value
    return value


@lru_cache(maxsize=1)
def _load_trocr():
    """Load the optional local handwritten-text model once per app process."""
    try:
        import torch
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    except ImportError as exc:
        raise RuntimeError(
            "Local Handwriting OCR needs the optional packages. Run: "
            "pip install -r requirements-handwriting.txt"
        ) from exc

    model_name = "microsoft/trocr-small-handwritten"
    try:
        processor = TrOCRProcessor.from_pretrained(model_name)
        model = VisionEncoderDecoderModel.from_pretrained(model_name)
    except Exception as exc:
        raise RuntimeError(
            "Could not load the TrOCR handwriting model. Check your internet connection "
            "for the first download, then retry. The model is cached locally after download."
        ) from exc

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    return processor, model, device, torch


def _trocr_text(crop: Image.Image) -> str:
    """Recognize one cropped handwritten line with Microsoft's small TrOCR model."""
    processor, model, device, torch = _load_trocr()
    # Add a clean margin so the recognition model sees the handwritten line,
    # not the ruled borders immediately touching the cell.
    prepared = ImageOps.expand(crop.convert("RGB"), border=16, fill="white")
    pixel_values = processor(images=prepared, return_tensors="pt").pixel_values.to(device)
    with torch.inference_mode():
        generated = model.generate(pixel_values, max_new_tokens=48, num_beams=2)
    return processor.batch_decode(generated, skip_special_tokens=True)[0].strip()

def _read(image: Image.Image, box: tuple[float, float, float, float], field: str,
         engine: str = "tesseract") -> str:
    crop = _crop(image, box)
    if engine == "trocr":
        raw = _trocr_text(crop)
    else:
        raw = _choose_candidate(_ocr_candidates(crop, field), field)
    return _clean(field, raw)


def local_extract_one(uploaded_file: Any, engine: str = "tesseract") -> tuple[list[dict[str, str]], list[str]]:
    """Extract only the required header and three line-item groups."""
    if engine == "tesseract":
        tesseract_path = find_tesseract()
        if not tesseract_path:
            raise RuntimeError("Tesseract is not installed. Follow the Windows Tesseract setup in README.md.")
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
    elif engine != "trocr":
        raise ValueError(f"Unknown local OCR engine: {engine}")

    if hasattr(uploaded_file, "getvalue"):
        content = uploaded_file.getvalue()
    elif isinstance(uploaded_file, (bytes, bytearray)):
        content = bytes(uploaded_file)
    else:
        content = uploaded_file.read()
    image = orient_image(Image.open(io.BytesIO(content)))

    if engine == "trocr":
        header_raw = {field: _trocr_text(_crop(image, roi)) for field, roi in HEADER_ROIS.items()}
    else:
        header_raw = {field: _choose_candidate(_ocr_candidates(_crop(image, roi), field), field)
                      for field, roi in HEADER_ROIS.items()}
    header = {field: _clean(field, value) for field, value in header_raw.items() if field != "Item No"}
    header_items = _numbers(header_raw.get("Item No", ""), 4, 6)
    header["Item No"] = header_items[0] if header_items else ""

    records: list[dict[str, str]] = []
    for index, roi in enumerate(GROUP_ROIS):
        code = _read(image, roi["code"], "Item Code", engine=engine)
        # The Item No field at the top can list one number per repeated line-item group.
        # Use that ordered list as a mapping fallback if the group-local number is unclear.
        if not code and index < len(header_items):
            code = header_items[index]
        elif not code and len(header_items) == 1:
            code = header_items[0]

        color = _read(image, roi["color"], "Color", engine=engine)
        size = _read(image, roi["size"], "Size", engine=engine)
        qty = _read(image, roi["qty"], "Qty", engine=engine)
        # Ignore isolated OCR noise in a blank group (for example a stray "7"
        # interpreted from a table line). A group must contain a code, colour,
        # or size to become a record; quantity alone is not enough evidence.
        if not any((code, color, size)):
            continue

        # If the header's first Item No was unreadable, use the first validated
        # line-item code as the common Item No value rather than leaving it empty.
        if not header.get("Item No") and code:
            header["Item No"] = code

        line = {column: "" for column in COLUMNS}
        line.update(header)
        line.update({"Item Code": code, "Color": color, "Size": size, "Qty": qty})
        records.append(line)

    notes = []
    if not header.get("Challan No"):
        notes.append("Challan No could not be read confidently. Enter it in the review table.")
    if not header.get("Date"):
        notes.append("Date could not be read confidently. Enter it in the review table.")
    if not header.get("Item No"):
        notes.append("Item No could not be read confidently from the header or line-item codes.")
    if not header.get("Style"):
        notes.append("Style could not be read confidently. Enter it in the review table.")
    if not records:
        notes.append("No line-item values passed validation. Add the rows manually in the review table.")
    elif any(not row.get(field) for row in records for field in ("Item Code", "Color", "Size", "Qty")):
        notes.append("Some line-item cells were not read confidently and are left blank for correction.")
    engine_name = "TrOCR local handwriting model" if engine == "trocr" else "Tesseract Local OCR"
    notes.append(
        f"{engine_name} reads only the Challan No, Date, Item No, Style and the three repeated line-item groups. "
        "The Item No list is matched to groups in left-to-right order when a group-local code is unclear. "
        "Handwriting recognition is imperfect; verify all cells before saving."
    )
    return records, notes
