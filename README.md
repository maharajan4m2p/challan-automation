# Challan AI to Excel Bot

Python + Streamlit application for Adishtam Ventures Line Issue Note images. The app extracts only the required form cells and writes the results into an Excel workbook with exactly eight columns:

1. Challan No
2. Date
3. Item No
4. Style
5. Item Code
6. Color
7. Size
8. Qty

Each populated Color / Size / Qty group becomes a separate Excel row. The app repeats the common Challan No, Date, Item No and Style values on each row. The ordered Item No list in the header is used to match line-item groups from left to right when a group-local Item Code is unclear.

## Important handwriting note

The supplied notes use pencil/pen handwriting on pink paper. Tesseract is a general OCR engine and is not a handwriting-specific model. On difficult handwriting it may miss digits or return incorrect words. The app now rejects incomplete/conflicting numeric readings and leaves uncertain cells blank instead of treating them as valid data. **Always review the editable table before saving.**

For stronger local handwriting recognition, use the optional TrOCR mode described below. It is free to run locally and does not use OpenAI API credits, but it requires a one-time model download and more memory than Tesseract. The Microsoft TrOCR small handwritten checkpoint is designed for recognition of single-line handwritten text; it is not trained specifically on this company's challan form, so manual verification is still required.

## Extraction modes

### 1. Free Local OCR (Tesseract)

- Uses Tesseract, OpenCV and Pillow locally.
- No API key, API credits or internet connection required after installation.
- Corrects the common portrait-photo orientation.
- Crops only the Challan No, Date, Item No, Style and the three repeated line-item groups.
- Separates the Item Code row from the Color row; these crops no longer overlap.
- Applies numeric/date validation to reduce false values. Unreadable cells remain blank for review.

### 2. Free Local Handwriting OCR (TrOCR) – optional add-on

This uses Microsoft's `microsoft/trocr-small-handwritten` model for cropped handwritten lines. It runs on the local computer after its model files have been downloaded.

1. Open Command Prompt in the project folder and activate your virtual environment.
2. Install the optional packages:

   ```bat
   pip install -r requirements-handwriting.txt
   ```
3. Start Streamlit:

   ```bat
   streamlit run app.py
   ```
4. In the sidebar, select **Free Local Handwriting OCR (TrOCR)**.
5. On first use, the app needs internet access to download the model (about 247 MB). Later runs use the local cache and do not need OpenAI or API credits.

This optional mode has a larger installation footprint and is slower on CPU than Tesseract. The model is downloaded from Hugging Face and is not bundled inside this project ZIP.

Model information: https://huggingface.co/microsoft/trocr-small-handwritten
Official usage documentation: https://huggingface.co/docs/transformers/model_doc/trocr

### 3. Optional OpenAI AI

- Select **Optional OpenAI AI** in the sidebar.
- Configure `OPENAI_API_KEY` in `.env` or Streamlit secrets.
- Uploaded images are sent to OpenAI for extraction. API usage may incur charges.
- If the key, quota, model access or network is unavailable, an error is shown. Local modes remain independent.

## Windows installation

1. Install Python 3.10 or newer: https://www.python.org/downloads/.
2. Install Tesseract OCR for Windows. One commonly used Windows build is available at https://github.com/UB-Mannheim/tesseract/wiki.
3. During Tesseract setup, include English language data. The app checks these usual paths:
   - `C:\Program Files\Tesseract-OCR\tesseract.exe`
   - `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`
   Or add the Tesseract folder to Windows PATH.
4. Extract this ZIP and open the `Challan_AI_Excel_Bot` folder in VS Code or Command Prompt.
5. Create and activate a virtual environment:

   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   ```
6. Install the standard requirements:

   ```bat
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
7. Start the application:

   ```bat
   streamlit run app.py
   ```
8. Open the local URL printed by Streamlit, usually http://localhost:8501.

## Optional OpenAI setup

1. Copy `.env.example` to `.env`.
2. Add your API key and model:

   ```env
   OPENAI_API_KEY=your_api_key_here
   OPENAI_MODEL=gpt-5.5
   ```
3. Restart Streamlit and choose **Optional OpenAI AI**.

Never commit `.env` or share your API key. A ChatGPT subscription does not include OpenAI API credits.

## Workflow

1. Select Tesseract, TrOCR handwriting or Optional OpenAI AI.
2. Upload one or more JPG, JPEG, PNG or WEBP challan images.
3. Click **Extract required fields**.
4. Review and correct the editable eight-column table. Add rows for any missed combinations.
5. Click **Save verified rows**.
6. Download the blank template or updated workbook.

## Excel output

The workbook sheet is named `Challan Register`, with dark-blue column headers, filters, frozen header row and the exact eight requested columns. The blank template and completed workbook are downloadable from the app.

## Duplicate checking and history

- SQLite database `challan_history.db` is created automatically beside `app.py`.
- Exact duplicate rows already saved in this local database are skipped.
- Extraction history records the time, image names, extraction mode, saved row count and workbook name.

## Accuracy and limitations

- OCR coordinates are tuned to the supplied Adishtam Ventures Line Issue Note layout. If the company changes the form layout, update the normalized ROIs in `challan_ocr.py`.
- TrOCR is a general handwriting recognizer and is not custom-trained on the specific handwriting used by the challan issuers.
- The model cannot guarantee exact reading of every handwritten number, colour name or style. Review the output before saving it as business data.
- The workbook is kept in the current Streamlit session. Download it before closing or restarting the app.
- Local modes process images on your computer. Optional OpenAI AI sends the images to the configured API.
