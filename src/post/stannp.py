"""Thin Stannp API client, isolated behind send_letter(pdf_path, recipient).

Kept deliberately minimal so it can later be swapped for the shared geo-slab
postal module without touching letter.py or the CLI. Nothing here posts live
unless STANNP_TEST_MODE is False and the caller passes an explicit --yes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import requests

from .. import config, db

API_URL = "https://us.stannp.com/api/v1/letters/create"


@dataclass
class Recipient:
    title: str
    firstname: str
    lastname: str
    address1: str
    town: str
    postcode: str
    company: str = ""
    country: str = "GB"


def _parse_recipient(company, addressee: str) -> Recipient:
    """Best-effort split of a name + a one-line UK address into Stannp fields."""
    parts = addressee.split()
    firstname = parts[0] if parts else ""
    lastname = parts[-1] if len(parts) > 1 else ""
    if addressee == "The Owner":
        firstname, lastname = "The", "Owner"
    addr = company["registered_address"] or ""
    segments = [s.strip() for s in addr.split(",") if s.strip()]
    postcode = segments[-1] if segments else ""
    town = segments[-2] if len(segments) >= 2 else (company["town"] or "")
    address1 = segments[0] if segments else (company["town"] or "")
    return Recipient(
        title="", firstname=firstname, lastname=lastname,
        address1=address1, town=town, postcode=postcode, company=company["name"],
    )


def send_letter(pdf_path: str, recipient: Recipient, test: bool | None = None) -> dict[str, Any]:
    """Create a letter on Stannp from a PDF. Returns {id, delivery_est, test}."""
    if not config.STANNP_API_KEY:
        raise RuntimeError("STANNP_API_KEY is not set.")
    test_mode = config.STANNP_TEST_MODE if test is None else test
    with open(pdf_path, "rb") as fh:
        files = {"file": fh}
        data = {
            "test": "true" if test_mode else "false",
            "recipient[title]": recipient.title,
            "recipient[firstname]": recipient.firstname,
            "recipient[lastname]": recipient.lastname,
            "recipient[address1]": recipient.address1,
            "recipient[town]": recipient.town,
            "recipient[postcode]": recipient.postcode,
            "recipient[company]": recipient.company,
            "recipient[country]": recipient.country,
        }
        resp = requests.post(
            API_URL, params={"api_key": config.STANNP_API_KEY},
            data=data, files=files, timeout=60,
        )
    resp.raise_for_status()
    payload = resp.json()
    est = (date.today() + timedelta(days=4)).isoformat()
    return {
        "id": str(payload.get("data", {}).get("id", "")),
        "delivery_est": est,
        "test": test_mode,
        "raw": payload,
    }


def send_approved(yes: bool = False) -> dict:
    """Send every approved letter via Stannp. Requires yes=True to actually post."""
    conn = db.get_connection()
    try:
        approved = db.get_letters_by_status(conn, "approved")
        cost = len(approved) * config.STANNP_UNIT_PRICE_GBP
        summary = {
            "count": len(approved),
            "estimated_cost_gbp": round(cost, 2),
            "test_mode": config.STANNP_TEST_MODE,
            "sent": 0,
        }
        if not yes:
            summary["note"] = "Dry preview. Re-run with --yes to send."
            return summary
        from .letter import _addressee  # local import avoids a cycle

        for letter in approved:
            company = db.get_company(conn, letter["company_id"])
            addressee, _sal, _pid = _addressee(conn, company)
            recipient = _parse_recipient(company, addressee)
            result = send_letter(letter["pdf_path"], recipient)
            db.update_letter(
                conn, letter["id"], status="sent",
                stannp_id=result["id"], delivery_est=result["delivery_est"],
                sent_at=date.today().isoformat(),
            )
            summary["sent"] += 1
            print(f"  posted letter {letter['id']} for {company['name']} "
                  f"(stannp {result['id']}, test={result['test']})")
        return summary
    finally:
        conn.close()
