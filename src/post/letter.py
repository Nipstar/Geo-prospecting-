"""Postal letter generator for companies routed to 'post'.

Renders a one-page branded letter (Antek system) addressed to a named director
(Ltd) or the proprietor / "The Owner" (sole trader), carrying the same headline
finding as touch 2 plus a unique QR code and short URL to claim the free check.
"""
from __future__ import annotations

import base64
import io
import secrets
import string
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config, db
from ..reports import brand
from ..visibility.report import slugify

TEMPLATE_DIR = Path(__file__).parent / "templates"
_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _claim_code(conn) -> str:
    """Short unique code, unambiguous characters, checked against the table."""
    alphabet = _CODE_ALPHABET.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if db.get_letter_by_code(conn, code) is None:
            return code
    raise RuntimeError("Could not allocate a unique claim code.")


def _qr_data_uri(url: str) -> str:
    try:
        import qrcode
    except ImportError:
        return ""
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


import gender_guesser.detector as _gender_mod

_DETECTOR = _gender_mod.Detector(case_sensitive=False)


def _salutation(full_name: str) -> str:
    """Grammatical greeting: 'Mr Smith' / 'Ms Jones', or 'Sir or Madam' when the
    forename's gender can't be determined. Never 'Mr/Ms'."""
    tokens = [t for t in full_name.split() if t]
    if not tokens:
        return "Sir or Madam"
    surname = tokens[-1]
    lower = {t.lower().strip(".,") for t in tokens}
    title = None
    if "kaur" in lower:            # Sikh female name marker
        title = "Ms"
    elif "singh" in lower:         # Sikh male name marker
        title = "Mr"
    else:
        g = _DETECTOR.get_gender(tokens[0])
        if g in ("female", "mostly_female"):
            title = "Ms"
        elif g in ("male", "mostly_male"):
            title = "Mr"
    return f"{title} {surname}" if title else "Sir or Madam"


def _opener(company, check, sector_word: str) -> str:
    """A clean, grammatical opening finding built from the data (not the terse
    mini-check headline)."""
    town = company["town"] or "your area"
    mentioned = check["platforms_mentioned"] or 0
    tested = check["platforms_tested"] or 0
    comp = (check["competitor_named"] or "").split(",")[0].strip()
    if mentioned == 0:
        line = (f"When people in {town} ask an AI tool like ChatGPT for a {sector_word}, "
                f"{company['name']} does not appear at all across the {tested} engines I checked")
    else:
        line = (f"When people in {town} ask an AI tool like ChatGPT for a {sector_word}, "
                f"{company['name']} appears in only {mentioned} of the {tested} engines I checked")
    line += f", while {comp} appears in more." if comp else "."
    return line


def _addressee(conn, company) -> tuple[str, str, int | None]:
    """Return (addressee_line, salutation, person_id). Directors first."""
    people = db.get_people_for_company(conn, company["id"])
    _official = {"companies_house_officer", "sunbiz_officer", "linkedin"}
    directors = [p for p in people if p["person_source"] in _official and p["name"]]
    named = directors or [p for p in people if p["name"]]
    if named:
        p = named[0]
        return p["name"], _salutation(p["name"]), p["id"]
    return "The Owner", "Sir or Madam", None


def _delivery_address(company) -> str:
    """Ltd: registered office. Sole trader: Places address if we have one."""
    if company["company_type"] == "ltd" and company["registered_address"]:
        return company["registered_address"]
    return company["registered_address"] or ""


_US_TOWNS = {"tampa", "brandon", "palm harbor", "st. petersburg", "clearwater",
             "wesley chapel", "lutz", "riverview", "land o' lakes", "miami",
             "greater northdale", "jacksonville"}


def _market(company) -> str:
    """US letters drop the UK Andover return address + reframe the intro so a
    US recipient doesn't wonder why UK mail landed. Everything else = UK."""
    if (company["county"] or "").strip().lower() == "florida":
        return "US"
    try:
        town = (company["town"] or "").strip().lower()
    except (IndexError, KeyError):
        town = ""
    return "US" if town in _US_TOWNS else "UK"


