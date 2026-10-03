"""
Render the five dashboard layout wireframes from the declared visual contract.

Every box position, size, label and type is read from visual_specification.csv. Nothing is positioned by hand, so
the wireframes cannot drift from the specification the report is actually built against.

These are LAYOUT WIREFRAMES, not dashboard mock-ups. They deliberately contain no data: a wireframe that showed
plausible-looking numbers would be inventing results for a report that has not been built yet.

    C:/Users/abhij/anaconda3/python.exe dashboard/power_bi/render_wireframes.py

Output: dashboard/power_bi/wireframes/0*.png, each exactly 1280 x 720 pixels.
"""

from pathlib import Path
import sys

import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "dashboard" / "power_bi" / "visual_specification.csv"
OUT = ROOT / "dashboard" / "power_bi" / "wireframes"

CANVAS_W, CANVAS_H = 1280, 720
DPI = 100
# The 1280x720 report canvas is drawn into the top of the image; the remainder is the caption strip, so the
# caption can never overlap or clip the bottom row of visuals.
CONTENT_SCALE = 0.967

# project palette, matching sme_acquisition_theme.json
NAVY = "#1F3A5F"
BLUE = "#2E6F9E"
LIGHT_BLUE = "#4A9BC7"
TEAL = "#1A8F7A"
AMBER = "#C8821A"
RED = "#9B2C24"
NEUTRAL_BG = "#F4F6F8"
NEUTRAL_LINE = "#C3CDD6"
GREY_TEXT = "#4A5A6A"
WHITE = "#FFFFFF"

# how each visual type is drawn, so headers, cards, slicers, charts, tables and warnings are distinguishable
STYLE = {
    "header":  dict(face=NAVY,        edge=NAVY,        text=WHITE,      label="HEADER"),
    "warning": dict(face="#FCF2E2",   edge=AMBER,       text=NAVY,       label="WARNING"),
    "card":    dict(face=WHITE,       edge=BLUE,        text=NAVY,       label="KPI CARD"),
    "multi":   dict(face=WHITE,       edge=AMBER,       text=NAVY,       label="MULTI-ROW CARD"),
    "slicer":  dict(face="#E9EEF3",   edge=LIGHT_BLUE,  text=NAVY,       label="SLICER"),
    "chart":   dict(face=WHITE,       edge=TEAL,        text=NAVY,       label="CHART"),
    "table":   dict(face=WHITE,       edge=NAVY,        text=NAVY,       label="TABLE"),
    "map":     dict(face=WHITE,       edge=TEAL,        text=NAVY,       label="MAP"),
}

PAGES = [
    ("Executive Overview",      "01_executive_overview_wireframe.png",   "Page 1"),
    ("Market Opportunity",      "02_market_opportunity_wireframe.png",   "Page 2"),
    ("Acquisition Funnel",      "03_acquisition_funnel_wireframe.png",   "Page 3"),
    ("Lead Action Queue",       "04_lead_action_queue_wireframe.png",    "Page 4"),
    ("RM Capacity and Routing", "05_rm_capacity_routing_wireframe.png",  "Page 5"),
]


def classify(visual_id: str, visual_type: str, title: str) -> str:
    """Map a specification row to a wireframe box style."""
    vt, tl = visual_type.lower(), title.lower()
    if "text box" in vt:
        # the first text box on each page is the page banner; later ones are standing warnings
        return "header" if visual_id.endswith("-01") else "warning"
    if "multi-row" in vt:
        return "multi"
    if "slicer" in vt:
        return "slicer"
    if "card" in vt:
        return "card"
    if "table" in vt:
        return "table"
    if "map" in vt:
        return "map"
    return "chart"


