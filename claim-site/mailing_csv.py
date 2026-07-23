"""Export a mailing CSV for the postal print run: addressee, address, links.

Usage: uv run python claim-site/mailing_csv.py
Writes data/output/mailing-list.csv (one row per drafted letter).
"""
from __future__ import annotations
import csv, os, re, sys
sys.path.insert(0, "/data/.claudeclaw/agents/clawdineresearch/geo-prospecting")
os.chdir("/data/.claudeclaw/agents/clawdineresearch/geo-prospecting")
from src import db  # noqa: E402
from src.post import letter as L  # noqa: E402
from src.visibility.report import slugify  # noqa: E402

SITE = "https://go.antekautomation.com"
_PC = re.compile(r"([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})", re.I)

# US market = Florida county or a Tampa Bay / Miami town. Everything else = UK.
_US_TOWNS = {"tampa", "brandon", "palm harbor", "st. petersburg", "clearwater",
             "wesley chapel", "lutz", "riverview", "land o' lakes", "miami",
             "greater northdale", "jacksonville"}


def _country(co) -> str:
    if (co["county"] or "").strip().lower() == "florida":
        return "US"
    if (co["town"] or "").strip().lower() in _US_TOWNS:
        return "US"
    return "UK"


# Optional: `python mailing_csv.py --region us|uk` -> writes only that market
# to mailing-list-<region>.csv. No arg = full combined list (unchanged).
region = None
if "--region" in sys.argv:
    region = sys.argv[sys.argv.index("--region") + 1].strip().upper()

conn = db.get_connection()
rows = conn.execute(
    """SELECT c.*, l.claim_code, l.pdf_path
       FROM companies c JOIN letters l ON l.company_id = c.id
       WHERE l.letter_no = 1
       ORDER BY c.town, c.name"""
).fetchall()
if region:
    rows = [r for r in rows if _country(r) == region]

out_path = f"data/output/mailing-list-{region.lower()}.csv" if region else "data/output/mailing-list.csv"
cols = ["country", "business_name", "addressee", "salutation", "address", "postcode", "town",
        "phone", "website", "visibility_score", "top_competitor",
        "claim_url", "claim_code", "letter_pdf"]

with open(out_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for co in rows:
        addressee, salutation, _pid = L._addressee(conn, co)
        addr = co["registered_address"] or ""
        m = _PC.search(addr or "")
        postcode = m.group(1).upper() if m else ""
        v = db.latest_check(conn, co["id"])
        score = int(round(v["composite_score"])) if v else ""
        comp = (v["competitor_named"].split(",")[0].strip() if v and v["competitor_named"] else "")
        w.writerow([
            _country(co),
            co["name"], addressee, f"Dear {salutation},", addr, postcode, co["town"] or "",
            co["phone"] or "", co["website"] or "", score, comp,
            f"{SITE}/{co['slug'] or slugify(co['name'])}", co["claim_code"] or "",
            os.path.basename(co["pdf_path"] or ""),
        ])
conn.close()
print(f"wrote {len(rows)} rows -> {out_path}")
