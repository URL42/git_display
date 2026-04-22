"""
renderer.py — Pillow-based layout engine for GitHub BWR e-paper dashboard
Produces two PIL 'L' mode images (black layer + red layer) at 800×480.

BWR pixel conventions used throughout:
  value   0  →  ink   (black on black layer, red on red layer)
  value 255  →  blank (white / no ink)

A pixel visible as WHITE must be 255 on BOTH layers.
A pixel visible as RED  must be   0 on red layer, 255 on black layer.
A pixel visible as BLACK must be  0 on black layer (red layer value ignored).
"""

from __future__ import annotations

import numpy as np
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

from github_api import relative_time
from config import CONTRIB_THRESHOLDS, ROTATE_180

# ─── Display dimensions ────────────────────────────────────────────────────────
W, H = 800, 480

# ─── Layout zones (all in pixels) ─────────────────────────────────────────────
HEADER_H        = 36          # height of the top red header bar
PANEL_TOP       = HEADER_H + 2
PANEL_BOTTOM    = 340         # bottom of the two side-by-side panels
PANEL_DIV_X     = W // 2     # x-coord of the vertical panel divider

CHART_ZONE_TOP  = PANEL_BOTTOM + 5   # top of the entire chart zone
CHART_MONTH_Y   = CHART_ZONE_TOP + 10 # y for month labels (inside box border)
CHART_ORIGIN_Y  = CHART_MONTH_Y + 15 # y where cells start

# Centre the 52-week grid (with a left gap for day labels)
CELL_SIZE       = 11
CELL_GAP        = 2
STEP            = CELL_SIZE + CELL_GAP    # 13 px per slot
CHART_WIDTH     = 52 * STEP - CELL_GAP   # 676 px
DAY_LABEL_W     = 14                      # px reserved left of cells for M/W/F
CHART_ORIGIN_X  = (W - CHART_WIDTH - DAY_LABEL_W) // 2 + DAY_LABEL_W  # ≈ 69

LEGEND_Y        = CHART_ORIGIN_Y + 7 * STEP + 8

# ─── Dither patterns for levels 1 & 2 ────────────────────────────────────────
# 4×4 tile; True = place an ink pixel. Tiled across the cell area.
_DITHER = {
    1: np.array([
        [1, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 0],
    ], dtype=bool),
    2: np.array([
        [1, 0, 1, 0],
        [0, 1, 0, 1],
        [1, 0, 1, 0],
        [0, 1, 0, 1],
    ], dtype=bool),
}

# Pre-bake cell patch images for all 5 levels → fast paste() at render time
# Each entry: (black_patch, red_patch)  — PIL 'L' images of size CELL_SIZE²
_CELL_PATCHES: dict[int, tuple[Image.Image, Image.Image]] = {}

def _build_patches() -> None:
    for lvl in range(5):
        b = np.full((CELL_SIZE, CELL_SIZE), 255, dtype=np.uint8)
        r = np.full((CELL_SIZE, CELL_SIZE), 255, dtype=np.uint8)

        if lvl == 0:
            pass                       # white — leave both at 255

        elif lvl in _DITHER:
            pat = _DITHER[lvl]
            for dy in range(CELL_SIZE):
                for dx in range(CELL_SIZE):
                    if pat[dy % 4, dx % 4]:
                        r[dy, dx] = 0  # dithered red

        elif lvl == 3:
            r[:] = 0                   # solid red

        elif lvl == 4:
            b[:] = 0                   # solid black

        # Draw 1px black border on the black layer patch
        b_img = Image.fromarray(b, mode="L")
        r_img = Image.fromarray(r, mode="L")
        bd    = ImageDraw.Draw(b_img)
        bd.rectangle([0, 0, CELL_SIZE - 1, CELL_SIZE - 1], outline=0, width=1)

        _CELL_PATCHES[lvl] = (b_img, r_img)

_build_patches()


