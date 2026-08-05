"""Postal letter generator for companies routed to 'post'.

Renders a one-page branded letter (Antek system) addressed to a named director
(Ltd) or the proprietor / "The Owner" (sole trader), carrying the same headline
finding as touch 2 plus a unique QR code and short URL to claim the free check.
"""
from __future__ import annotations

import base64
import io
import re
import secrets
import string
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config, db, franchises
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

# Words that indicate a business descriptor rather than a surname.
# Used to detect "Jose Fuentes Real Estate Broker" → "Jose Fuentes".
_BUSINESS_WORDS = frozenset({
    # Business descriptors
    "real", "realty", "realtors", "estate", "estates",
    "property", "properties", "homes", "home", "house", "housing",
    "broker", "brokers", "brokerage",
    "group", "team", "associates", "associate", "partners", "partnership",
    "services", "solutions", "management", "consulting", "advisors",
    "agency", "agencies", "international", "national", "global",
    "residential", "commercial", "investment", "investments",
    "llc", "llp", "ltd", "inc", "corp", "co",
    # Location / place words (to prevent "Victoria Station" → false positive)
    "station", "plaza", "bridge", "park", "street", "lane", "avenue",
    "road", "square", "bay", "hill", "lake", "river", "valley",
    "manor", "hall", "court", "view", "heights", "ridge", "grove",
    "point", "port", "harbor", "harbour", "beach", "shore", "cliff",
    "gate", "tower", "place", "center", "centre", "village", "town",
})


def _extract_name_from_company(company_name: str) -> str | None:
    """Heuristic: if a company name begins with FirstName Surname <business word>,
    return 'FirstName Surname'.  Fires only when no person is found in the DB.

    Examples:
      "Jose Fuentes Real Estate Broker…" → "Jose Fuentes"
      "Sarah Johnson Realty LLC"          → "Sarah Johnson"
      "Hampton Real Estate Group"         → None  (Hampton has no detectable gender)
      "Victoria Station Properties"       → None  (Station is not a surname here
                                                    because 3rd token is in _BUSINESS_WORDS
                                                    BUT 2nd token also matches → depends)
    """
    tokens = [t.rstrip(",.;:'\"") for t in company_name.split() if t]
    if len(tokens) < 3:
        return None
    first, second, third = tokens[0], tokens[1], tokens[2]
    # First token must look like a human first name
    g = _DETECTOR.get_gender(first)
    if g not in ("male", "female", "mostly_male", "mostly_female"):
        return None
    # Second token must NOT be a business keyword (so it's a surname, not a descriptor)
    if second.lower() in _BUSINESS_WORDS:
        return None
    # Third token (or beyond) should be a business word → confirms this is a named firm
    if third.lower() not in _BUSINESS_WORDS:
        return None
    return f"{first} {second}"


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


_NAME_TAGLINE_SEPS = (
    " | ", " / ", " - ", " – ", " — ",
    " with ", " at ", " @ ",
    " powered by ", " Powered by ", " Powered By ",
    " brokered by ", " Brokered by ",
    " in ", " In ",
)


def clean_display_name(raw: str) -> str:
    """Strip Google-Places-style marketing taglines off a recipient-facing
    business name, keeping the real name/brand.

    Places listing titles often stack the actual business name with SEO
    taglines, city/area mentions, and brokerage affiliations behind a
    separator, e.g. "Alena Nicole Kolyadchik, LLC / English - Russian
    speaking Realtor(R) in Orlando", "David Freed Realtor | Miami Is Home |
    Keller Williams Realty", "Jac Smith Group with Keller Williams Realty
    St. Pete", "Chris Rogers Realtor - Home Dream Team Clearwater".

    Truncating at the EARLIEST occurrence of any spaced separator keeps the
    real name/team and drops the tail. All separators require surrounding
    spaces, so a brand's own unspaced character sequence is never touched
    (e.g. "RE/MAX", "LLC/KW St Pete"). Verified safe against the full FL
    dataset — the one case that over-truncates ("RE/MAX - Martha Loss...")
    is a franchise office already excluded from lettering entirely.

    A trailing separator remnant (comma/dash/ampersand left dangling after
    truncation) is trimmed, but a trailing PERIOD is never touched — it
    legitimately ends abbreviations like "Inc.", "Co.", "P.A.".
    """
    name = (raw or "").strip()
    if not name:
        return name
    earliest: int | None = None
    for sep in _NAME_TAGLINE_SEPS:
        idx = name.find(sep)
        if idx != -1 and (earliest is None or idx < earliest):
            earliest = idx
    if earliest is not None:
        candidate = name[:earliest].strip()
        if len(candidate) >= 4:      # don't truncate down to near-nothing
            name = candidate
    name = re.sub(r"[,;:&\-–—\s]+$", "", name).strip()
    name = _strip_realtor_suffix(name)
    return name


