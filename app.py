import base64
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pytesseract
import streamlit as st
from dotenv import load_dotenv
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from PIL import Image, ImageEnhance, ImageOps
from challan_ocr import COLUMNS, local_extract_one, orient_image

load_dotenv()
st.set_page_config(page_title="Challan AI to Excel Bot", page_icon="🧾", layout="wide")

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "challan_history.db"
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")


def get_api_key():
    try:
        return st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    except Exception:
        return os.getenv("OPENAI_API_KEY", "")


def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS extraction_history (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, image_names TEXT NOT NULL,
            engine TEXT NOT NULL, record_count INTEGER NOT NULL, workbook_name TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS saved_records (
            row_hash TEXT PRIMARY KEY, challan_no TEXT, item_code TEXT, color TEXT,
            size TEXT, qty TEXT, saved_at TEXT NOT NULL)""")


def record_hash(row):
    key = [str(row.get(c, "")).strip().upper() for c in COLUMNS]
    return hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()


def duplicate_hashes():
    with sqlite3.connect(DB_PATH) as con:
        return {r[0] for r in con.execute("SELECT row_hash FROM saved_records")}


def save_records_and_history(records, image_names, engine, workbook_name):
    hashes = duplicate_hashes()
    fresh, duplicates = [], []
    for row in records:
        h = record_hash(row)
        if h in hashes:
            duplicates.append(row)
        else:
            fresh.append(row)
            hashes.add(h)
    with sqlite3.connect(DB_PATH) as con:
        for row in fresh:
            con.execute("INSERT OR IGNORE INTO saved_records VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (record_hash(row), row.get("Challan No", ""), row.get("Item Code", ""),
                         row.get("Color", ""), row.get("Size", ""), row.get("Qty", ""),
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.execute("INSERT INTO extraction_history VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     ", ".join(image_names), engine, len(fresh), workbook_name))
    return fresh, duplicates


def get_history(limit=12):
    with sqlite3.connect(DB_PATH) as con:
        return con.execute("""SELECT created_at, image_names, engine, record_count, workbook_name
            FROM extraction_history ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()



def ai_extract(images):
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("Optional AI mode needs OPENAI_API_KEY. Switch to Free Local OCR to continue without API credits.")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    content = [{"type": "input_text", "text":
        "Extract only these eight fields from Adishtam Ventures Line Issue Note images: "
        + ", ".join(COLUMNS) + ". Ignore all other printed text, stamps, signatures and addresses. "
        "The header Item No field can contain several comma-separated numbers; use the first number as the common Item No. "
        "The handwritten number in each repeated Color/Size/Qty group is Item Code. Match that Item Code only to the Color, Size and Qty in the same group. "
        "Return one row for every colour/size/quantity combination. Repeat header fields on each row. "
        "Do not guess; use empty strings for unreadable values. Date format DD/MM/YYYY. "
        "Use JSON object {records:[{...}], notes:[...]}. Keep all values as strings."}]
    for f in images:
        mime = f.type or "image/jpeg"
        if mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError(f"Unsupported image type: {f.name}")
        if f.size > 15 * 1024 * 1024:
            raise ValueError(f"{f.name} exceeds 15 MB.")
        content.append({"type": "input_image", "image_url":
                        f"data:{mime};base64,{base64.b64encode(f.getvalue()).decode()}", "detail": "high"})
    response = client.responses.create(
        model=MODEL, input=[{"role": "user", "content": content}],
        text={"format": {"type": "json_object"}})
    payload = json.loads(response.output_text)
    rows = [{c: str(r.get(c, "") or "") for c in COLUMNS} for r in payload.get("records", [])]
    return rows, payload.get("notes", [])


def create_workbook(records=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Challan Register"
    for col, name in enumerate(COLUMNS, 1):
        cell = ws.cell(1, col, name)
        cell.fill = PatternFill("solid", fgColor="163A5F")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col)].width = [16, 14, 12, 24, 14, 16, 10, 10][col-1]
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:H1"
    for row in records or []:
        ws.append([row.get(c, "") for c in COLUMNS])
        for cell in ws[ws.max_row]:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.auto_filter.ref = f"A1:H{max(ws.max_row, 1)}"
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def append_to_workbook(data, records):
    wb = load_workbook(io.BytesIO(data))
    ws = wb["Challan Register"] if "Challan Register" in wb.sheetnames else wb.active
    headers = [ws.cell(1, i).value for i in range(1, 9)]
    if headers != COLUMNS:
        raise ValueError("Workbook columns do not match the required eight-column layout.")
    for row in records:
        ws.append([row.get(c, "") for c in COLUMNS])
        for cell in ws[ws.max_row]:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.auto_filter.ref = f"A1:H{ws.max_row}"
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


init_db()
if "workbook_bytes" not in st.session_state:
    st.session_state.workbook_bytes = create_workbook()
if "pending_records" not in st.session_state:
    st.session_state.pending_records = []
if "pending_notes" not in st.session_state:
    st.session_state.pending_notes = []
if "pending_images" not in st.session_state:
    st.session_state.pending_images = []

