"""
Core logic: given extracted catalog content + the FYIND attribute library,
ask Claude to identify every distinct product and fill in every field the
upload-ready template needs -- but ONLY where the catalog actually states a
value. Nothing is invented.

Each product also gets a confidence score (0-100) for the Attribute Set
match, which excel_writer.py turns into "Confident" / "Needs review".
"""
import json
import anthropic
from app import config

_client = None


def _get_client():
    """Lazy init so a missing/invalid key doesn't crash the whole app at
    startup -- it only fails when someone actually tries to classify."""
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Add it as an environment "
                "variable (see .env.example)."
            )
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = f"""You are a product classification specialist for a B2B \
industrial marketplace (FYIND). You will be given:
1. Content extracted from a supplier's product catalog (text and/or images).
2. A library of "Product Names" from FYIND's database, each with its own \
defined attribute set (allowed attribute names for that product type).

Your job, for EVERY distinct product/item you find in the catalog, is to \
extract a full record with these fields:

- "item_description": clear, human-readable product name/description \
(REQUIRED -- skip the item entirely if you cannot determine this).
- "manufacturer": the manufacturer's name, ONLY if stated in the catalog \
for this specific item (not the supplier submitting the catalog).
- "place_of_origin": country/place of origin, ONLY if stated.
- "additional_information": notable features/description text for the \
item, ONLY if stated (keep it concise -- a sentence or short phrase).
- "supplier_status": one of "Manufacturer", "Distributor", "Exporter", or \
"Importer" ONLY if the catalog explicitly states which the supplier is. If \
not stated anywhere, leave this as null (the caller will default it).
- "price": the price, ONLY if stated (include currency symbol/code as shown).
- "matched_product_name": the single best-matching Product Name from the \
FYIND library, or null if no reasonable match exists.
- "confidence": your confidence (integer 0-100) that matched_product_name \
is the correct Product Name for this item. Use 100 only for an exact, \
unambiguous match. Use lower scores for approximate/generic matches, and a \
low score (e.g. 20-50) when you had to pick the "least wrong" option from a \
sparse library. If matched_product_name is null, set confidence to 0.
- "attributes": an ordered list of up to {config.MAX_ATTRIBUTES} \
{{"name": ..., "value": ...}} objects. Prioritize attribute names that DO \
exist in the matched Product Name's attribute set in the library, and ONLY \
include an attribute here if the catalog actually states a value for it \
-- never invent or guess a value, and never include an attribute the \
catalog doesn't mention. If the catalog states a relevant attribute that is \
NOT part of the matched Product Name's attribute set (e.g. catalog says \
"Finish: Powder Coated" but the library has no "Finish" attribute for this \
product), still include it in this same list (extra attributes beyond the \
library are allowed) -- just don't let extras crowd out attributes that ARE \
in the matched library set if you're at the {config.MAX_ATTRIBUTES}-item cap; \
prioritize library-matched attributes first, then extras.
- "brand": brand name for this item, ONLY if stated (distinct from \
manufacturer if the catalog distinguishes them; otherwise use the same value).
- "warranty": ONLY if stated.
- "guarantee": ONLY if stated.
- "return_exchange": return/exchange policy for this item, ONLY if stated.
- "notes": brief explanation if your Product Name match was approximate, \
ambiguous, or you had to make a judgment call. Empty string otherwise.

Rules:
- Never fabricate a value for any field. Leave it out (use null / empty \
list) if the catalog doesn't state it.
- If group-level specs apply to several pictured items (e.g. a shared \
Size/Material table under several product photos), apply those shared \
specs to each item individually.
- One JSON object per distinct product -- don't merge different items \
into one record, and don't split one item into duplicates.

Respond with ONLY a JSON object (no markdown fences, no preamble) of this shape:
{{
  "products": [
    {{
      "item_description": "...",
      "manufacturer": null,
      "place_of_origin": null,
      "additional_information": null,
      "supplier_status": null,
      "price": null,
      "matched_product_name": "Gate Valve",
      "confidence": 92,
      "attributes": [{{"name": "Size", "value": "1/2\\" to 12\\""}}],
      "brand": null,
      "warranty": null,
      "guarantee": null,
      "return_exchange": null,
      "notes": ""
    }}
  ]
}}
"""