_DANGLING_WORDS = {"of", "for", "in", "the", "a", "an", "with", "by", "at", "and", "to"}


def _strip_realtor_suffix(name: str) -> str:
    """Drop a trailing 'Realtor'/'Realtors'/'REALTOR®' credential — redundant
    in a letter that already says "...ask for a real estate agent...".

    Repeats until stable (handles "Realtor ®" and similar double-tails), then
    re-applies the same trailing-punctuation trim as the caller. Guarded: if
    stripping would leave a dangling preposition/article ("Association OF
    Realtors" -> "Association of"), the word was grammatically load-bearing,
    not a decorative suffix — the original is kept.
    """
    original = name
    prev = None
    while prev != name:
        prev = name
        name = re.sub(r"[,\s]*realtors?\.?\s*[®™]?\s*$", "", name, flags=re.I).strip()
        name = re.sub(r"[®™]\s*$", "", name).strip()
        name = re.sub(r"[,;:&/\-–—\s]+$", "", name).strip()
    last_word = name.split()[-1].lower().rstrip(".,") if name else ""
    if last_word in _DANGLING_WORDS or len(name) < 4:
        return original
    return name


def _opener(company, check, sector_word: str) -> str:
    """A clean, grammatical opening finding built from the data (not the terse
    mini-check headline)."""
    town = company["town"] or "your area"
    mentioned = check["platforms_mentioned"] or 0
    tested = check["platforms_tested"] or 0
    comp = (check["competitor_named"] or "").split(",")[0].strip()
    name = clean_display_name(company["name"])
    if mentioned == 0:
        line = (f"When people in {town} ask an AI tool like ChatGPT for a {sector_word}, "
                f"{name} does not appear at all across the {tested} engines I checked")
    else:
        line = (f"When people in {town} ask an AI tool like ChatGPT for a {sector_word}, "
                f"{name} appears in only {mentioned} of the {tested} engines I checked")
    line += f", while {comp} appears in more of them." if comp else "."
    return line


def _pluralise(word: str) -> str:
    """Plural of a service noun for 'how <town> <nouns> show up'. Idempotent:
    already-plural nouns (solicitors) are left alone; 'real estate agency' ->
    'real estate agencies'."""
    w = (word or "").strip()
    if not w or w.lower().endswith("s"):
        return w
    if w.lower().endswith("y") and w[-2:-1].lower() not in "aeiou":
        return w[:-1] + "ies"
    if w.lower().endswith(("ch", "sh", "x", "z")):
        return w + "es"
    return w + "s"


def _addressee(conn, company) -> tuple[str, str, int | None]:
    """Return (addressee_line, salutation, person_id). Directors first.

    Fallback chain:
      1. Official DB record (Companies House officer, Sunbiz, LinkedIn)
      2. Any person record in the DB
      3. Heuristic: name embedded in company name ("Jose Fuentes Real Estate…")
      4. "The Owner" / "Sir or Madam"
    """
    people = db.get_people_for_company(conn, company["id"])
    _official = {"companies_house_officer", "sunbiz_officer", "linkedin"}
    directors = [p for p in people if p["person_source"] in _official and p["name"]]
    named = directors or [p for p in people if p["name"]]
    if named:
        p = named[0]
        return p["name"], _salutation(p["name"]), p["id"]
    # Heuristic: extract person name from company name (e.g. named real-estate firms)
    extracted = _extract_name_from_company(company["name"] or "")
    if extracted:
        return extracted, _salutation(extracted), None
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


