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


def _addressee(conn, company) -> tuple[str, str, int | None]:
    """Return (addressee_line, salutation, person_id). Directors first."""
    people = db.get_people_for_company(conn, company["id"])
    directors = [p for p in people if p["person_source"] == "companies_house_officer" and p["name"]]
    named = directors or [p for p in people if p["name"]]
    if named:
        p = named[0]
        surname = p["name"].split()[-1]
        return p["name"], f"Mr/Ms {surname}", p["id"]
    return "The Owner", "Sir or Madam", None


def _delivery_address(company) -> str:
    """Ltd: registered office. Sole trader: Places address if we have one."""
    if company["company_type"] == "ltd" and company["registered_address"]:
        return company["registered_address"]
    return company["registered_address"] or ""


def build_letter(conn, company, letter_no: int = 1) -> dict:
    """Generate a letter PDF + letters row. letter_no 2 = shorter follow-up."""
    check = db.latest_check(conn, company["id"])
    if check is None:
        raise ValueError(
            f"No visibility check for {company['name']}. Run `cli check mini` first."
        )
    addressee, salutation, person_id = _addressee(conn, company)
    code = _claim_code(conn)
    claim_url = f"{config.CLAIM_BASE_URL}/{code}"
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
        headline=check["headline_finding"] or "",
        sector_word=sector_word,
        claim_url=claim_url,
        qr_data_uri=_qr_data_uri(claim_url),
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


def draft_letters_for_post(limit: int = 25, dry_run: bool = False) -> list[dict]:
    """Draft first letters for post-channel companies that have a check and no
    letter yet."""
    conn = db.get_connection()
    out: list[dict] = []
    try:
        rows = conn.execute(
            """SELECT c.* FROM companies c
               WHERE c.channel = 'post' AND c.status NOT IN ('closed_lost','client')
                 AND EXISTS (SELECT 1 FROM visibility_checks v WHERE v.company_id = c.id)
                 AND NOT EXISTS (SELECT 1 FROM letters l WHERE l.company_id = c.id)
               ORDER BY COALESCE(c.pitchability_score, 0) DESC, c.id LIMIT ?""",
            (limit,),
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
