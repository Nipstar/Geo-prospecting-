"""Website owner enrichment (US) — the primary owner-finder for small firms.

B2B databases (Apollo/Prospeo) only resolve ~10% of small local realtors: solo
brokers aren't in corporate staff datasets. Their own website almost always
names them (About/Team page, or the business name itself), so we scrape that.
Measured ~80% hit-rate on real FL realtor domains at ~£0.

Pipeline per company (US-scoped):
  1. business-name-is-a-person heuristic (free, instant)
  2. fetch homepage + about/team pages -> LLM extract owner {name, title}
  3. scrape a public contact email (owner-matched preferred over generic)

Email is scraped only for US rows (the cold-email channel). Names are stored for
everyone. Verified email reveal (Prospeo/Apollo) is a separate, later step.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests

from .. import config, db, franchises

_UA = {"User-Agent": "Mozilla/5.0 (compatible; AntekBot/1.0)"}
_ABOUT_PATHS = ["/about", "/about-us", "/our-agents", "/team", "/meet-the-team",
                "/our-team", "/agents", "/staff", "/contact"]
_US_COUNTIES = {"Florida", "Texas", "FL", "TX"}

# Words that mean the business name is NOT a personal name.
_COMPANY_WORDS = re.compile(
    r"\b(realty|real estate|group|team|homes?|properties|associates?|"
    r"brokerage|llc|inc|pa|co|company|agency|partners?|international|"
    r"solutions?|services?|realtors?)\b", re.I)
_GENERIC_LOCAL = {"info", "contact", "admin", "hello", "office", "sales",
                  "team", "support", "help", "enquiries", "inquiries", "mail"}
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_EMAIL_JUNK = re.compile(r"\.(png|jpg|jpeg|gif|svg|webp)$|sentry|wixpress|"
                         r"example\.(com|org)|domain\.com|@(?:sentry|godaddy|squarespace|sentry\.io)|"
                         r"\.(wixpress|w3\.org)", re.I)
# Placeholder local-parts that appear in theme demos, never real contacts.
_EMAIL_PLACEHOLDER = {"sample", "your", "youremail", "name", "yourname", "email",
                      "test", "firstname", "lastname", "john", "jane", "user",
                      "example", "demo", "noreply", "no-reply"}


def _domain(website: str | None) -> str | None:
    if not website:
        return None
    host = urlparse(website if "//" in website else f"http://{website}").netloc
    host = host.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else (host or None)


def _person_from_name(business: str) -> str | None:
    """If the business name is essentially a person's name, return it."""
    # Strip trailing role/suffix after comma/pipe: "Louis Urbina, PA - Realtor".
    head = re.split(r"[,|]", business)[0].strip()
    if _COMPANY_WORDS.search(head):
        return None
    toks = [t for t in re.split(r"\s+", head) if t]
    if len(toks) in (2, 3) and all(re.match(r"^[A-Z][a-zA-Z.'-]+$", t) for t in toks):
        return head
    return None


def _fetch(url: str) -> str:
    try:
        r = requests.get(url, headers=_UA, timeout=12)
        return r.text if r.status_code < 400 else ""
    except Exception:  # noqa: BLE001
        return ""


def _fetch_pw(url: str, page) -> str:
    """Render a JS/anti-bot page with a live browser (phase-2)."""
    try:
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        return page.content()
    except Exception:  # noqa: BLE001
        return ""


def _visible_text(html: str) -> str:
    html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _extract_owner(business: str, text: str) -> dict | None:
    """LLM-extract the owner/principal from about-page text (gpt-4o-mini)."""
    if not config.OPENROUTER_API_KEY or len(text) < 40:
        return None
    prompt = (
        "From this real-estate business's website text, identify the single "
        "person to address a letter to: the owner, broker/principal, founder, "
        "OR — if it's a team/group — the team leader (often the individual the "
        "team is named after, e.g. 'The Crawford Team' -> the lead named "
        "Crawford, 'McGuire Home Team, Rob McGuire' -> Rob McGuire). "
        f"Business name: {business}. Text: {text[:4000]} "
        'Reply ONLY compact JSON: {"name":"Full Name or null","title":"role or null"}. '
        "Give a real FULL name (first + last) or null — never a surname alone, "
        "never a generic label. Use null if no specific individual is named."
    )
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "openai/gpt-4o-mini", "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=45)
        c = r.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", c, re.S)
        data = json.loads(m.group(0)) if m else None
    except Exception:  # noqa: BLE001
        return None
    if not data:
        return None
    name = (data.get("name") or "").strip()
    if not name or name.lower() in ("null", "none", "n/a"):
        return None
    # Reject if the model wrote a real name plus a literal "null"/"none" token
    # for the part it didn't know (e.g. "Laura null") — the whole-string check
    # above only catches an all-null value, not this partial-hallucination case.
    _junk_tokens = {"null", "none", "n/a", "na", "unknown", "undefined"}
    tokens = [t for t in re.split(r"\s+", name) if len(t) > 1]
    if any(t.lower() in _junk_tokens for t in tokens):
        return None
    # Require a real full name (first + last), not a bare surname or label.
    if len(tokens) < 2:
        return None
    title = (data.get("title") or "").strip()
    if title.lower() in _junk_tokens:
        title = ""
    return {"name": name, "title": title or None}


