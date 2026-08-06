"""
Client for FYIND's real Admin APIs (per fyind_api.md):

  - GET /api/Admin/Supplier/catalogue/suppliers          -> supplier list
  - GET /api/Admin/AttributeSet/api/GetAttributesPaginated -> attribute library

MEMORY-CRITICAL DESIGN NOTE:
FYIND's full attribute library has ~9,048 product names, and some rows have
very large/messy `attributeSetValues` strings. Loading the ENTIRE library
into memory on every classify request was the root cause of repeated
out-of-memory crashes on Render's free tier -- confirmed via a debug
endpoint that crashed the server just by calling get_attribute_library()
alone, with no file upload involved.

Two fixes, both in this file:
  1. search_attribute_sets(keywords) -- uses FYIND's own `attributeSetName`
     search parameter to fetch only a small, relevant slice of the library
     (typically dozens-to-hundreds of rows, not 9,048), based on category
     keywords Claude guesses from the catalog first.
  2. Attribute VALUES are discarded entirely, everywhere -- we only ever
     need attribute NAMES (for Claude's prompt) and the attribute_set_name
     (for the Excel export). Nothing downstream uses the allowed-values
     lists, so keeping them was pure memory waste, especially for the rows
     with huge malformed value strings.

get_attribute_library() (the old full-fetch-everything function) is kept
only as a manual/administrative fallback -- it is NOT used in the normal
classify flow anymore. Do not wire it back into the request path without
also addressing its memory footprint (e.g. run it as a one-off background
job that writes a slimmed cache file, rather than fetching live per-request).
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
_cache = {"suppliers": None, "suppliers_at": 0}

# Hard caps to prevent the prompt sent to Claude from exploding. Some FYIND
# rows have severely malformed `attributeSetValues` strings that parse into
# hundreds of spurious "attribute names" -- and broad category keywords
# (e.g. "pipe", "steel") can legitimately match hundreds of real products.
# Without these caps, a single classify request hit 1.25M tokens (limit is
# 200K) purely from the attribute library JSON, before any catalog content.
MAX_ATTRIBUTE_NAMES_PER_PRODUCT = 12
MAX_LIBRARY_ENTRIES = 300  # total, across ALL keywords combined

# Per-keyword search result cache, so repeated uploads with similar catalogs
# (e.g. your team testing the same supplier a few times) don't re-hit FYIND
# every single time. Small in-memory dict: {keyword_lowercase: (result_dict, timestamp)}
_search_cache = {}


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
    "Gate Valve": {"attribute_set_name": "Gate Valve_Valves", "attribute_names": ["Size", "Material", "Class", "End Connection"]},
    "Ball Valve": {"attribute_set_name": "Ball Valve_Valves", "attribute_names": ["Size", "Material", "Pressure", "Type"]},
    "Pipe Elbow": {"attribute_set_name": "Pipe Elbow_Pipe Fittings", "attribute_names": ["Size", "Material", "Type", "End Type"]},
}


# ---------------------------------------------------------------------------
# SUPPLIERS
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
        # (no value) -- omitting it causes a 404.
        url = f"{SUPPLIER_ENDPOINT}?adminSupplier&pageSize={page_size}&pageIndex={page}&status=false"
        resp = requests.get(url, headers=_headers(), timeout=20)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break

        for r in rows:
            all_suppliers.append({"id": r.get("supplierID"), "name": r.get("supplierName")})

        total = rows[0].get("totalSupplierCount", len(rows))
        if len(all_suppliers) >= total or len(rows) < page_size:
            break
        page += 1

    seen = {}
    for s in all_suppliers:
        seen[s["id"]] = s
    result = sorted(seen.values(), key=lambda s: (s["name"] or "").lower())

    _cache["suppliers"] = result
    _cache["suppliers_at"] = now
    return result


# ---------------------------------------------------------------------------
# ATTRIBUTE SETS -- targeted search (the memory-safe path, used by default)
# ---------------------------------------------------------------------------

def search_attribute_sets(keywords: list[str], page_size: int = 50, max_pages_per_keyword: int = 2) -> dict:
    """
    Fetches only attribute sets matching the given keywords (via FYIND's
    `attributeSetName` search param), merges results, and returns a SLIM
    library: {productName: {"attribute_set_name": ..., "attribute_names": [...]}}

    Attribute VALUES are discarded -- only names are kept. This is the
    memory-safe replacement for get_attribute_library().

    Two safety nets against the prompt exploding (this hit 1.25M tokens
    once against Claude's 200K limit, before these caps existed):
      - max_pages_per_keyword caps how far we paginate a single keyword's
        results (default 2 pages x 50 = 100 rows per keyword max).
      - MAX_LIBRARY_ENTRIES caps the TOTAL merged size across all keywords
        combined -- stops pulling more once the cap is hit, regardless of
        how many keywords are left to search.
    """
    if config.use_mock():
        # In mock mode, just return whatever mock entries loosely match, or
        # everything if nothing matches (small dataset either way).
        matched = {
            k: v for k, v in _MOCK_ATTRIBUTE_LIBRARY.items()
            if any(kw.lower() in k.lower() for kw in keywords)
        }
        return matched or dict(_MOCK_ATTRIBUTE_LIBRARY)

    merged = {}
    for keyword in keywords:
        if len(merged) >= MAX_LIBRARY_ENTRIES:
            break  # hard stop -- already have enough, don't keep fetching

        kw_key = keyword.strip().lower()
        if not kw_key:
            continue

        now = time.time()
        cached = _search_cache.get(kw_key)
        if cached and (now - cached[1] < CACHE_TTL_SECONDS):
            merged.update(cached[0])
            continue

        kw_results = {}
        page = 1
        while page <= max_pages_per_keyword:
            resp = requests.get(
                ATTRIBUTE_ENDPOINT,
                params={"pageSize": page_size, "pageIndex": page, "attributeSetName": keyword},
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
                kw_results[pname] = {
                    "attribute_set_name": row.get("attributeSetName"),
                    "attribute_names": _parse_attribute_names(row.get("attributeSetValues")),
                }
                row["attributeSetValues"] = None  # drop the raw string ASAP, don't hold onto it

            total = rows[0].get("totalCount", len(rows))
            rows = None  # release this page's list now that we've extracted what we need
            if page * page_size >= total or len(kw_results) == 0:
                break
            page += 1

        _search_cache[kw_key] = (kw_results, now)
        merged.update(kw_results)

    # Final hard trim in case the last keyword's batch pushed us over.
    if len(merged) > MAX_LIBRARY_ENTRIES:
        merged = dict(list(merged.items())[:MAX_LIBRARY_ENTRIES])

    return merged


def _parse_attribute_names(raw) -> list[str]:
    """
    attributeSetValues comes back as a JSON-encoded string of
    {attrName: [values...] or value}. We only need the KEY NAMES -- values
    are discarded immediately, which is what keeps this memory-light even
    for the rows with huge/messy value strings.

    Capped at MAX_ATTRIBUTE_NAMES_PER_PRODUCT -- some rows have severely
    malformed data that parses into hundreds of spurious "names"; capping
    here is what actually stops those rows from blowing up prompt size.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return list(data.keys())[:MAX_ATTRIBUTE_NAMES_PER_PRODUCT]
    except (json.JSONDecodeError, TypeError):
        return _parse_legacy_packed_string_keys(raw)


def _parse_legacy_packed_string_keys(s: str) -> list[str]:
    import re
    if not isinstance(s, str) or not s:
        return []
    # Old packed format: "Attr1:val1, val2, Attr2:val1, ..." -- extract just
    # the attribute name tokens, never build the value lists at all.
    # Some rows (e.g. a "Flicker Machine Blade" row seen in testing) have
    # severely malformed data that matches hundreds of spurious "names" --
    # cap and bail out early rather than scanning/storing all of them.
    names = re.findall(r'(?:^|,\s*)([A-Za-z][A-Za-z0-9 /\.\-_]{0,30}?):', s)
    seen = []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.append(n)
        if len(seen) >= MAX_ATTRIBUTE_NAMES_PER_PRODUCT:
            break
    return seen


# ---------------------------------------------------------------------------
# ATTRIBUTE SETS -- full fetch (LEGACY / NOT used in normal request flow)
# ---------------------------------------------------------------------------

def get_attribute_library(force_refresh: bool = False) -> dict:
    """
    Fetches FYIND's ENTIRE attribute library (~9,048 products).
    NOT called anywhere in the normal classify flow anymore -- kept only as
    a manual/administrative utility. Calling this from a live web request
    on a small-memory host is exactly what caused the OOM crashes; if you
    need this for something (e.g. a nightly full-sync job), run it in a
    separate worker/script with adequate memory, not inline in a request.
    """
    if config.use_mock():
        return _MOCK_ATTRIBUTE_LIBRARY

    library = {}
    page = 1
    page_size = 200  # smaller pages than before -- less held in memory per HTTP round-trip
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
                "attribute_names": _parse_attribute_names(row.get("attributeSetValues")),
            }

        total = rows[0].get("totalCount", len(rows))
        rows = None
        if page * page_size >= total:
            break
        page += 1

    return library