def render_letter_html(conn, company, letter_no: int = 1,
                       stamped_address: bool = False) -> tuple[str, dict]:
    """Render the branded letter HTML + metadata. `stamped_address=True` hides
    the in-body sender/addressee blocks (for PostGrid, which stamps its own
    address page). Does not touch the DB or write a PDF."""
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
        sector_word_plural=_pluralise(sector_word),
        claim_url=claim_url,
        qr_data_uri=_qr_data_uri(claim_url),
        market=_market(company),
        stamped_address=stamped_address,
    )
    meta = {"person_id": person_id, "claim_code": code, "claim_url": claim_url,
            "addressee": addressee, "salutation": salutation, "slug": slug,
            "market": _market(company)}
    return html, meta


def build_merge_variables(conn, company) -> tuple[dict, dict]:
    """Build the PostGrid ``mergeVariables`` dict for a company letter.

    Returns ``(merge_vars, meta)`` where:
      - ``merge_vars``   goes to ``create_letter(merge=merge_vars)``
      - ``meta``         has the same keys as ``render_letter_html`` (for DB write)

    This is the PostGrid-template path: the HTML lives in the portal and PostGrid
    substitutes ``{{salutation}}``, ``{{headline}}``, etc.  No local Jinja2 render.
    """
    check = db.latest_check(conn, company["id"])
    if check is None:
        raise ValueError(
            f"No visibility check for {company['name']}. Run `cli check mini` first."
        )
    addressee, salutation, person_id = _addressee(conn, company)
    code = _claim_code(conn)
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

    market = _market(company)
    town = (company["town"] or "your area")

    # addresseeName: shown in the recipient address block on the letter
    #   → named person: their full name (e.g. "Mr John Smith")
    #   → unknown:      "The Owner"
    # salutation: used in "Dear X," — never "Dear The Owner,"
    #   → named with detectable gender: "Mr Smith" / "Ms Jones"
    #   → unknown:                      "Sir or Madam"
    if addressee and addressee != "The Owner":
        addressee_name = addressee          # full name already formatted
        dear = _salutation(addressee)       # "Mr Smith" / "Ms Jones" / "Sir or Madam"
    else:
        addressee_name = "The Owner"
        dear = "Sir or Madam"

    merge = {
        "addresseeName": addressee_name,
        "salutation": dear,
        "headline": _opener(company, check, sector_word),
        "town": town,
        "sector": _pluralise(sector_word),  # always plural — "how X solicitors show up"
        "claimUrl": claim_url,
    }
    meta = {
        "person_id": person_id, "claim_code": code, "claim_url": claim_url,
        "addressee": addressee_name, "salutation": dear, "slug": slug,
        "market": market,
    }
    return merge, meta


def build_letter(conn, company, letter_no: int = 1) -> dict:
    """Generate a letter PDF + letters row. letter_no 2 = shorter follow-up."""
    html, meta = render_letter_html(conn, company, letter_no, stamped_address=False)
    suffix = "-followup" if letter_no == 2 else ""
    out_path = config.LETTERS_DIR / f"{slugify(company['name'])}{suffix}.pdf"
    _render_pdf(html, out_path)

    letter_id = db.insert_letter(
        conn, company["id"], person_id=meta["person_id"], letter_no=letter_no,
        claim_code=meta["claim_code"], pdf_path=str(out_path), status="drafted",
    )
    return {"letter_id": letter_id, "pdf_path": str(out_path),
            "claim_code": meta["claim_code"], "claim_url": meta["claim_url"],
            "addressee": meta["addressee"]}


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
                      WHERE v.company_id = c.id ORDER BY v.id DESC LIMIT 1), 0) < ?1)
               ORDER BY COALESCE(c.pitchability_score, 0) DESC, c.id LIMIT ?2""",
            (max_score, limit),
        ).fetchall()
        for company in rows:
            # Defensive last-line check — franchises should already be diverted
            # at routing, but a franchise office can still end up with
            # channel='post' from stale data or a name found some other way
            # (business-name-is-a-person heuristic, DBPR). Standing rule is
            # never to letter a franchise, so refuse here too.
            if franchises.is_franchise(company["name"]):
                print(f"  x skip (franchise): {company['name']}")
                continue
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
