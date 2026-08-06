"""
Client for FYIND's real Admin APIs (per fyind_api.md):

  - GET /api/Admin/Supplier/catalogue/suppliers          -> supplier list
  - GET /api/Admin/AttributeSet/api/GetAttributesPaginated -> attribute library

Both are paginated; we page through them fully so the app has the complete
supplier list and attribute library to work with. Results are cached
in-memory for a short time (see CACHE_TTL_SECONDS) since the attribute
library in particular can be large and doesn't change minute to minute.
"""
import json
import time
import requests
from app import config

SUPPLIER_ENDPOINT = f"{config.FYIND_BASE_URL}/api/Admin/Supplier/catalogue/suppliers"
ATTRIBUTE_ENDPOINT = f"{config.FYIND_BASE_URL}/api/Admin/AttributeSet/api/GetAttributesPaginated"

COMMON_HEADERS = {
    "Accept": "*/*",
    "Origin": "https://manage.fyind.com",
    "Referer": "https://manage.fyind.com/",
    "User-Agent": "Mozilla/5.0 (FYIND-Classifier-App)",
}

CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
_cache = {"suppliers": None, "suppliers_at": 0, "attributes": None, "attributes_at": 0}


def _headers():
    h = dict(COMMON_HEADERS)
    if config.FYIND_REGION:
        h["Region"] = config.FYIND_REGION
    return h


# ---------------------------------------------------------------------------
# MOCK DATA (used only if config.use_mock() is True -- e.g. no network yet)
# ---------------------------------------------------------------------------
_MOCK_SUPPLIERS = [
    {"id": 1001, "name": "Deep Sea Pipes & Fittings Trading LLC"},
    {"id": 1002, "name": "Al Manara Industrial Supplies"},
    {"id": 1003, "name": "Gulf Valve & Flange Co."},
]

_MOCK_ATTRIBUTE_LIBRARY = {
    "Gate Valve": {
        "attribute_set_name": "Gate Valve_Valves",
        "attributes": {"Size": [], "Material": [], "Class": [], "End Connection": []},
    },
    "Ball Valve": {
        "attribute_set_name": "Ball Valve_Valves",
        "attributes": {"Size": [], "Material": [], "Pressure": [], "Type": []},
    },
    "Pipe Elbow": {
        "attribute_set_name": "Pipe Elbow_Pipe Fittings",
        "attributes": {"Size": [], "Material": [], "Type": [], "End Type": []},
    },
}


# ---------------------------------------------------------------------------
# PUBLIC FUNCTIONS
# ---------------------------------------------------------------------------

def get_suppliers(force_refresh: bool = False) -> list[dict]:
    """Returns list of {id, name} dicts for the dropdown."""
    if config.use_mock():
        return _MOCK_SUPPLIERS

    now = time.time()
    if not force_refresh and _cache["suppliers"] and (now - _cache["suppliers_at"] < CACHE_TTL_SECONDS):
        return _cache["suppliers"]

    all_suppliers = []
    page = 1
    page_size = 200
    while True:
        # Their own admin UI always sends "adminSupplier" as a bare query key
        # (no value) -- omitting it causes a 404. Build the URL manually to
        # match that exact shape rather than relying on requests' params dict.
        url = f"{SUPPLIER_ENDPOINT}?adminSupplier&pageSize={page_size}&pageIndex={page}&status=true"
        resp = requests.get(
            url,
            headers=_headers(),
            timeout=20,
        )
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break

        for r in rows:
            all_suppliers.append({
                "id": r.get("supplierID"),
                "name": r.get("supplierName"),
            })

        total = rows[0].get("totalSupplierCount", len(rows))
        if len(all_suppliers) >= total or len(rows) < page_size:
            break
        page += 1

    # de-dupe by id, sort alphabetically for a nicer dropdown
    seen = {}
    for s in all_suppliers:
        seen[s["id"]] = s
    result = sorted(seen.values(), key=lambda s: (s["name"] or "").lower())

    _cache["suppliers"] = result
    _cache["suppliers_at"] = now
    return result


def get_attribute_library(force_refresh: bool = False) -> dict:
    """
    Returns dict keyed by Product Name:
      {
        "Gate Valve": {
            "attribute_set_name": "Gate Valve_Valves",
            "attributes": {"Size": [...], "Material": [...], ...}
        },
        ...
      }
    """
    if config.use_mock():
        return _MOCK_ATTRIBUTE_LIBRARY

    now = time.time()
    if not force_refresh and _cache["attributes"] and (now - _cache["attributes_at"] < CACHE_TTL_SECONDS):
        return _cache["attributes"]

    library = {}
    page = 1
    page_size = 500
    while True:
        resp = requests.get(
            ATTRIBUTE_ENDPOINT,
            params={"pageSize": page_size, "pageIndex": page},
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break

        for row in rows:
            pname = row.get("productName")
            if not pname:
                continue
            library[pname] = {
                "attribute_set_name": row.get("attributeSetName"),
                "attributes": _parse_attribute_values(row.get("attributeSetValues")),
            }

        total = rows[0].get("totalCount", len(rows))
        if page * page_size >= total or len(rows) < page_size:
            break
        page += 1

    _cache["attributes"] = library
    _cache["attributes_at"] = now
    return library


def _parse_attribute_values(raw) -> dict:
    """
    attributeSetValues comes back as a JSON-encoded string, e.g.
      '{"Size":["1/2\\"","3/4\\""],"Material":["Cast Iron","Bronze"]}'
    or sometimes a simpler flat form like '{"Status":"Active"}'.
    Normalizes everything to {attr_name: [allowed_values...]}.
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Fallback: some legacy rows may still use the old packed-string
        # format ("Attr1:val1, val2, Attr2:val1, ...") we handled for the
        # manually-uploaded AttributeSetList.xlsx earlier in this project.
        return _parse_legacy_packed_string(raw)

    result = {}
    for k, v in data.items():
        if isinstance(v, list):
            result[k] = [str(x) for x in v]
        elif v in (None, ""):
            result[k] = []
        else:
            result[k] = [str(v)]
    return result


def _parse_legacy_packed_string(s: str) -> dict:
    import re
    if not isinstance(s, str) or not s:
        return {}
    parts = re.split(r'(?:^|,\s*)([A-Za-z][A-Za-z0-9 /\.\-_]{0,30}?):', s)
    result = {}
    it = iter(parts[1:])
    for label, value in zip(it, it):
        label = label.strip()
        vals = [v.strip() for v in value.split(',') if v.strip()]
        if label not in result:
            result[label] = vals
    return result
