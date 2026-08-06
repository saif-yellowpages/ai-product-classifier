"""
Central configuration. Everything here is read from environment variables,
so nothing secret ever lives in the code itself.

When deploying (e.g. on Render), set these as Environment Variables in the
dashboard. Locally, copy .env.example to .env and fill it in.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Anthropic (Claude) API ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

# --- FYIND APIs (real endpoints, per fyind_api.md) ---
FYIND_BASE_URL = os.getenv("FYIND_BASE_URL", "https://adminui-api.fyind.com")
FYIND_REGION = os.getenv("FYIND_REGION", "UAE")  # optional "Region" header

# --- Behavior toggles ---
# When true, uses local mock data instead of calling the real FYIND APIs.
# Useful for offline development / demoing without network access.
USE_MOCK_FYIND = os.getenv("USE_MOCK_FYIND", "false")


def use_mock() -> bool:
    return USE_MOCK_FYIND.strip().lower() == "true"


# --- File upload limits ---
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".webp", ".xlsx", ".csv"}

# Max attribute columns in the upload template (Attribute 1..6)
MAX_ATTRIBUTES = int(os.getenv("MAX_ATTRIBUTES", "6"))

# Confidence threshold (%) at/above which Review = "Confident", below = "Needs review"
CONFIDENCE_THRESHOLD = int(os.getenv("CONFIDENCE_THRESHOLD", "85"))

# Max PDF pages processed per catalog -- caps both memory use and API cost
# on very large documents.
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "40"))