# ─── Font loader ──────────────────────────────────────────────────────────────
def _load_fonts() -> dict[str, ImageFont.ImageFont]:
    sans_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    ]
    bold_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]

    def load(paths: list[str], size: int) -> ImageFont.ImageFont:
        for p in paths:
            try:
                return ImageFont.truetype(p, size)
            except (IOError, OSError):
                pass
        return ImageFont.load_default()

    return {
        "title":   load(bold_paths, 17),
        "heading": load(bold_paths, 13),
        "body":    load(sans_paths, 13),
        "small":   load(sans_paths, 11),
        "tiny":    load(sans_paths, 10),
    }


# ─── Utility helpers ──────────────────────────────────────────────────────────
def _tw(font: ImageFont.ImageFont, text: str) -> int:
    """Text width in pixels, works with both old and new Pillow."""
    try:
        return int(font.getlength(text))
    except AttributeError:
        try:
            l, t, r, b = font.getbbox(text)
            return r - l
        except Exception:
            return len(text) * 7   # crude fallback


def _trunc(s: str, font: ImageFont.ImageFont, max_px: int) -> str:
    """Truncate string to fit within max_px, appending '…' if needed."""
    if _tw(font, s) <= max_px:
        return s
    while s and _tw(font, s + "…") > max_px:
        s = s[:-1]
    return s + "…"


def _contrib_level(count: int) -> int:
    t = CONTRIB_THRESHOLDS
    if count == 0:       return 0
    elif count <= t[1]:  return 1
    elif count <= t[2]:  return 2
    elif count <= t[3]:  return 3
    else:                return 4


# ─── Section renderers ────────────────────────────────────────────────────────

