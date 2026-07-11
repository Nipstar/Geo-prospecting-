"""CSV prospect import with flexible column mapping.

Handles the existing Airtable export of estate-agent records (company, website,
town, contact name, role, linkedin_url). When a row carries person data, both a
company and a linked person record are created.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from .. import db
from . import util

# Accepted header aliases -> canonical field. Matching is case-insensitive and
# ignores spaces/underscores.
COLUMN_ALIASES: dict[str, list[str]] = {
    "name": ["company", "companyname", "name", "business", "businessname"],
    "website": ["website", "url", "web", "site"],
    "town": ["town", "city", "location"],
    "county": ["county", "region"],
    "sector": ["sector", "industry", "category", "type"],
    "phone": ["phone", "telephone", "tel", "mobile"],
    "person_name": ["contactname", "contact", "name", "personname", "director", "fullname"],
    "role": ["role", "title", "jobtitle", "position"],
    "linkedin_url": ["linkedin", "linkedinurl", "linkedinprofile", "profile"],
}


def _norm(header: str) -> str:
    return header.lower().replace(" ", "").replace("_", "").replace("-", "")


def _build_mapping(headers: list[str]) -> dict[str, str]:
    """Return {canonical_field: actual_header}. 'name' for company wins over the
    person alias when both could match, resolved by column order preference."""
    norm_to_actual = {_norm(h): h for h in headers}
    mapping: dict[str, str] = {}
    # Company name first, so a bare 'name' header binds to the company.
    for field in ["name", "website", "town", "county", "sector", "phone", "role", "linkedin_url"]:
        for alias in COLUMN_ALIASES[field]:
            if alias in norm_to_actual:
                mapping[field] = norm_to_actual[alias]
                break
    # Person name: prefer an explicit contact/director column, avoid reusing the
    # company 'name' column.
    company_header = mapping.get("name")
    for alias in COLUMN_ALIASES["person_name"]:
        actual = norm_to_actual.get(alias)
        if actual and actual != company_header:
            mapping["person_name"] = actual
            break
    return mapping


def import_csv(path: str | Path, dry_run: bool = False) -> dict[str, int]:
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        mapping = _build_mapping(headers)
        if "name" not in mapping:
            raise ValueError(
                f"Could not find a company-name column in {headers}. "
                "Add a header like 'company' or 'name'."
            )
        rows = list(reader)

    conn = db.get_connection()
    companies = people = updated = 0
    try:
        for row in rows:
            name = (row.get(mapping["name"]) or "").strip()
            if not name:
                continue
            town = (row.get(mapping.get("town", "")) or "").strip() or None
            website = (row.get(mapping.get("website", "")) or "").strip() or None

            existing = util.find_duplicate(conn, name, town, website)
            fields = {
                "name": name,
                "website": website,
                "town": town,
                "county": (row.get(mapping.get("county", "")) or "").strip() or None,
                "sector": (row.get(mapping.get("sector", "")) or "").strip() or None,
                "phone": (row.get(mapping.get("phone", "")) or "").strip() or None,
                "source": "csv_import",
                "source_date": date.today().isoformat(),
            }
            fields = {k: v for k, v in fields.items() if v is not None}

            if dry_run:
                verb = "update" if existing else "insert"
                print(f"  {verb}: {name} ({town or '?'})")
                if existing:
                    updated += 1
                else:
                    companies += 1
                company_id = existing["id"] if existing else -1
            elif existing:
                db.update_company(conn, existing["id"], **{k: v for k, v in fields.items() if k != "name"})
                company_id = existing["id"]
                updated += 1
            else:
                company_id = db.insert_company(conn, status="new", **fields)
                companies += 1

            # Person record, if present.
            person_name = (row.get(mapping.get("person_name", "")) or "").strip()
            linkedin = (row.get(mapping.get("linkedin_url", "")) or "").strip()
            role = (row.get(mapping.get("role", "")) or "").strip()
            if person_name or linkedin:
                if dry_run:
                    print(f"      person: {person_name or '(no name)'} {linkedin}")
                    people += 1
                elif company_id != -1:
                    dup = db.find_person(conn, company_id, person_name, linkedin)
                    if dup:
                        db.update_person(
                            conn, dup["id"],
                            name=person_name or dup["name"],
                            role=role or dup["role"],
                            linkedin_url=linkedin or dup["linkedin_url"],
                        )
                    else:
                        db.insert_person(
                            conn, company_id,
                            name=person_name or None,
                            role=role or None,
                            linkedin_url=linkedin or None,
                            person_source="airtable",
                            connection_status="none",
                        )
                        people += 1
    finally:
        conn.close()
    return {"companies": companies, "updated": updated, "people": people}
