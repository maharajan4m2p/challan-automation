# Changelog

## OCR reliability update

- Corrected the normalized crop positions for the repeated line-item groups.
- Separated the Item Code row from the Colour row; the prior crops overlapped both rows.
- Added validation for numeric fields and dates so incomplete/conflicting readings are left blank.
- Added an optional local TrOCR handwriting-recognition mode using `microsoft/trocr-small-handwritten`.
- Kept Tesseract Local OCR and Optional OpenAI AI available.
- Updated the UI preview to show the orientation-corrected challan image.
- Preserved the eight-column Excel workbook, editable review table, SQLite duplicate checking and extraction history.

## Validation status

- Python syntax compilation: passed.
- OCR helper tests on both supplied sample images: executed; Tesseract still missed multiple handwritten fields, so this mode must be reviewed manually.
- Streamlit app import and Excel workbook round-trip: passed using a UI stub because Streamlit is not installed in the build environment.
- Actual Streamlit server startup: not run in this environment.
- TrOCR model inference: not run because the model packages/weights could not be downloaded in this environment. Install `requirements-handwriting.txt`; the model downloads on first use.