def wrap(text: str, width: int) -> str:
    """Greedy wrap that never breaks a word, so labels stay readable."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def shorten(title: str, limit: int) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "\u2026"


def render(page: str, filename: str, page_label: str, spec: pd.DataFrame) -> Path:
    rows = spec[spec.page == page]
    fig = plt.figure(figsize=(CANVAS_W / DPI, CANVAS_H / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, CANVAS_W)
    ax.set_ylim(CANVAS_H / CONTENT_SCALE, 0)   # y increases downward, matching Power BI coordinates
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), CANVAS_W, CANVAS_H / CONTENT_SCALE,
                           facecolor=NEUTRAL_BG, edgecolor="none", zorder=0))

    for r in rows.itertuples():
        x, y, w, h = [int(v) for v in str(r.position_x_y_w_h_px).split(",")]
        kind = classify(r.visual_id, r.visual_type, r.title)
        st = STYLE[kind]
        pad = 3
        ax.add_patch(FancyBboxPatch(
            (x + pad, y + pad), max(w - 2 * pad, 1), max(h - 2 * pad, 1),
            boxstyle="round,pad=0,rounding_size=4",
            facecolor=st["face"], edgecolor=st["edge"],
            linewidth=1.6 if kind in ("header", "warning", "multi") else 1.2,
            zorder=2))

        inner_w = w - 16
        cx, top = x + 8, y + 8
        # characters that fit on one line at 7.5pt Segoe-like spacing (~5.4 px per char)
        chars = max(int(inner_w / 5.4), 8)

        # visual ID chip
        ax.text(cx, top + 9, r.visual_id, fontsize=8.5, fontweight="bold",
                color=st["text"], va="center", ha="left", zorder=3, family="DejaVu Sans")
        # type label, right-aligned, only when the box is wide enough to hold both
        if inner_w > 150:
            ax.text(x + w - 8, top + 9, st["label"], fontsize=6.5, color=st["text"],
                    alpha=0.75, va="center", ha="right", zorder=3, family="DejaVu Sans")

        # abbreviated title, wrapped, vertically centred in the remaining space
        max_lines = max(int((h - 30) / 13), 1)
        title = wrap(shorten(r.title, chars * max_lines), chars)
        title = "\n".join(title.split("\n")[:max_lines])
        ax.text(cx, top + 24, title, fontsize=7.5, color=st["text"],
                va="top", ha="left", zorder=3, linespacing=1.35, family="DejaVu Sans")

    # Wireframe caption, drawn in reserved space BELOW the 720px canvas so it never clips a visual.
    strip_top = CANVAS_H + 2
    strip_h = CANVAS_H / CONTENT_SCALE - strip_top
    ax.add_patch(Rectangle((0, strip_top), CANVAS_W, strip_h, facecolor=NAVY, edgecolor="none", zorder=4))
    ax.text(8, strip_top + strip_h / 2,
            f"WIREFRAME — layout only, no data — {page_label}: {page} — canvas 1280 x 720",
            fontsize=8, color=WHITE, va="center", ha="left", zorder=5, family="DejaVu Sans")
    ax.text(CANVAS_W - 8, strip_top + strip_h / 2,
            "Generated from visual_specification.csv — synthetic data, hypothetical rules",
            fontsize=7, color="#A8BBCC", va="center", ha="right", zorder=5, family="DejaVu Sans")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / filename
    fig.savefig(path, dpi=DPI, facecolor=NEUTRAL_BG, metadata={"Software": None})
    plt.close(fig)
    return path


def main() -> int:
    spec = pd.read_csv(SPEC)
    seen = []
    for page, filename, label in PAGES:
        rows = spec[spec.page == page]
        if rows.empty:
            print(f"FAIL: no visuals found for page {page}")
            return 1
        path = render(page, filename, label, spec)
        seen += list(rows.visual_id)
        print(f"  rendered {filename}  ({len(rows)} visuals)")

    missing = sorted(set(spec.visual_id) - set(seen))
    dupes = sorted({v for v in seen if seen.count(v) > 1})
    print(f"visuals placed: {len(seen)} of {len(spec)}")
    print(f"missing: {missing or 'none'} | duplicated: {dupes or 'none'}")
    return 0 if not missing and not dupes and len(seen) == len(spec) else 1


if __name__ == "__main__":
    sys.exit(main())