def classify_catalog(extracted_content: dict, attribute_library: dict) -> list[dict]:
    """
    extracted_content: {"text": str, "images": [base64_png, ...]}
    attribute_library: dict from fyind_client.get_attribute_library()

    Returns a list of product dicts (see SYSTEM_PROMPT shape above).
    """
    # attribute_library is already slim (attribute_names only, no values) --
    # see fyind_client.search_attribute_sets(). Just pass it through as-is,
    # EXCEPT: defensive final backstop. fyind_client.py already caps this at
    # the source, but this is cheap insurance against any future change (or
    # config value) letting it through too large again -- we hit 1.25M
    # tokens once (Claude's limit is 200K) before the source-side caps
    # existed, purely from an oversized library JSON.
    slim_library = attribute_library
    MAX_LIBRARY_JSON_CHARS = 150_000  # ~35-40K tokens, leaves room for catalog content
    library_json = json.dumps(slim_library)
    if len(library_json) > MAX_LIBRARY_JSON_CHARS:
        trimmed = dict(list(slim_library.items())[: len(slim_library) // 2])
        while len(json.dumps(trimmed)) > MAX_LIBRARY_JSON_CHARS and len(trimmed) > 10:
            trimmed = dict(list(trimmed.items())[: len(trimmed) // 2])
        slim_library = trimmed

    content_blocks = []
    if extracted_content.get("text"):
        content_blocks.append({
            "type": "text",
            "text": f"CATALOG TEXT CONTENT:\n{extracted_content['text'][:80000]}"
        })
    for img_b64 in extracted_content.get("images", [])[:20]:  # safety cap
        content_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
        })
    content_blocks.append({
        "type": "text",
        "text": (
            "FYIND ATTRIBUTE LIBRARY (Product Name -> attribute_set_name, "
            "attribute_names):\n" + json.dumps(slim_library)
        ),
    })
    content_blocks.append({
        "type": "text",
        "text": "Now extract and classify every product per the instructions.",
    })

    response = _get_client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content_blocks}],
    )

    raw = "".join(b.text for b in response.content if b.type == "text")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude did not return valid JSON: {e}\nRaw output:\n{raw[:2000]}")

    products = parsed.get("products", [])
    # Defensive cleanup: cap attributes, clamp confidence
    for p in products:
        p["attributes"] = (p.get("attributes") or [])[: config.MAX_ATTRIBUTES]
        conf = p.get("confidence", 0)
        try:
            p["confidence"] = max(0, min(100, int(conf)))
        except (TypeError, ValueError):
            p["confidence"] = 0
    return products


CATEGORY_GUESS_PROMPT = """You are looking at content from a supplier's \
product catalog. Your ONLY job right now is to guess likely product \
CATEGORY keywords that could be used to search a database of product \
types -- NOT to identify specific products yet.

Look at the catalog content and list 4-8 short, general keywords that \
describe the KINDS of products in it (e.g. "pipe", "valve", "steel", \
"pvc", "gasket", "coupling", "fitting", "hose") -- broad category/material \
words, not specific product names or brand names.

Respond with ONLY a JSON object (no markdown fences, no preamble):
{"keywords": ["keyword1", "keyword2", ...]}
"""


def guess_categories(content: dict) -> list[str]:
    """
    Quick, cheap pass: given catalog content (text and/or a couple of
    images), asks Claude for broad category keywords -- used to fetch only
    a relevant slice of FYIND's attribute library via search_attribute_sets()
    instead of loading all ~9,000 product names into memory.
    """
    content_blocks = []
    if content.get("text"):
        content_blocks.append({"type": "text", "text": f"CATALOG CONTENT:\n{content['text'][:20000]}"})
    for img_b64 in content.get("images", [])[:5]:  # a few pages is plenty for a category guess
        content_blocks.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
        })
    if not content_blocks:
        return []
    content_blocks.append({"type": "text", "text": "List category keywords now."})

    response = _get_client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=500,
        system=CATEGORY_GUESS_PROMPT,
        messages=[{"role": "user", "content": content_blocks}],
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(raw)
        return [k for k in parsed.get("keywords", []) if isinstance(k, str) and k.strip()]
    except json.JSONDecodeError:
        return []


def classify_catalog_streaming(filepath: str, attribute_library: dict) -> list[dict]:
    """
    PDF-specific: classifies one page at a time (via extraction.iter_pdf_pages)
    instead of loading every page image into memory before calling Claude
    once. Trades a bit more total API cost/time (one Claude call per page
    instead of one call for the whole document) for a much smaller memory
    footprint -- only one rendered page image exists at a time.

    Pages with no meaningful content (no text, no image needed) are skipped
    to avoid wasting an API call on a blank/divider page.
    """
    from app import extraction  # local import avoids a circular import at module load time

    all_products = []
    for page_num, text, image_b64 in extraction.iter_pdf_pages(filepath):
        if not text.strip() and not image_b64:
            continue  # skip empty/blank pages -- no point calling Claude on nothing

        page_content = {"text": text, "images": [image_b64] if image_b64 else []}
        try:
            page_products = classify_catalog(page_content, attribute_library)
        except ValueError:
            # If Claude's response for this one page fails to parse, don't
            # let it kill the whole document -- skip this page and continue.
            continue

        all_products.extend(page_products)

    return all_products
