"""Minimal HTTP wrapper around the inbound AI Visibility Check.

Deployed on Coolify (Contabo) and called by WF9 (n8n): a single POST /scan that
classifies, scans, scores and renders a PDF, returning a flat JSON payload for
n8n to hand to Brevo. Auth is a shared secret in the X-Scan-Token header.

Run locally:  uv run uvicorn src.server:app --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .visibility import inbound, score

SCAN_TOKEN = os.getenv("SCAN_TOKEN", "")

app = FastAPI(title="Antek AI Visibility Check", version="1.0")


class ScanRequest(BaseModel):
    # Accept both the form field names and shorthands.
    business_name: str | None = None
    company: str | None = None
    website_url: str | None = None
    domain: str | None = None
    location: str | None = None
    email: str | None = None
    name: str | None = None
    phone: str | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/scan")
def scan(req: ScanRequest, x_scan_token: str = Header(default="")) -> dict:
    if SCAN_TOKEN and x_scan_token != SCAN_TOKEN:
        raise HTTPException(status_code=401, detail="bad scan token")

    company_name = (req.business_name or req.company or "").strip()
    website = (req.website_url or req.domain or "").strip()
    if not website:
        raise HTTPException(status_code=422, detail="website_url is required")

    try:
        return inbound.run_inbound(
            company_name=company_name, website=website, location=req.location or "",
            email=req.email or "", name=req.name or "", phone=req.phone or "",
        )
    except score.VisibilityProbeError as exc:
        # Every engine errored — not a genuine 0/100. Tell n8n so it emails the
        # lead a "taking longer than expected" note instead of a fake report.
        return {"status": "failed_probes", "error": str(exc)}
