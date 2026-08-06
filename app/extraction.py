"""
Turns an uploaded supplier catalog (PDF / DOCX / image) into content Claude
can read: either extracted text, or base64 images for pages that are mostly
pictures/tables (common in product brochures).
"""
import base64
import io
from pathlib import Path

import fitz  # PyMuPDF
from docx import Document
from PIL import Image

MAX_PAGES_AS_IMAGES = 10  # safety cap so we don't blow up memory on free-tier hosting


def extract_content(filepath: str) -> dict:
    """
    Returns {"text": str, "images": [base64_png, ...]}
    Both may be populated; the classifier will send whichever is non-empty.
    """
    ext = Path(filepath).suffix.lower()

    if ext == ".pdf":
        return _extract_pdf(filepath)
    elif ext in (".docx", ".doc"):
        return _extract_docx(filepath)
    elif ext in (".png", ".jpg", ".jpeg", ".webp"):
        return _extract_image(filepath)
    elif ext in (".xlsx", ".csv"):
        return _extract_tabular(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _extract_pdf(filepath: str) -> dict:
    doc = fitz.open(filepath)
    text_parts = []
    images_b64 = []

    for i, page in enumerate(doc):
        text = page.get_text()
        text_parts.append(text)

        # If a page has very little extractable text, it's likely image-heavy
        # (common for glossy product brochures) -- render it as an image too
        # so Claude's vision can read tables/photos/captions.
        if len(text.strip()) < 40 and len(images_b64) < MAX_PAGES_AS_IMAGES:
            pix = page.get_pixmap(dpi=72)  # low DPI keeps memory usage small on free-tier hosting
            img_bytes = pix.tobytes("png")
            images_b64.append(base64.b64encode(img_bytes).decode())
            pix = None  # release reference promptly

    # Always also render first N pages as images for brochures with logos/
    # tables that render poorly as plain text.
    if len(images_b64) == 0 and len(doc) <= MAX_PAGES_AS_IMAGES:
        for page in doc:
            pix = page.get_pixmap(dpi=72)
            img_bytes = pix.tobytes("png")
            images_b64.append(base64.b64encode(img_bytes).decode())
            pix = None

    doc.close()  # explicitly free the PDF from memory
    return {"text": "\n\n".join(text_parts), "images": images_b64}


def _extract_docx(filepath: str) -> dict:
    doc = Document(filepath)
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return {"text": "\n".join(parts), "images": []}


def _extract_image(filepath: str) -> dict:
    img = Image.open(filepath).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {"text": "", "images": [base64.b64encode(buf.getvalue()).decode()]}


def _extract_tabular(filepath: str) -> dict:
    import pandas as pd
    ext = Path(filepath).suffix.lower()
    df = pd.read_csv(filepath) if ext == ".csv" else pd.read_excel(filepath)
    return {"text": df.to_csv(index=False), "images": []}
