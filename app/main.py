import os
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

    try:
        extracted = extraction.extract_content(str(saved_path))
        attribute_library = fyind_client.get_attribute_library()
        products = classifier.classify_catalog(extracted, attribute_library)
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
    })


@app.get("/api/download")
async def download(path: str):
    full = BASE_DIR / path
    if not full.exists() or full.parent.name != "outputs":
        raise HTTPException(404, "File not found")
    return FileResponse(str(full), filename=full.name)


@app.get("/api/health")
async def health():
    return {"status": "ok", "mock_mode": config.use_mock()}

@app.get("/api/debug/attributes")
async def debug_attributes():
    import sys
    library = fyind_client.get_attribute_library()
    size_bytes = sys.getsizeof(str(library))
    return {
        "product_count": len(library),
        "approx_size_mb": round(size_bytes / (1024 * 1024), 2),
    }
