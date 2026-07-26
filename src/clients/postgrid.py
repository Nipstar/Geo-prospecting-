"""PostGrid Print & Mail — letter sending (UK + US).

Server-side render: send the letter HTML (with {{merge}} variables) + a
destination/return address; PostGrid renders the PDF, stamps the address and
mails it. Removes the local WeasyPrint/PDF step from the send path.

Docs: https://docs.postgrid.com  — auth header `x-api-key`. The key itself
decides test vs live (test keys sandbox everything; nothing is mailed).

  create_letter(to, from_addr, html, merge=...) -> order dict (id, status, ...)
  get_letter(order_id)                          -> current status
  create_contact(addr)                          -> reusable contact id

Address is auto-stamped, so we default addressPlacement='insert_blank_page' —
PostGrid adds a dedicated address page and the letter template needs no changes.
"""
from __future__ import annotations

from typing import Any

import requests

from .. import config

BASE = "https://api.postgrid.com/print-mail/v1"


def _headers() -> dict[str, str]:
    if not config.POSTGRID_API_KEY:
        raise RuntimeError("POSTGRID_API_KEY is not set.")
    return {"x-api-key": config.POSTGRID_API_KEY,
            "Content-Type": "application/json"}


def _contact(addr: dict[str, Any]) -> dict[str, Any]:
    """Normalise a contact for PostGrid. Accepts our own address fields."""
    out = {
        "firstName": addr.get("first_name") or addr.get("firstName"),
        "lastName": addr.get("last_name") or addr.get("lastName"),
        "companyName": addr.get("company") or addr.get("companyName"),
        "addressLine1": addr.get("line1") or addr.get("addressLine1")
                        or addr.get("address"),
        "addressLine2": addr.get("line2") or addr.get("addressLine2"),
        "city": addr.get("city") or addr.get("town"),
        "provinceOrState": addr.get("state") or addr.get("county")
                           or addr.get("provinceOrState"),
        "postalOrZip": addr.get("postal") or addr.get("postcode")
                       or addr.get("zip") or addr.get("postalOrZip"),
        "country": addr.get("country") or "GB",
    }
    return {k: v for k, v in out.items() if v}


def create_contact(addr: dict[str, Any]) -> str:
    resp = requests.post(f"{BASE}/contacts", json=_contact(addr),
                         headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def create_letter(to: dict[str, Any], from_addr: dict[str, Any], html: str,
                  merge: dict[str, Any] | None = None,
                  *, color: bool = False, double_sided: bool = False,
                  address_placement: str = "insert_blank_page",
                  size: str | None = None,
                  extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create (queue) a letter. `to`/`from_addr` may be our address dicts or an
    existing PostGrid contact id string. `html` may contain {{merge}} vars."""
    payload: dict[str, Any] = {
        "to": to if isinstance(to, str) else _contact(to),
        "from": from_addr if isinstance(from_addr, str) else _contact(from_addr),
        "html": html,
        "color": color,
        "doubleSided": double_sided,
        "addressPlacement": address_placement,
    }
    if merge:
        payload["mergeVariables"] = merge
    if size:
        payload["size"] = size          # e.g. "a4" for UK, "us_letter" for US
    if extra:
        payload.update(extra)
    resp = requests.post(f"{BASE}/letters", json=payload,
                         headers=_headers(), timeout=60)
    resp.raise_for_status()
    return resp.json()


def get_letter(order_id: str) -> dict[str, Any]:
    resp = requests.get(f"{BASE}/letters/{order_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def is_test_key() -> bool:
    return config.POSTGRID_API_KEY.startswith("test_")
