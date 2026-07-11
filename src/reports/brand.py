"""Antek Automation brand system.

Neo-brutalist design tokens shared by every rendered artifact (reports,
letters). Zero border-radius, hard offset shadows, flat colour blocks.
"""
from __future__ import annotations

# --- Colour ----------------------------------------------------------------
CORAL = "#CD5C3C"
CREAM = "#E8DCC8"
SAGE = "#C8D8D0"
CHARCOAL = "#2C2C2C"
WHITE = "#FFFFFF"

# --- Type ------------------------------------------------------------------
FONT_DISPLAY = "Outfit"        # 700 / 800 weights for headings
FONT_BODY = "DM Sans"          # 400 / 500 / 700 for body
FONT_MONO = "JetBrains Mono"   # code / scores / data

# --- Form ------------------------------------------------------------------
RADIUS = "0"                    # zero border-radius, always
SHADOW = f"4px 4px 0 {CHARCOAL}"   # hard offset shadow
BORDER = f"2px solid {CHARCOAL}"

# --- Company details (used in footers, never "Ltd") ------------------------
COMPANY_NAME = "Antek Automation"
OPERATOR = "Andy Norman"
ADDRESS_LINES = [
    "Chantry House",
    "38 Chantry Way",
    "Andover",
    "SP10 1LZ",
]
PHONE = "0333 038 9960"
WEBSITE = "antekautomation.com"


def font_face_css() -> str:
    """Web-font imports. WeasyPrint fetches Google Fonts at render time; if the
    machine is offline these degrade gracefully to the CSS fallbacks below."""
    return (
        "@import url('https://fonts.googleapis.com/css2?"
        "family=Outfit:wght@400;700;800&"
        "family=DM+Sans:wght@400;500;700&"
        "family=JetBrains+Mono:wght@400;700&display=swap');"
    )


def base_css() -> str:
    """Shared CSS variables + resets for every branded document."""
    return f"""
    :root {{
        --coral: {CORAL};
        --cream: {CREAM};
        --sage: {SAGE};
        --charcoal: {CHARCOAL};
        --shadow: {SHADOW};
        --border: {BORDER};
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; border-radius: {RADIUS}; }}
    body {{
        font-family: '{FONT_BODY}', 'Helvetica Neue', Arial, sans-serif;
        color: var(--charcoal);
        background: {WHITE};
        line-height: 1.5;
        font-size: 11pt;
    }}
    h1, h2, h3 {{
        font-family: '{FONT_DISPLAY}', 'Arial Black', sans-serif;
        font-weight: 800;
        line-height: 1.05;
        letter-spacing: -0.01em;
    }}
    .mono {{ font-family: '{FONT_MONO}', 'Courier New', monospace; }}
    .card {{
        border: var(--border);
        box-shadow: var(--shadow);
        background: {WHITE};
        padding: 18px;
    }}
    .coral {{ background: var(--coral); color: {WHITE}; }}
    .cream {{ background: var(--cream); }}
    .sage {{ background: var(--sage); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border: 1px solid var(--charcoal); padding: 8px 10px; text-align: left; }}
    th {{ background: var(--charcoal); color: {WHITE}; font-family: '{FONT_DISPLAY}'; }}
    """


# Design token dict, handy for templates that want raw values.
TOKENS = {
    "coral": CORAL,
    "cream": CREAM,
    "sage": SAGE,
    "charcoal": CHARCOAL,
    "white": WHITE,
    "font_display": FONT_DISPLAY,
    "font_body": FONT_BODY,
    "font_mono": FONT_MONO,
    "radius": RADIUS,
    "shadow": SHADOW,
    "border": BORDER,
    "company_name": COMPANY_NAME,
    "operator": OPERATOR,
    "address_lines": ADDRESS_LINES,
    "phone": PHONE,
    "website": WEBSITE,
}
