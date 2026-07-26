#!/usr/bin/env python
"""Upload Antek Automation letter templates to PostGrid.

Run once (or to refresh after HTML edits):
    .venv/bin/python scripts/create_postgrid_templates.py

Prints the template IDs; add them to .env:
    POSTGRID_TEMPLATE_UK=tmpl_...
    POSTGRID_TEMPLATE_US=tmpl_...

PostGrid dashboard: https://app.postgrid.com/templates
   — edit fonts, spacing, colours visually there after upload.
"""
import sys, os

# Resolve repo root so we can import src.*
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Load .env before importing config
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    pass  # python-dotenv optional; export vars manually

from src.clients import postgrid
from src.post.postgrid_html import uk_html, us_html


def upload(description: str, html: str) -> str:
    result = postgrid.create_template(description, html)
    tmpl_id = result.get("id", "?")
    status = result.get("status", "")
    print(f"  ✓  {description}")
    print(f"     id     = {tmpl_id}")
    print(f"     status = {status}")
    return tmpl_id


def main():
    key = os.environ.get("POSTGRID_API_KEY", "")
    if not key:
        print("ERROR: POSTGRID_API_KEY not set.")
        sys.exit(1)

    is_test = postgrid.is_test_key()
    print(f"\nPostGrid template upload — {'SANDBOX (test key)' if is_test else 'LIVE'}\n")

    uk_id = upload("Antek Automation — UK letter (A4)", uk_html())
    us_id = upload("Antek Automation — US letter (Letter)", us_html())

    print("\n── Add to your .env ──────────────────────────────────────")
    print(f"POSTGRID_TEMPLATE_UK={uk_id}")
    print(f"POSTGRID_TEMPLATE_US={us_id}")
    print("──────────────────────────────────────────────────────────\n")
    print("Then visit https://app.postgrid.com/templates to edit visually.")
    print("When the look is right, re-run postgrid-send — it will use the template IDs.")


if __name__ == "__main__":
    main()