def build_letter(conn, company, letter_no: int = 1) -> dict:
    """Generate a letter PDF + letters row. letter_no 2 = shorter follow-up."""
    check = db.latest_check(conn, company["id"])
    if check is None:
        raise ValueError(
            f"No visibility check for {company['name']}. Run `cli check mini` first."
        )
    addressee, salutation, person_id = _addressee(conn, company)
    code = _claim_code(conn)  # kept for per-letter tracking in the letters table
    # Letters link to the live personalised claim page. Use the short stable slug.
    import os
    claim_site = os.getenv("CLAIM_SITE_URL", "https://antek-claim.pages.dev").rstrip("/")
    slug = (company["slug"] if "slug" in company.keys() and company["slug"] else slugify(company["name"]))
    claim_url = f"{claim_site}/{slug}"
    sector_word = None
    try:
        sector_word = company["primary_service"]
    except (IndexError, KeyError):
        pass
    sector_word = sector_word or company["sector"] or "businesses"

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("letter.html.j2")
    html = template.render(
        font_faces=brand.font_face_css(),
        base_css=brand.base_css(),
        tokens=brand.TOKENS,
        company=company,
        addressee=addressee,
        salutation=salutation,
        delivery_address=_delivery_address(company),
        date_str=date.today().strftime("%d %B %Y"),
        headline=_opener(company, check, sector_word),
        sector_word=sector_word,
        claim_url=claim_url,
        qr_data_uri=_qr_data_uri(claim_url),
        market=_market(company),
    )

    suffix = "-followup" if letter_no == 2 else ""
    out_path = config.LETTERS_DIR / f"{slugify(company['name'])}{suffix}.pdf"
    _render_pdf(html, out_path)

    letter_id = db.insert_letter(
        conn, company["id"], person_id=person_id, letter_no=letter_no,
        claim_code=code, pdf_path=str(out_path), status="drafted",
    )
    return {"letter_id": letter_id, "pdf_path": str(out_path), "claim_code": code,
            "claim_url": claim_url, "addressee": addressee}


def _render_pdf(html: str, out_path: Path) -> None:
    from weasyprint import HTML

    out_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf(str(out_path))


def draft_letters_for_post(limit: int = 25, dry_run: bool = False,
                           max_score: float | None = None) -> list[dict]:
    """Draft first letters for post-channel companies that have a check and no
    letter yet.

    max_score: skip companies whose latest visibility score is above this — a
    firm already visible in AI (e.g. 70+) is a poor fit for a "you're invisible"
    letter and is likely a competitor named in everyone else's opener.
    """
    conn = db.get_connection()
    out: list[dict] = []
    try:
        db.ensure_slugs(conn)
        rows = conn.execute(
            """SELECT c.* FROM companies c
               WHERE c.channel = 'post' AND c.status NOT IN ('closed_lost','client')
                 AND EXISTS (SELECT 1 FROM visibility_checks v WHERE v.company_id = c.id)
                 AND NOT EXISTS (SELECT 1 FROM letters l WHERE l.company_id = c.id)
                 AND (?1 IS NULL OR COALESCE(
                     (SELECT v.composite_score FROM visibility_checks v
                      WHERE v.company_id = c.id ORDER BY v.id DESC LIMIT 1), 0) <= ?1)
               ORDER BY COALESCE(c.pitchability_score, 0) DESC, c.id LIMIT ?2""",
            (max_score, limit),
        ).fetchall()
        for company in rows:
            if dry_run:
                print(f"  would draft letter: {company['name']}")
                out.append({"company": company["name"]})
                continue
            res = build_letter(conn, company, letter_no=1)
            print(f"  + letter {res['letter_id']}: {company['name']} -> {res['addressee']} [{res['claim_code']}]")
            out.append(res)
    finally:
        conn.close()
    return out


def draft_followup(company_id: int) -> dict:
    conn = db.get_connection()
    try:
        company = db.get_company(conn, company_id)
        return build_letter(conn, company, letter_no=2)
    finally:
        conn.close()