def _emails(htmls: list[str]) -> list[str]:
    found: list[str] = []
    for h in htmls:
        for m in re.findall(r'mailto:([^"\'?>]+)', h):
            found.append(m.strip())
        found.extend(_EMAIL_RE.findall(h))
    out: list[str] = []
    seen = set()
    for e in found:
        # Strip URL-encoding tails and trailing punctuation/quotes/backslashes.
        e = e.strip().lower().split("?")[0].rstrip("\\\"'<>).,;:")
        if not _EMAIL_RE.match(e) or e in seen or _EMAIL_JUNK.search(e):
            continue
        if e.split("@")[0] in _EMAIL_PLACEHOLDER:
            continue
        seen.add(e)
        out.append(e)
    return out


def _pick_email(emails: list[str], owner: str | None, domain: str | None) -> str | None:
    """Owner-matched > personal > generic. Same-domain preferred."""
    if not emails:
        return None
    names = {t.lower() for t in re.split(r"\s+", owner or "") if len(t) > 2}

    def score(e: str) -> int:
        local, _, host = e.partition("@")
        s = 0
        if domain and domain in host:
            s += 2
        if names & set(re.split(r"[._-]", local)):
            s += 5           # local part contains owner name
        if local in _GENERIC_LOCAL:
            s -= 3           # info@, contact@ ...
        return s

    best = max(emails, key=score)
    return best if score(best) > -3 else None


def run_web_owner_enrich(limit: int = 50, state: str | None = None,
                         town: str | None = None, scrape_email: bool = True,
                         dry_run: bool = False, sleep: float = 0.5,
                         render_js: bool = False) -> dict[str, int]:
    conn = db.get_connection()
    db.ensure_person_contact(conn)

    # Optional phase-2: a headless browser for JS/anti-bot sites that return
    # nothing to plain requests. `fetch(url)` transparently uses it when on.
    _pw = _browser = _page = None
    if render_js:
        import os
        # The container images ship a read-only /ms-playwright whose chromium
        # build may not match our pinned Playwright (index max is 1.61 → build
        # 1228; some images pre-bake a newer build). `playwright install
        # chromium` puts a build-matched browser in the writable shared cache;
        # prefer that so the launch does not fail on a missing executable.
        _shared = os.path.expanduser("~/.cache/ms-playwright")
        if os.path.isdir(_shared):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _shared
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=True)
        _page = _browser.new_page(user_agent=_UA["User-Agent"])
    fetch = (lambda u: _fetch_pw(u, _page)) if render_js else _fetch  # noqa: E731

    where = ["c.website IS NOT NULL AND c.website <> ''",
             "c.website LIKE 'http%'",
             "c.county IN ('Florida','Texas','FL','TX')",
             "NOT EXISTS (SELECT 1 FROM people p WHERE p.company_id=c.id AND p.name IS NOT NULL)"]
    params: list = []
    if state:
        where.append("c.county = ?"); params.append(state)
    if town:
        where.append("c.town = ?"); params.append(town)
    sql = f"SELECT c.* FROM companies c WHERE {' AND '.join(where)} ORDER BY c.id LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    processed = named = via_name = via_web = emails_found = no_owner = errors = 0
    franchise = 0
    try:
        for co in rows:
            processed += 1
            if franchises.is_franchise(co["name"]):
                franchise += 1
                continue  # corporate office — no single owner to find
            domain = _domain(co["website"])
            if not domain:
                no_owner += 1
                continue
            try:
                base = f"https://{domain}"
                htmls = [fetch(base) or fetch(f"http://{domain}")]
                text = _visible_text(htmls[0])

                # Try the homepage first; if no owner named there, crawl the
                # about/team pages and retry (owner is often only on /about,
                # even when the homepage is content-rich).
                owner = _extract_owner(co["name"], text) if text else None
                if not owner:
                    for path in _ABOUT_PATHS:
                        extra = fetch(urljoin(base, path))
                        if not extra:
                            continue
                        htmls.append(extra)
                        etext = _visible_text(extra)
                        if len(etext) < 60:
                            continue
                        owner = _extract_owner(co["name"], etext)
                        if owner:
                            text += " " + etext
                            break

                name = owner["name"] if owner else _person_from_name(co["name"])
                source_kind = "web" if owner else ("name" if name else None)
                if not name:
                    no_owner += 1
                    print(f"  ? {co['name']} ({domain}): no owner found")
                    continue
                title = (owner["title"] if owner and owner["title"] else "Owner")

                email = _pick_email(_emails(htmls), name, domain) if scrape_email else None

                named += 1
                if source_kind == "web":
                    via_web += 1
                else:
                    via_name += 1
                if email:
                    emails_found += 1
                tag = f"{name} ({title})" + (f" <{email}>" if email else "")
                print(f"  + {co['name']} -> {tag}  [{source_kind}]")
                if not dry_run:
                    db.insert_person(conn, company_id=co["id"], name=name, role=title,
                                     email=email, person_source=f"web:{source_kind}")
                    conn.commit()  # commit per-company: progress is durable and
                                   # the run is resumable (re-query skips named cos)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  ! {co['name']}: {exc}")
            time.sleep(sleep)
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
        if _browser:
            try:
                _browser.close(); _pw.stop()
            except Exception:  # noqa: BLE001
                pass
    return {"processed": processed, "named": named, "via_name": via_name,
            "via_web": via_web, "emails_found": emails_found,
            "no_owner": no_owner, "franchise_skipped": franchise, "errors": errors}
