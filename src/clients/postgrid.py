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


class PostGridError(RuntimeError):
    """Carries PostGrid's stable error.type + human message (e.g.
    invalid_api_key_error, org_missing_payment_method_error, limit_reached_error)."""

    def __init__(self, status: int, type_: str, message: str):
        self.status = status
        self.type = type_
        self.message = message
        super().__init__(f"[{status} {type_}] {message}")


def _check(resp: requests.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {}) or {}
        except Exception:  # noqa: BLE001
            err = {}
        raise PostGridError(resp.status_code, err.get("type", "http_error"),
                            err.get("message", resp.text[:200]))
    return resp.json()


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    if not config.POSTGRID_API_KEY:
        raise RuntimeError("POSTGRID_API_KEY is not set.")
    h = {"x-api-key": config.POSTGRID_API_KEY, "Content-Type": "application/json"}
    if idempotency_key:
        # Safe retries — PostGrid returns the original order, never a duplicate.
        h["Idempotency-Key"] = idempotency_key
    return h


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
    return _check(resp)["id"]


def create_letter(to: dict[str, Any], from_addr: dict[str, Any],
                  html: str | None = None,
                  merge: dict[str, Any] | None = None,
                  *, template: str | None = None,
                  color: bool = False, double_sided: bool = False,
                  address_placement: str = "insert_blank_page",
                  size: str | None = None,
                  mailing_class: str | None = None,
                  idempotency_key: str | None = None,
                  metadata: dict[str, Any] | None = None,
                  extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create (queue) a letter. `to`/`from_addr` may be our address dicts or an
    existing PostGrid contact id string. `html` may contain {{merge}} vars."""
    if not (html or template):
        raise ValueError("create_letter needs either html or a portal template id.")
    payload: dict[str, Any] = {
        "to": to if isinstance(to, str) else _contact(to),
        "from": from_addr if isinstance(from_addr, str) else _contact(from_addr),
        "color": color,
        "doubleSided": double_sided,
        "addressPlacement": address_placement,
    }
    payload["template" if template else "html"] = template or html
    if merge:
        payload["mergeVariables"] = merge
    if size:
        payload["size"] = size          # e.g. "a4" for UK, "us_letter" for US
    if mailing_class:
        payload["mailingClass"] = mailing_class   # default: first_class (fastest)
    if metadata:
        payload["metadata"] = metadata            # e.g. {companyId, campaign} for dedup/analytics (<10kb)
    if extra:
        payload.update(extra)
    resp = requests.post(f"{BASE}/letters", json=payload,
                         headers=_headers(idempotency_key), timeout=60)
    return _check(resp)


def get_letter(order_id: str) -> dict[str, Any]:
    resp = requests.get(f"{BASE}/letters/{order_id}", headers=_headers(), timeout=30)
    return _check(resp)


def cancel_letter(order_id: str) -> dict[str, Any]:
    """Cancel a letter while it is still in `ready` status."""
    resp = requests.delete(f"{BASE}/letters/{order_id}", headers=_headers(), timeout=30)
    return _check(resp)


def create_template(description: str, html: str) -> dict[str, Any]:
    """Create (or update) a reusable PostGrid letter template.

    Returns the full template object; ``id`` (e.g. ``tmpl_abc123``) is what
    you pass to ``create_letter(template=...)`` to avoid resending the HTML on
    every letter.  Templates live in the PostGrid dashboard and can be edited
    there visually.
    """
    payload = {"description": description, "html": html}
    resp = requests.post(f"{BASE}/templates", json=payload,
                         headers=_headers(), timeout=60)
    return _check(resp)


def list_templates(skip: int = 0, limit: int = 40) -> list[dict[str, Any]]:
    """Return a page of PostGrid letter templates."""
    resp = requests.get(f"{BASE}/templates",
                        params={"skip": skip, "limit": limit},
                        headers=_headers(), timeout=30)
    return _check(resp).get("data", [])


def is_test_key() -> bool:
    return config.POSTGRID_API_KEY.startswith("test_")
