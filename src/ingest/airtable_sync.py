"""Direct Airtable pull. Same field mapping as csv_import, idempotent upsert."""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .. import config, db
from . import csv_import, util

API_BASE = "https://api.airtable.com/v0"


def _fetch_records(table: str) -> list[dict[str, Any]]:
    if not (config.AIRTABLE_API_KEY and config.AIRTABLE_BASE_ID):
        raise RuntimeError("AIRTABLE_API_KEY and AIRTABLE_BASE_ID must be set.")
    headers = {"Authorization": f"Bearer {config.AIRTABLE_API_KEY}"}
    url = f"{API_BASE}/{config.AIRTABLE_BASE_ID}/{requests.utils.quote(table)}"
    records: list[dict[str, Any]] = []
    params: dict[str, Any] = {"pageSize": 100}
    while True:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        params["offset"] = offset
    return records


def sync_table(table: str, dry_run: bool = False) -> dict[str, int]:
    records = _fetch_records(table)
    # Reuse csv_import's alias resolution by treating Airtable field names as
    # CSV headers.
    all_headers: set[str] = set()
    for rec in records:
        all_headers.update(rec.get("fields", {}).keys())
    mapping = csv_import._build_mapping(sorted(all_headers))
    if "name" not in mapping:
        raise ValueError(
            f"No company-name field found in Airtable table '{table}'. Fields: {sorted(all_headers)}"
        )

    conn = db.get_connection()
    companies = people = updated = 0
    try:
        for rec in records:
            f = rec.get("fields", {})
            name = str(f.get(mapping["name"], "")).strip()
            if not name:
                continue
            town = str(f.get(mapping.get("town", ""), "")).strip() or None
            website = str(f.get(mapping.get("website", ""), "")).strip() or None
            existing = util.find_duplicate(conn, name, town, website)
            fields = {
                "name": name,
                "website": website,
                "town": town,
                "county": str(f.get(mapping.get("county", ""), "")).strip() or None,
                "sector": str(f.get(mapping.get("sector", ""), "")).strip() or None,
                "phone": str(f.get(mapping.get("phone", ""), "")).strip() or None,
                "source": "airtable",
                "source_date": date.today().isoformat(),
            }
            fields = {k: v for k, v in fields.items() if v is not None}

            if dry_run:
                print(f"  {'update' if existing else 'insert'}: {name} ({town or '?'})")
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

            person_name = str(f.get(mapping.get("person_name", ""), "")).strip()
            linkedin = str(f.get(mapping.get("linkedin_url", ""), "")).strip()
            role = str(f.get(mapping.get("role", ""), "")).strip()
            if (person_name or linkedin) and company_id != -1 and not dry_run:
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
                        name=person_name or None, role=role or None,
                        linkedin_url=linkedin or None,
                        person_source="airtable", connection_status="none",
                    )
                    people += 1
            elif (person_name or linkedin) and dry_run:
                print(f"      person: {person_name or '(no name)'} {linkedin}")
                people += 1
    finally:
        conn.close()
    return {"companies": companies, "updated": updated, "people": people}