st.title("🧾 Challan AI to Excel Bot")
st.caption("Adishtam Ventures Line Issue Note · Field-specific OCR · Eight-column Excel")

with st.sidebar:
    st.header("Extraction settings")
    engine = st.radio(
        "Extraction mode",
        ["Free Local OCR (Tesseract)", "Free Local Handwriting OCR (TrOCR)", "Optional OpenAI AI"],
    )
    if engine == "Free Local OCR (Tesseract)":
        st.success("Free: no API key or API credits required.")
        st.caption("Tesseract runs locally and reads only the required field crops. It may leave difficult handwriting blank for review.")
    elif engine == "Free Local Handwriting OCR (TrOCR)":
        st.info("Free local handwritten-text model. Install requirements-handwriting.txt; the model downloads once on first use (about 247 MB). No OpenAI key or API credits are used.")
    else:
        st.info("Requires OPENAI_API_KEY in .env or Streamlit secrets. API usage may incur charges.")
    st.divider()
    st.subheader("Extraction history")
    history = get_history()
    if history:
        for created, names, mode, count, book in history:
            st.caption(f"{created} · {mode} · {count} saved row(s)")
            st.text(names[:100])
    else:
        st.caption("No saved extraction history yet.")

st.subheader("1. Upload challan images")
uploads = st.file_uploader("Upload one or more JPG, JPEG, PNG or WEBP images",
                           type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)
if uploads:
    cols = st.columns(min(3, len(uploads)))
    for i, f in enumerate(uploads):
        with cols[i % len(cols)]:
            try:
                preview = orient_image(Image.open(io.BytesIO(f.getvalue())))
                st.image(preview, caption=f.name + " · corrected orientation", use_container_width=True)
            except Exception:
                st.image(f, caption=f.name, use_container_width=True)

if st.button("🔎 Extract required fields", type="primary", disabled=not uploads, use_container_width=False):
    try:
        with st.spinner("Correcting orientation and reading the target fields..."):
            all_rows, all_notes = [], []
            if engine == "Free Local OCR (Tesseract)":
                for f in uploads:
                    rows, notes = local_extract_one(f, engine="tesseract")
                    all_rows.extend(rows)
                    all_notes.extend([f"{f.name}: {n}" for n in notes])
            elif engine == "Free Local Handwriting OCR (TrOCR)":
                for f in uploads:
                    rows, notes = local_extract_one(f, engine="trocr")
                    all_rows.extend(rows)
                    all_notes.extend([f"{f.name}: {n}" for n in notes])
            else:
                all_rows, all_notes = ai_extract(uploads)
            st.session_state.pending_records = all_rows
            st.session_state.pending_notes = all_notes
            st.session_state.pending_images = [f.name for f in uploads]
            if not all_rows:
                st.warning("No line-item rows were detected. You can add rows manually below.")
            else:
                st.success(f"Prepared {len(all_rows)} row(s). Review every handwritten value.")
    except Exception as exc:
        st.error(f"Extraction failed: {exc}")
        if engine == "Optional OpenAI AI":
            st.info("Check API key, model access, billing/quota and internet. Both local OCR modes remain available.")
        elif engine == "Free Local Handwriting OCR (TrOCR)":
            st.info("Install the optional handwriting packages with: pip install -r requirements-handwriting.txt. The first use also needs internet to download the local model.")

st.subheader("2. Review and edit extracted rows")
st.caption("Exactly eight columns are used. Each populated Color / Size / Qty group becomes its own row. Correct blank or uncertain handwritten values before saving; add rows for any missed combinations.")
if st.session_state.pending_records is not None:
    df = pd.DataFrame(st.session_state.pending_records, columns=COLUMNS)
    edited = st.data_editor(df, num_rows="dynamic", use_container_width=True, hide_index=True,
                            key="challan_review")
    if st.button("💾 Save verified rows", type="primary"):
        rows = edited.fillna("").astype(str).to_dict(orient="records")
        rows = [r for r in rows if any(str(r.get(c, "")).strip() for c in COLUMNS)]
        fresh, duplicates = save_records_and_history(rows, st.session_state.pending_images,
                                                      engine, "Challan_Register.xlsx")
        if fresh:
            st.session_state.workbook_bytes = append_to_workbook(st.session_state.workbook_bytes, fresh)
            st.success(f"Saved {len(fresh)} new row(s) to the workbook.")
        if duplicates:
            st.warning(f"Skipped {len(duplicates)} duplicate row(s) already saved in the local database.")
        st.session_state.pending_records = []
        st.session_state.pending_notes = []
        st.session_state.pending_images = []
else:
    st.info("Upload challan images and extract fields to begin. You can also add records manually after extraction.")

for note in st.session_state.pending_notes:
    st.warning(note)

st.subheader("3. Excel template and export")
st.download_button("⬇️ Download blank Excel template", create_workbook(),
                   file_name="Challan_Template.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
st.download_button("⬇️ Download updated Excel workbook", st.session_state.workbook_bytes,
                   file_name="Challan_Register.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   type="primary")
st.caption("The workbook is kept in the current Streamlit session. Download it before closing or restarting the app.")
