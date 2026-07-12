#!/usr/bin/env python3
"""One-shot Brevo scaffolding for the AI Visibility Check funnel.

Idempotent: creates the "GEO Funnel" folder, the "GEO Funnel - Checked" list,
and the contact attributes WF9 writes — skipping anything that already exists —
then prints the list ID to paste into WF9 (BREVO_LIST_ID).

Run once from an IP that is authorised in Brevo
(https://app.brevo.com/security/authorised_ips):

    BREVO_API_KEY=xkeysib-... python3 deploy/brevo_setup.py
    # or it reads BREVO_API_KEY from ../.env / the repo .env

Attributes created (category "normal"):
    BUSINESS_NAME text · DOMAIN text · TOWN text · TRADE text ·
    TOP_COMPETITOR text · PHONE text · VISIBILITY_SCORE float ·
    MENTION_RATE float · SCAN_DATE date
FIRSTNAME / LASTNAME / SMS are Brevo defaults and are left alone.
"""
from __future__ import annotations

import json
import os
import re
import pathlib
import urllib.error
import urllib.request

FOLDER_NAME = "GEO Funnel"
LIST_NAME = "GEO Funnel - Checked"
ATTRIBUTES = {
    "BUSINESS_NAME": "text",
    "DOMAIN": "text",
    "TOWN": "text",
    "TRADE": "text",
    "TOP_COMPETITOR": "text",
    "PHONE": "text",
    "VISIBILITY_SCORE": "float",
    "MENTION_RATE": "float",
    "SCAN_DATE": "date",
}
BASE = "https://api.brevo.com/v3"


def _load_key() -> str:
    key = os.getenv("BREVO_API_KEY", "").strip()
    if key:
        return key
    for envfile in (pathlib.Path(__file__).resolve().parents[3] / ".env",
                    pathlib.Path(__file__).resolve().parents[1] / ".env"):
        if envfile.exists():
            for line in envfile.read_text().splitlines():
                m = re.match(r"^BREVO_API_KEY=(.*)$", line.strip())
                if m:
                    return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("Set BREVO_API_KEY (env or .env).")


def _req(key: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"api-key": key, "accept": "application/json",
                 "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def main() -> None:
    key = _load_key()

    # Sanity: catches the "unrecognised IP" 401 early with a clear message.
    status, acct = _req(key, "GET", "/account")
    if status != 200:
        raise SystemExit(f"Brevo auth failed ({status}): {acct.get('message', acct)}")
    print(f"Account OK: {acct.get('email')}")

    # Folder ------------------------------------------------------------------
    _, folders = _req(key, "GET", "/contacts/folders?limit=50&offset=0")
    folder = next((f for f in folders.get("folders", []) if f["name"] == FOLDER_NAME), None)
    if folder:
        folder_id = folder["id"]
        print(f"Folder exists: {FOLDER_NAME} (id {folder_id})")
    else:
        _, created = _req(key, "POST", "/contacts/folders", {"name": FOLDER_NAME})
        folder_id = created.get("id")
        print(f"Created folder {FOLDER_NAME} (id {folder_id})")

    # List --------------------------------------------------------------------
    _, lists = _req(key, "GET", "/contacts/lists?limit=50&offset=0")
    lst = next((l for l in lists.get("lists", []) if l["name"] == LIST_NAME), None)
    if lst:
        list_id = lst["id"]
        print(f"List exists: {LIST_NAME} (id {list_id})")
    else:
        _, created = _req(key, "POST", "/contacts/lists",
                          {"name": LIST_NAME, "folderId": folder_id})
        list_id = created.get("id")
        print(f"Created list {LIST_NAME} (id {list_id})")

    # Attributes --------------------------------------------------------------
    _, attrs = _req(key, "GET", "/contacts/attributes")
    existing = {a["name"] for a in attrs.get("attributes", []) if a.get("category") == "normal"}
    for name, typ in ATTRIBUTES.items():
        if name in existing:
            print(f"  attribute exists: {name}")
            continue
        status, resp = _req(key, "POST", f"/contacts/attributes/normal/{name}", {"type": typ})
        if status in (200, 201, 204):
            print(f"  created attribute: {name} ({typ})")
        else:
            print(f"  ! attribute {name}: {status} {resp.get('message', resp)}")

    print("\n=== DONE ===")
    print(f"BREVO_LIST_ID = {list_id}   # paste into WF9")


if __name__ == "__main__":
    main()
