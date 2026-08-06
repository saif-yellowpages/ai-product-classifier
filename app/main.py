import uuid
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, fyind_client, extraction, classifier, excel_writer

app = FastAPI(title="FYIND Product Classifier")

BASE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/suppliers")
async def suppliers():
    try:
        return JSONResponse(fyind_client.get_suppliers())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not fetch suppliers: {e}")


@app.post("/api/classify")
async def classify(
    supplier_id: str = Form(...),
    supplier_name: str = Form(...),
    file: UploadFile = File(...),
):
    ext = Path(file.filename).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > config.MAX_UPLOAD_MB:
        raise HTTPException(400, f"File too large ({size_mb:.1f}MB > {config.MAX_UPLOAD_MB}MB limit)")

    job_id = uuid.uuid4().hex[:12]
    saved_path = UPLOAD_DIR / f"{job_id}{ext}"
    saved_path.write_bytes(contents)
    contents = None  # release the in-memory copy now that it's on disk

    try:
        # ---- Step 1: get a small preview of the catalog to guess categories ----
        # We deliberately do NOT fetch FYIND's full ~9,048-product library
        # here -- that was the cause of repeated out-of-memory crashes.
        # Instead: peek at a bit of the catalog, guess likely categories,
        # then fetch only matching attribute sets.
        if ext == ".pdf":
            preview = _peek_pdf(str(saved_path), max_pages=3)
        else:
            preview = extraction.extract_content(str(saved_path))

        keywords = classifier.guess_categories(preview)
        attribute_library = fyind_client.search_attribute_sets(keywords) if keywords else {}

        # ---- Step 2: classify ----
        if ext == ".pdf":
            products = classifier.classify_catalog_streaming(str(saved_path), attribute_library)
        else:
            products = classifier.classify_catalog(preview, attribute_library)

        out_path = excel_writer.build_export(products, attribute_library, supplier_name)
    except Exception as e:
        raise HTTPException(500, f"Classification failed: {e}")
    finally:
        saved_path.unlink(missing_ok=True)

    return JSONResponse({
        "job_id": job_id,
        "download_url": f"/api/download?path={out_path}",
        "classified_count": sum(1 for p in products if p.get("matched_product_name")),
        "unclassified_count": sum(1 for p in products if not p.get("matched_product_name")),
        "total": len(products),
        "categories_searched": keywords,
    })


def _peek_pdf(filepath: str, max_pages: int = 3) -> dict:
    """Cheap preview of just the first few pages, used only for category
    guessing -- not the full document, keeps this step fast and light."""
    text_parts = []
    images = []
    gen = extraction.iter_pdf_pages(filepath)
    try:
        for page_num, text, image_b64 in gen:
            if page_num >= max_pages:
                break
            text_parts.append(text)
            if image_b64:
                images.append(image_b64)
    finally:
        gen.close()  # triggers the generator's `finally: doc.close()` even on early break
    return {"text": "\n\n".join(text_parts), "images": images}


@app.get("/api/download")
async def download(path: str):
    full = BASE_DIR / path
    if not full.exists() or full.parent.name != "outputs":
        raise HTTPException(404, "File not found")
    return FileResponse(str(full), filename=full.name)


@app.get("/api/health")
async def health():
    return {"status": "ok", "mock_mode": config.use_mock()}