def _draw_header(
    ib: Image.Image, ir: Image.Image,
    username: str, total: int,
    fonts: dict,
) -> None:
    """Full-width red header bar with username, contribution total, timestamp."""
    db = ImageDraw.Draw(ib)
    dr = ImageDraw.Draw(ir)

    # Solid red background on the red layer
    dr.rectangle([0, 0, W - 1, HEADER_H - 1], fill=0)

    # Left: "github / username"  (white text on red = 255 on red layer)
    dr.text((10, (HEADER_H - _tw(fonts["title"], "A")) // 2 - 1),
            f"github / {username}", font=fonts["title"], fill=255)

    # Centre: contribution count
    centre_text = f"{total:,} contributions this year"
    cx = (W - _tw(fonts["small"], centre_text)) // 2
    dr.text((cx, (HEADER_H - 10) // 2 + 1), centre_text, font=fonts["small"], fill=255)

    # Right: last-updated timestamp
    ts   = datetime.now().strftime("%-H:%M")
    ts_w = _tw(fonts["small"], ts)
    dr.text((W - ts_w - 10, (HEADER_H - 10) // 2 + 1), ts, font=fonts["small"], fill=255)

    # Bottom border of header
    db.line([(0, HEADER_H), (W - 1, HEADER_H)], fill=0, width=1)


PANEL_MARGIN = 4   # gap between display edge and panel box
PANEL_HEAD_H = 22  # height of the filled black panel header bar

def _draw_dividers(ib: Image.Image) -> None:
    db = ImageDraw.Draw(ib)
    # Left panel box
    db.rectangle(
        [PANEL_MARGIN, PANEL_TOP,
         PANEL_DIV_X - PANEL_MARGIN, PANEL_BOTTOM],
        outline=0, width=2,
    )
    # Right panel box
    db.rectangle(
        [PANEL_DIV_X + PANEL_MARGIN, PANEL_TOP,
         W - PANEL_MARGIN, PANEL_BOTTOM],
        outline=0, width=2,
    )
    # (no box around chart zone — cleaner without it)


def _draw_repo_panel(
    ib: Image.Image, ir: Image.Image,
    repos: list, fonts: dict,
) -> None:
    """Left panel: recent repositories."""
    db = ImageDraw.Draw(ib)
    dr = ImageDraw.Draw(ir)

    x0, x1 = PANEL_MARGIN + 2, PANEL_DIV_X - PANEL_MARGIN - 2
    pw = x1 - x0

    # Filled black header bar + white label
    bar_y0, bar_y1 = PANEL_TOP + 2, PANEL_TOP + PANEL_HEAD_H
    db.rectangle([x0, bar_y0, x1, bar_y1], fill=0)
    db.text((x0 + 6, bar_y0 + 4), "RECENT REPOS", font=fonts["heading"], fill=255)

    # Row layout
    y0       = PANEL_TOP + PANEL_HEAD_H + 4
    avail_h  = PANEL_BOTTOM - y0 - 4
    row_h    = min(avail_h // max(len(repos), 1), 44)
    item_y   = y0

    for repo in repos:
        if item_y + row_h > PANEL_BOTTOM:
            break

        # ── Repo name ───────────────────────────────────────────────
        name = _trunc(repo["name"], fonts["body"], pw - 120)
        db.text((x0, item_y + 1), name, font=fonts["body"], fill=0)

        # ── Language tag (red pill) ──────────────────────────────────
        lang    = (repo["language"] or "—")[:8]
        tag_pad = 5
        tag_w   = _tw(fonts["tiny"], lang) + tag_pad * 2
        tag_x   = x1 - tag_w
        tag_y   = item_y + 1
        tag_h   = 13
        # Red tag background (rectangle — rounded_rectangle requires Pillow 8.2+)
        dr.rectangle([tag_x, tag_y, tag_x + tag_w, tag_y + tag_h], fill=0)
        # White text (255 on both layers where text pixels land)
        dr.text((tag_x + tag_pad, tag_y + 1), lang, font=fonts["tiny"], fill=255)

        # ── Star count ───────────────────────────────────────────────
        stars   = f"★ {repo['stars']}"
        star_x  = tag_x - _tw(fonts["small"], stars) - 8
        db.text((int(star_x), item_y + 2), stars, font=fonts["small"], fill=0)

        # ── Description (line 2) ─────────────────────────────────────
        desc = repo.get("description", "")
        if desc and row_h >= 30:
            desc = _trunc(desc, fonts["tiny"], pw - 4)
            db.text((x0 + 2, item_y + 16), desc, font=fonts["tiny"], fill=0)

        # ── Pushed timestamp (line 3) ─────────────────────────────────
        if row_h >= 40:
            pushed = relative_time(repo["pushed_at"])
            db.text((x0 + 2, item_y + 28), f"pushed {pushed}",
                    font=fonts["tiny"], fill=0)

        # ── Row separator ─────────────────────────────────────────────
        sep_y = item_y + row_h - 1
        db.line([(x0, sep_y), (x1, sep_y)], fill=0, width=1)
        item_y += row_h


def _draw_feed_panel(
    ib: Image.Image, ir: Image.Image,
    feed: list, fonts: dict,
) -> None:
    """Right panel: activity feed."""
    db = ImageDraw.Draw(ib)

    x0, x1 = PANEL_DIV_X + PANEL_MARGIN + 2, W - PANEL_MARGIN - 2
    pw = x1 - x0

    # Filled black header bar + white label
    bar_y0, bar_y1 = PANEL_TOP + 2, PANEL_TOP + PANEL_HEAD_H
    db.rectangle([x0, bar_y0, x1, bar_y1], fill=0)
    db.text((x0 + 6, bar_y0 + 4), "ACTIVITY FEED", font=fonts["heading"], fill=255)

    y0      = PANEL_TOP + PANEL_HEAD_H + 4
    avail_h = PANEL_BOTTOM - y0 - 4
    row_h   = min(avail_h // max(len(feed), 1), 44)
    item_y  = y0

    for event in feed:
        if item_y + row_h > PANEL_BOTTOM:
            break

        # ── Repo name (left) + relative time (right) ─────────────────
        repo_short = _trunc(event["repo_short"], fonts["body"], pw - 60)
        db.text((x0, item_y + 1), repo_short, font=fonts["body"], fill=0)

        t   = relative_time(event["created_at"])
        t_x = x1 - _tw(fonts["tiny"], t)
        db.text((int(t_x), item_y + 3), t, font=fonts["tiny"], fill=0)

        # ── Event description (line 2) ────────────────────────────────
        desc = _trunc(event["description"] or "", fonts["tiny"], pw - 4)
        db.text((x0 + 2, item_y + 16), desc, font=fonts["tiny"], fill=0)

        # ── Full repo path (line 3, dimmer with tiny font) ────────────
        if row_h >= 40:
            full = _trunc(event["repo"], fonts["tiny"], pw - 4)
            db.text((x0 + 2, item_y + 28), full, font=fonts["tiny"], fill=0)

        sep_y = item_y + row_h - 1
        db.line([(x0, sep_y), (x1, sep_y)], fill=0, width=1)
        item_y += row_h


def _draw_chart(
    ib: Image.Image, ir: Image.Image,
    weeks: list, fonts: dict,
) -> None:
    """Contribution calendar grid with month labels, day labels, and legend."""
    db = ImageDraw.Draw(ib)
    dr = ImageDraw.Draw(ir)

    ox = CHART_ORIGIN_X
    oy = CHART_ORIGIN_Y

    # ── Month labels ───────────────────────────────────────────────────────
    last_month = None
    for col, week in enumerate(weeks):
        days = week.get("contributionDays", [])
        if not days:
            continue
        try:
            month = datetime.strptime(days[0]["date"], "%Y-%m-%d").strftime("%b")
        except (KeyError, ValueError):
            continue
        if month != last_month:
            last_month = month
            lx = ox + col * STEP
            # Don't render if it would overlap the right edge
            if lx + _tw(fonts["tiny"], month) < W - 4:
                db.text((lx, CHART_MONTH_Y), month, font=fonts["tiny"], fill=0)

    # ── Day-of-week labels (Mon / Wed / Fri) ──────────────────────────────
    for row, label in [(1, "M"), (3, "W"), (5, "F")]:
        lx = ox - DAY_LABEL_W + 1
        ly = oy + row * STEP + 1
        db.text((lx, ly), label, font=fonts["tiny"], fill=0)

    # ── Cells ─────────────────────────────────────────────────────────────
    # Use pre-baked patches for maximum speed on Pi Zero 2W
    for col, week in enumerate(weeks):
        for row, day in enumerate(week.get("contributionDays", [])):
            lvl    = _contrib_level(day.get("contributionCount", 0))
            bp, rp = _CELL_PATCHES[lvl]
            px     = ox + col * STEP
            py     = oy + row * STEP
            ib.paste(bp, (px, py))
            ir.paste(rp, (px, py))

    # ── Legend ────────────────────────────────────────────────────────────
    lx = ox
    db.text((lx, LEGEND_Y + 1), "Less", font=fonts["tiny"], fill=0)
    lx += _tw(fonts["tiny"], "Less") + 6
    for lvl in range(5):
        bp, rp = _CELL_PATCHES[lvl]
        ib.paste(bp, (lx, LEGEND_Y))
        ir.paste(rp, (lx, LEGEND_Y))
        lx += STEP
    db.text((lx + 3, LEGEND_Y + 1), "More", font=fonts["tiny"], fill=0)


# ─── Public API ───────────────────────────────────────────────────────────────

def render(data: dict) -> tuple[Image.Image, Image.Image]:
    """
    Main render entry point. Produces two 800×480 'L' mode PIL images.

    Parameters
    ----------
    data : dict with keys:
        "username"  : str
        "calendar"  : { "totalContributions": int, "weeks": [...] }
        "repos"     : list of repo dicts
        "feed"      : list of event dicts

    Returns
    -------
    (image_black, image_red)
        Both mode 'L', size (800, 480), white background (255).
        Pass directly to epd.getbuffer().
    """
    fonts = _load_fonts()

    # White backgrounds (255 = no ink)
    ib = Image.new("L", (W, H), 255)
    ir = Image.new("L", (W, H), 255)

    _draw_header(ib, ir, data["username"], data["calendar"]["totalContributions"], fonts)
    _draw_dividers(ib)
    _draw_repo_panel(ib, ir, data["repos"], fonts)
    _draw_feed_panel(ib, ir, data["feed"], fonts)
    _draw_chart(ib, ir, data["calendar"]["weeks"], fonts)

    if ROTATE_180:
        ib = ib.rotate(180)
        ir = ir.rotate(180)

    return ib, ir
