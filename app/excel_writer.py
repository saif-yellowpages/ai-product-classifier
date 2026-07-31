"""
Fills the real FYIND "upload-ready" template (templates_xlsx/upload_template.xlsx)
with classified products, and appends two extra columns beyond the template:
"Confidence" and "Review".

Only the columns the user asked for are populated:
  Item Description, Manufacturer, Place Of Origin, Additional Information,
  Supplier, Supplier Status, Price, Attribute Set, Attribute 1..6, Brand,
  Warranty, Guarantee, Return & Exchange, Status
Everything else in the template (Listing Code, Item Code, Relationship,
Images, Featured Image, Video File Name, Speciality Tags, Qoh, Datasheets,
Stocklist, Short Description, Clearance, Sku) is left blank -- those are
presumably filled in later / by another system.
"""
from datetime import datetime
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from app import config

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = BASE_DIR / "templates_xlsx" / "upload_template.xlsx"

# Column letters in the template, by header name (from the sample file you sent)
COLS = {
    "Item Description": "D",
    "Manufacturer": "E",
    "Place Of Origin": "F",
    "Additional Information": "G",
    "Supplier": "L",
    "Supplier Status": "M",
    "Price": "R",
    "Attribute Set": "U",
    "Attribute 1": "V",
    "Attribute 2": "W",
    "Attribute 3": "X",
    "Attribute 4": "Y",
    "Attribute 5": "Z",
    "Attribute 6": "AA",
    "Brand": "AB",
    "Warranty": "AC",
    "Guarantee": "AD",
    "Return & Exchange": "AE",
    "Status": "AF",
}
CONFIDENCE_COL = "AG"
REVIEW_COL = "AH"

HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=12)


def build_export(products: list[dict], attribute_library: dict, supplier_name: str) -> str:
    """
    products: list of dicts as returned by classifier.classify_catalog()
    Returns the filepath of the generated .xlsx file (a filled copy of the
    real upload template).
    """
    wb = load_workbook(TEMPLATE_PATH)
    ws = wb["Sheet1"]

    # Style + label the two extra columns we're appending
    ws[f"{CONFIDENCE_COL}1"] = "Confidence"
    ws[f"{REVIEW_COL}1"] = "Review"
    for col in (CONFIDENCE_COL, REVIEW_COL):
        cell = ws[f"{col}1"]
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[col].width = 16

    row_idx = 2
    for p in products:
        if not p.get("item_description"):
            continue  # item_description is mandatory; skip anything missing it

        pname = p.get("matched_product_name")
        attribute_set_name = ""
        if pname and pname in attribute_library:
            attribute_set_name = attribute_library[pname]["attribute_set_name"]
        elif pname:
            attribute_set_name = pname  # matched a name Claude saw but our local copy lacks metadata for

        ws[f"{COLS['Item Description']}{row_idx}"] = p["item_description"]
        ws[f"{COLS['Manufacturer']}{row_idx}"] = p.get("manufacturer") or ""
        ws[f"{COLS['Place Of Origin']}{row_idx}"] = p.get("place_of_origin") or ""
        ws[f"{COLS['Additional Information']}{row_idx}"] = p.get("additional_information") or ""
        ws[f"{COLS['Supplier']}{row_idx}"] = supplier_name
        ws[f"{COLS['Supplier Status']}{row_idx}"] = p.get("supplier_status") or "Suppliers"
        ws[f"{COLS['Price']}{row_idx}"] = p.get("price") or ""
        ws[f"{COLS['Attribute Set']}{row_idx}"] = attribute_set_name

        attrs = p.get("attributes") or []
        for i in range(config.MAX_ATTRIBUTES):
            col = COLS[f"Attribute {i+1}"]
            if i < len(attrs):
                a = attrs[i]
                ws[f"{col}{row_idx}"] = f"{a['name']}:{a['value']}"
            else:
                ws[f"{col}{row_idx}"] = ""

        ws[f"{COLS['Brand']}{row_idx}"] = p.get("brand") or ""
        ws[f"{COLS['Warranty']}{row_idx}"] = p.get("warranty") or ""
        ws[f"{COLS['Guarantee']}{row_idx}"] = p.get("guarantee") or ""
        ws[f"{COLS['Return & Exchange']}{row_idx}"] = p.get("return_exchange") or ""
        ws[f"{COLS['Status']}{row_idx}"] = "Active"

        confidence = p.get("confidence", 0)
        ws[f"{CONFIDENCE_COL}{row_idx}"] = f"{confidence}%"
        ws[f"{REVIEW_COL}{row_idx}"] = (
            "Confident" if confidence >= config.CONFIDENCE_THRESHOLD else "Needs review"
        )

        row_idx += 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_supplier = "".join(c if c.isalnum() else "_" for c in supplier_name)[:40]
    out_dir = BASE_DIR / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{safe_supplier}_{timestamp}.xlsx"
    wb.save(out_path)
    return str(out_path.relative_to(BASE_DIR))
