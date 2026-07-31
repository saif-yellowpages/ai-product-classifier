# FYIND Product Classifier

Pick a supplier → upload their product catalog (PDF, Word, image, or Excel) →
get back the real FYIND upload-ready Excel file, filled in and classified
against FYIND's live attribute set library.

## Status: wired to real FYIND APIs

Per `fyind_api.md`, this now calls the real endpoints:
- `GET https://adminui-api.fyind.com/api/Admin/Supplier/catalogue/suppliers`
- `GET https://adminui-api.fyind.com/api/Admin/AttributeSet/api/GetAttributesPaginated`

Both are paginated; `app/fyind_client.py` pages through all results and
caches them in memory for 15 minutes (attribute library in particular can be
large). No API key is required for these per the docs you sent — just the
standard headers, which are already set.

**Note:** I could not reach `adminui-api.fyind.com` from my own sandboxed dev
environment (network allowlist), so pagination/parsing logic is verified
against simulated responses matching the exact shapes in `fyind_api.md`, but
not against the live API. Run it locally with `USE_MOCK_FYIND=false` and
watch the first request closely — if FYIND's real response shape differs
even slightly from the docs (e.g. a nested `{"data": [...]}` wrapper, or
`attributeSetValues` sometimes not valid JSON), `app/fyind_client.py` is the
one file to adjust. It already has a fallback parser for the old
packed-string attribute format, just in case.

## The export format

`templates_xlsx/upload_template.xlsx` is your real sample file — the app
writes directly into a copy of it, so every column, header spelling
(including the trailing space in "Video File Name "), and column order
matches exactly. Populated columns, per your spec:

| Column | Source |
|---|---|
| Item Description | Catalog (mandatory — item skipped if Claude can't determine this) |
| Manufacturer | Catalog, if stated |
| Place Of Origin | Catalog, if stated |
| Additional Information | Catalog features/description, if stated |
| Supplier | Your dropdown selection |
| Supplier Status | Catalog (Manufacturer/Distributor/Exporter/Importer), else defaults to "Suppliers" |
| Price | Catalog, if stated |
| Attribute Set | Best-matching FYIND `attributeSetName` |
| Attribute 1–6 | `"AttributeName:Value"` — only attributes actually stated in the catalog, up to 6, library-matched ones prioritized over extras |
| Brand | Catalog, if stated |
| Warranty / Guarantee / Return & Exchange | Catalog, if stated |
| Status | Always "Active" |
| **Confidence** *(new, appended)* | Claude's confidence % in the Attribute Set match |
| **Review** *(new, appended)* | "Confident" if Confidence ≥ 85%, else "Needs review" |

All other template columns (Listing Code, Item Code, Relationship, Images,
Featured Image, Video File Name, Speciality Tags, Qoh, Datasheets, Stocklist,
Short Description, Clearance, Sku) are left blank, per your instructions.

**Assumption worth double-checking with your team:** I formatted each
Attribute N cell as `"Name:Value"` (e.g. `Size:1/2" to 12"`), matching the
internal format FYIND already uses for `attributeSetValues`. If your upload
system instead expects just the raw value with the name implied by column
position, or a different separator, tell me and it's a one-line change in
`app/excel_writer.py`.

## Local setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: add your ANTHROPIC_API_KEY (get one at console.anthropic.com)
# NEVER commit .env or share it -- it holds your real key.
```

⚠️ **Your Anthropic key needs credits.** When I tested it, authentication
succeeded but the request failed with "credit balance is too low." Add
credits at console.anthropic.com → Plans & Billing before running real
classification requests.

```bash
uvicorn app.main:app --reload
```

Visit http://localhost:8000. With `USE_MOCK_FYIND=false` (the default) it
will call the real FYIND APIs; set it to `true` in `.env` if you want to
demo the flow without hitting FYIND (uses a small built-in sample library).

## Deploying to Render (free tier)

1. Push this folder to a GitHub repo (`.gitignore` already excludes `.env`
   and generated files — double check nothing secret gets committed).
2. On [render.com](https://render.com), New → Blueprint → connect the repo
   (reads `render.yaml` automatically), or New → Web Service manually with:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. In the service's **Environment** tab, add `ANTHROPIC_API_KEY` (required).
   FYIND settings can stay at their defaults unless FYIND gives you a
   different base URL.
4. Deploy. Free tier spins down after ~15 min idle, ~30-60s to wake on the
   next request — fine for internal team tool use.

### Costs & limits to keep in mind
- Each classify request costs a small amount on your Anthropic account
  (roughly $0.01–0.10 depending on catalog size — image-heavy PDFs cost
  more than plain text).
- Free Render disk is ephemeral — fine here since files are generated
  per-request and downloaded immediately.
- `MAX_UPLOAD_MB` (20MB default) and the 30-page image-render cap in
  `extraction.py` are safety limits — raise them for bigger catalogs.
- The attribute library is cached 15 minutes in memory (`CACHE_TTL_SECONDS`
  in `fyind_client.py`) to avoid re-fetching it on every single upload.

## What to watch for on the first real run

1. **Supplier dropdown** — confirm names/IDs look right. If FYIND's response
   shape differs from `fyind_api.md`'s example, the dropdown will come back
   empty or with `None` names; check `app/fyind_client.py`'s `get_suppliers()`.
2. **Attribute Set matches** — spot-check a handful of Confidence scores
   against your own judgment. If Claude is consistently over- or
   under-confident, we can tune the prompt in `app/classifier.py`.
3. **Attribute 1–6 formatting** — confirm the `"Name:Value"` format is what
   your upload system expects (see assumption note above).
