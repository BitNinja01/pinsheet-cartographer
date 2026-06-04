# plugins/cartographer/layout.py
"""Page layout composition for Cartographer yardage books.

Produces a single 4.25" x 14" SVG page containing:
  - Top half: hole layout diagram with hole number, par, and tee yardages
  - Bottom half: two configurable content slots (green grid, stats panel, or notes)
"""
from __future__ import annotations

import base64
import io

import cairosvg
import svgwrite

# Page dimensions in SVG points (72 points per inch)
PAGE_W = 306.0    # 4.25 * 72
PAGE_H = 1008.0   # 14 * 72
MARGIN = 18.0     # 0.25 * 72
PRINTABLE_W = PAGE_W - 2 * MARGIN  # 270pt
PRINTABLE_H = PAGE_H - 2 * MARGIN  # 972pt

# Half-page dimensions (7" tall, used for special pages)
HALF_PAGE_H = 504.0  # 7 * 72
HALF_PAGE_PRINTABLE_H = HALF_PAGE_H - 2 * MARGIN  # 468pt

TOP_HALF_H = 486.0   # 6.75 * 72
BOTTOM_HALF_H = 486.0
PAGE_CONTENT_H = PAGE_H / 2 - MARGIN  # 486pt — single page content height within a sheet
SLOT_H = 243.0       # half of bottom half

# Hole number circle (adjustable)
HOLE_NUMBER_CIRCLE_RADIUS = 16.0  # Experiment with this value

TEE_DISPLAY_ORDER = ["red", "gold", "yellow", "white", "blue", "combo", "black", "green"]
TEE_COLOUR_MAP = {
    "blue": "#1565C0", "white": "#555", "red": "#C62828",
    "gold": "#F9A825", "yellow": "#FDD835", "black": "#212121",
    "combo": "#7B1FA2", "green": "#2E7D32",
}

_CORNER_ARM = 8.0  # length of each chevron arm in points

# JetBrainsMonoNL exact metrics (queried from font file via fontTools)
# Units per em: 1000
# Advance width: 600 units  →  0.6 × font_size
# sTypoAscender: 1020 units →  1.02 × font_size
# sTypoDescender: -300 units → 0.30 × font_size (magnitude)
_CHAR_WIDTH_RATIO = 0.80    # tuned to cairosvg/Pango rendering — proportional fallback font
                            # renders wider than monospace metrics (measured: "GREEN : 270" = 99.5pt
                            # at 12pt for 11 chars → 0.756/char; 0.80 adds safety margin)
_ASCENDER_RATIO   = 1.02    # sTypoAscender / upm
_DESCENDER_RATIO  = 0.30    # abs(sTypoDescender) / upm


def _svg_to_png_data_uri(svg_str: str) -> str:
    png_bytes = cairosvg.svg2png(bytestring=svg_str.encode("utf-8"))
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _text_width(text: str, font_size: float) -> float:
    """Exact rendered width of text in points for JetBrainsMonoNL (monospace)."""
    return len(text) * font_size * _CHAR_WIDTH_RATIO


def _text_ascender(font_size: float) -> float:
    """Distance from baseline to top of capitals, in points."""
    return font_size * _ASCENDER_RATIO


def _text_descender(font_size: float) -> float:
    """Distance from baseline to bottom of descenders, in points (positive value)."""
    return font_size * _DESCENDER_RATIO


def _measure_text_width(text: str, font_size: float, font_family: str) -> float:
    """Measure actual rendered width of text in points using cairosvg.

    Renders the text to a PNG and finds the rightmost non-white pixel.
    Accurate regardless of font fallback or proportional glyph widths.
    """
    import cairosvg
    from PIL import Image

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="500pt" height="40pt" viewBox="0 0 500 40">'
        f'<rect x="0" y="0" width="500" height="40" fill="white"/>'
        f'<text x="0" y="25" font-size="{font_size}pt" font-family="{font_family}" '
        f'fill="black" text-anchor="start">{text}</text>'
        f'</svg>'
    )
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=1000)
    img = Image.open(io.BytesIO(png)).convert("L")
    w, h = img.size
    scale = w / 500
    row = list(img.getdata())[h // 2 * w: (h // 2 + 1) * w]
    rightmost = max((i for i, p in enumerate(row) if p < 200), default=0)
    return rightmost / scale


def _wrap_text(text: str, font_size: int, max_width: float) -> list[str]:
    """Split text into lines that each fit within max_width at the given font size.

    Uses a conservative character-width estimate (0.65 × font_size for
    JetBrainsMono) rather than font-file measurement, to match SVG rendering.
    """
    char_w = font_size * 0.65
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test = " ".join(current + [word])
        if len(test) * char_w <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines if lines else [text]


def _draw_corner_marks(
    dwg: svgwrite.Drawing,
    x: float,
    y: float,
    w: float,
    h: float,
    arm: float | None = None,
) -> None:
    """Draw L-shaped corner marks at the four corners of a rectangle."""
    if arm is None:
        arm = _CORNER_ARM
    s = {"stroke": "#000000", "stroke_width": 0.5, "stroke_linecap": "square"}
    # top-left
    dwg.add(dwg.line(start=(x, y), end=(x + arm, y), **s))
    dwg.add(dwg.line(start=(x, y), end=(x, y + arm), **s))
    # top-right
    dwg.add(dwg.line(start=(x + w, y), end=(x + w - arm, y), **s))
    dwg.add(dwg.line(start=(x + w, y), end=(x + w, y + arm), **s))
    # bottom-left
    dwg.add(dwg.line(start=(x, y + h), end=(x + arm, y + h), **s))
    dwg.add(dwg.line(start=(x, y + h), end=(x, y + h - arm), **s))
    # bottom-right
    dwg.add(dwg.line(start=(x + w, y + h), end=(x + w - arm, y + h), **s))
    dwg.add(dwg.line(start=(x + w, y + h), end=(x + w, y + h - arm), **s))


def flip_page_svg(svg_str: str, w: float, h: float) -> str:
    """Return a new SVG that displays svg_str rotated 180° around its centre."""
    dwg = svgwrite.Drawing(size=(f"{w}pt", f"{h}pt"), viewBox=f"0 0 {w} {h}")
    g = dwg.g(transform=f"rotate(180, {w / 2}, {h / 2})")
    g.add(dwg.image(
        href=_svg_to_png_data_uri(svg_str),
        insert=(0, 0),
        size=(w, h),
    ))
    dwg.add(g)
    return dwg.tostring()


def compose_sheet(top_svg: str, bottom_svg: str) -> str:
    """Combine two PAGE_CONTENT_H SVGs into one PAGE_H sheet with corner marks."""
    dwg = svgwrite.Drawing(
        size=(f"{PAGE_W}pt", f"{PAGE_H}pt"),
        viewBox=f"0 0 {PAGE_W} {PAGE_H}",
    )

    dwg.add(dwg.rect(insert=(0, 0), size=(PAGE_W, PAGE_H), fill="white"))

    dwg.add(dwg.image(
        href=_svg_to_png_data_uri(top_svg),
        insert=(0, MARGIN),
        size=(PAGE_W, PAGE_CONTENT_H),
    ))

    dwg.add(dwg.image(
        href=_svg_to_png_data_uri(bottom_svg),
        insert=(0, PAGE_H / 2),
        size=(PAGE_W, PAGE_CONTENT_H),
    ))

    _draw_corner_marks(dwg, MARGIN, MARGIN, PRINTABLE_W, PAGE_CONTENT_H)
    _draw_corner_marks(dwg, MARGIN, PAGE_H / 2, PRINTABLE_W, PAGE_CONTENT_H)

    return dwg.tostring()


def render_hole_page(
    hole_svg: str,
    hole_num: int,
    par: int,
    tee_yardages: dict,
) -> str:
    """Content-only PAGE_CONTENT_H SVG: hole layout with overlays."""
    dwg = svgwrite.Drawing(
        size=(f"{PAGE_W}pt", f"{PAGE_CONTENT_H}pt"),
        viewBox=f"0 0 {PAGE_W} {PAGE_CONTENT_H}",
    )

    dwg.add(dwg.image(
        href=_svg_to_png_data_uri(hole_svg),
        insert=(MARGIN, 0),
        size=(PRINTABLE_W, PAGE_CONTENT_H),
    ))

    # --- Block 1: Hole number / Par (top-right corner) ---
    pad = MARGIN / 2
    par_label = f"Par {par}"

    # Content bbox — exact font metrics
    hn_content_w = max(2 * HOLE_NUMBER_CIRCLE_RADIUS, _text_width(par_label, 9))
    hn_content_h = 2 * HOLE_NUMBER_CIRCLE_RADIUS + 4 + _text_ascender(9) + _text_descender(9)

    # Rect: top-right corner at printable top-right (x=PAGE_W-MARGIN, y=0)
    hn_rect_w = hn_content_w + 2 * pad
    hn_rect_h = hn_content_h + 2 * pad
    hn_rect_x = PAGE_W - MARGIN - hn_rect_w
    hn_rect_y = 0

    dwg.add(dwg.rect(
        insert=(hn_rect_x, hn_rect_y),
        size=(hn_rect_w, hn_rect_h),
        fill="white",
        fill_opacity=1.0,
        stroke="#000000",
        stroke_width=0.5,
        rx=3,
        ry=3,
    ))

    # Circle: centred in rect horizontally, pad from rect top
    hn_cx = hn_rect_x + hn_rect_w / 2
    hn_cy = hn_rect_y + pad + HOLE_NUMBER_CIRCLE_RADIUS
    dwg.add(dwg.circle(
        center=(hn_cx, hn_cy),
        r=HOLE_NUMBER_CIRCLE_RADIUS,
        fill="white",
        stroke="#000000",
        stroke_width=1.0,
    ))
    dwg.add(dwg.text(
        str(hole_num),
        insert=(hn_cx, hn_cy),
        font_size="14pt",
        font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
        fill="#212121",
        text_anchor="middle",
        dominant_baseline="central",
    ))

    # Par text: 4pt gap below circle bottom, then ascender
    par_y = hn_cy + HOLE_NUMBER_CIRCLE_RADIUS + 4 + _text_ascender(9)
    dwg.add(dwg.text(
        par_label,
        insert=(hn_cx, par_y),
        font_size="9pt",
        font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
        fill="#212121",
        text_anchor="middle",
    ))

    known = {n: i for i, n in enumerate(TEE_DISPLAY_ORDER)}
    sorted_tees = sorted(
        tee_yardages.items(),
        key=lambda x: (known.get(x[0], 999), x[1])
    )

    if sorted_tees:
        pad = MARGIN / 2
        fs = 12
        line_h = _text_ascender(fs) + _text_descender(fs)  # 15.84pt
        n = len(sorted_tees)
        line_texts = [f"{tee_name.upper()} : {yardage}" for tee_name, yardage in sorted_tees]

        # Content bbox — measure actual rendered width via cairosvg (exact, adapts to any tee name)
        _ff = "JetBrainsMonoNL NFM, JetBrainsMono, monospace"
        content_w = max(_measure_text_width(t, fs, _ff) for t in line_texts)
        content_h = _text_ascender(fs) + (n - 1) * line_h + _text_descender(fs)

        # Rect: bbox + padding, bottom-right corner at printable bottom-right
        yd_rect_w = content_w + 2 * pad
        yd_rect_h = content_h + 2 * pad
        yd_rect_x = PAGE_W - MARGIN - yd_rect_w
        yd_rect_y = PAGE_CONTENT_H - yd_rect_h

        dwg.add(dwg.rect(
            insert=(yd_rect_x, yd_rect_y),
            size=(yd_rect_w, yd_rect_h),
            fill="white",
            fill_opacity=1.0,
            stroke="#000000",
            stroke_width=0.5,
            rx=3,
            ry=3,
        ))

        # Text: right-aligned to rect interior right edge — colons align naturally
        text_x = yd_rect_x + yd_rect_w - pad
        text_y = yd_rect_y + pad + _text_ascender(fs)
        for tee_name, yardage in sorted_tees:
            dwg.add(dwg.text(
                f"{tee_name.upper()} : {yardage}",
                insert=(text_x, text_y),
                font_size=f"{fs}pt",
                font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
                fill="#000000",
                text_anchor="end",
            ))
            text_y += line_h

    return dwg.tostring()


def render_bottom_slots(
    slot1_content: str,
    slot2_content: str,
    slot1_svg: str = "",
    slot2_svg: str = "",
    stats_data: dict | None = None,
    hole_num: int = 1,
) -> str:
    """Content-only PAGE_CONTENT_H SVG: two stacked slots with no gap."""
    dwg = svgwrite.Drawing(
        size=(f"{PAGE_W}pt", f"{PAGE_CONTENT_H}pt"),
        viewBox=f"0 0 {PAGE_W} {PAGE_CONTENT_H}",
    )

    slot_h = PAGE_CONTENT_H / 2

    _render_slot(dwg, slot1_content, slot1_svg, stats_data, hole_num,
                 MARGIN, 0, PRINTABLE_W, slot_h)
    _render_slot(dwg, slot2_content, slot2_svg, stats_data, hole_num,
                 MARGIN, slot_h, PRINTABLE_W, slot_h)

    return dwg.tostring()


def _render_slot(
    dwg: svgwrite.Drawing,
    content_type: str,
    svg_content: str,
    stats_data: dict | None,
    hole_num: int,
    x: float,
    y: float,
    width: float,
    height: float,
) -> None:
    """Render a single slot (green grid, stats panel, or notes)."""
    if content_type == "green_grid":
        if svg_content:
            dwg.add(dwg.image(
                href=_svg_to_png_data_uri(svg_content),
                insert=(x, y),
                size=(width, height),
            ))

    elif content_type == "stats_panel":
        # 2x2 grid of stat boxes
        box_w = width / 2
        box_h = height / 2

        stats = []
        if stats_data and hole_num in stats_data:
            hole_stats = stats_data[hole_num]
            stats = [
                ("FAIRWAY MISSES", hole_stats.get("fairway_misses", "L: \u00b7 R:")),
                ("GIR MISSES", hole_stats.get("gir_misses", "S: \u00b7 LO: \u00b7 L: \u00b7 R:")),
                ("SCORE", hole_stats.get("benchmark", "Avg: \u00b7 Exp:")),
                ("PENALTIES", hole_stats.get("penalties", "Avg:")),
            ]
        else:
            stats = [
                ("FAIRWAY MISSES", "L: \u00b7 R:"),
                ("GIR MISSES", "S: \u00b7 LO: \u00b7 L: \u00b7 R:"),
                ("SCORE", "Avg: \u00b7 Exp:"),
                ("PENALTIES", "Avg:"),
            ]

        positions = [
            (x, y),
            (x + box_w, y),
            (x, y + box_h),
            (x + box_w, y + box_h),
        ]

        for (bx, by), (title, value) in zip(positions, stats):
            dwg.add(dwg.rect(
                insert=(bx, by),
                size=(box_w, box_h),
                fill="white",
                stroke="#000000",
                stroke_width=0.5,
            ))

            rows = value.split(" \u00b7 ")
            header_h = 22
            row_h = (box_h - header_h) / max(len(rows), 1)
            col1_w = 38

            dwg.add(dwg.text(
                title,
                insert=(bx + box_w / 2, by + header_h / 2 + 3),
                font_size="8pt",
                font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
                fill="#000000",
                font_weight="bold",
                text_anchor="middle",
            ))

            dwg.add(dwg.line(
                start=(bx, by + header_h),
                end=(bx + box_w, by + header_h),
                stroke="#000000",
                stroke_width=0.5,
            ))

            dwg.add(dwg.line(
                start=(bx + col1_w, by + header_h),
                end=(bx + col1_w, by + box_h),
                stroke="#000000",
                stroke_width=0.3,
            ))

            for i, part in enumerate(rows):
                row_top = by + header_h + i * row_h
                if " " in part:
                    lbl, val = part.split(" ", 1)
                else:
                    lbl = part
                    val = ""

                dwg.add(dwg.text(
                    lbl,
                    insert=(bx + col1_w - 4, row_top + row_h / 2 + 3),
                    font_size="9pt",
                    font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
                    fill="#000000",
                    text_anchor="end",
                ))

                if val:
                    dwg.add(dwg.text(
                        val,
                        insert=(bx + col1_w + 4, row_top + row_h / 2 + 3),
                        font_size="9pt",
                        font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
                        fill="#000000",
                        text_anchor="start",
                    ))

                if i < len(rows) - 1:
                    dwg.add(dwg.line(
                        start=(bx, row_top + row_h),
                        end=(bx + box_w, row_top + row_h),
                        stroke="#000000",
                        stroke_width=0.3,
                    ))

    elif content_type == "notes":
        # Ruled lines
        line_spacing = 24.0
        line_y = y + line_spacing
        while line_y < y + height - 6:
            dwg.add(dwg.line(
                start=(x, line_y),
                end=(x + width, line_y),
                stroke="#000000",
                stroke_width=0.5,
            ))
            line_y += line_spacing


def compose_front_page(
    course_name: str,
    location: dict[str, str] | None = None,
    total_par: int = 0,
    tee_totals: dict[str, int] | None = None,
) -> str:
    # --- Compute phase ---
    title_max_w = PRINTABLE_W - 88
    title_lines = _wrap_text(course_name, 18, title_max_w)
    title_line_h = 26.0

    loc_str1 = ""
    loc_str2 = ""
    if location:
        city = location.get("city", "")
        state = location.get("state") or location.get("state/province", "")
        country = location.get("country", "")
        line1_parts = [p for p in [city, state] if p]
        if line1_parts:
            loc_str1 = ", ".join(line1_parts)
        if country:
            loc_str2 = country
    has_loc = bool(loc_str1 or loc_str2)

    sorted_tees: list[tuple[str, int]] = []
    if tee_totals:
        known = {n: i for i, n in enumerate(TEE_DISPLAY_ORDER)}
        sorted_tees = sorted(
            tee_totals.items(),
            key=lambda x: (known.get(x[0], 999), x[1]),
        )

    # Bounding-box heights (ascender above first baseline + descender below last baseline)
    _ASC = lambda fs: fs       # ascender: font_size above baseline
    _DSC = lambda fs: fs * 0.25  # descender: 0.25 × font_size below baseline
    title_h = _ASC(18) + (len(title_lines) - 1) * title_line_h + _DSC(18)
    if has_loc:
        if loc_str2:
            loc_h = _ASC(11) + 16 + _DSC(10)  # two lines: asc1 + offset + desc2
        else:
            loc_h = _ASC(11) + _DSC(11)       # one line
    else:
        loc_h = 0
    par_h = _ASC(14) + _DSC(14)
    n_tees = len(sorted_tees)
    tee_h = (_ASC(11) + (n_tees - 1) * 18 + _DSC(11)) if n_tees else 0

    # Fixed gap between sections, block centred vertically
    gap = 36.0
    total_block_h = title_h + loc_h + par_h + tee_h + 3 * gap
    title_top = (PAGE_CONTENT_H - total_block_h) / 2
    loc_top = title_top + title_h + gap
    par_top = loc_top + loc_h + gap
    tee_top = par_top + par_h + gap

    # Text baselines from bbox tops
    name_y = title_top + _ASC(18)
    title_bottom = name_y + (len(title_lines) - 1) * title_line_h

    loc_offset = loc_top + _ASC(11)
    if loc_str2:
        loc2_y = loc_offset + 16 if loc_str1 else loc_offset
        loc_bottom = loc2_y + _DSC(10)
    elif loc_str1:
        loc_bottom = loc_offset + _DSC(11)
    else:
        loc_bottom = title_bottom + _DSC(18)

    par_y = par_top + _ASC(14)

    table_y = tee_top + _ASC(11) if n_tees else par_y

    # --- Draw phase ---
    dwg = svgwrite.Drawing(
        size=(f"{PAGE_W}pt", f"{PAGE_CONTENT_H}pt"),
        viewBox=f"0 0 {PAGE_W} {PAGE_CONTENT_H}",
    )

    for i, line in enumerate(title_lines):
        dwg.add(dwg.text(
            line,
            insert=(PAGE_W / 2, name_y + i * title_line_h),
            font_size="18pt",
            font_weight="bold",
            font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
            fill="#212121",
            text_anchor="middle",
        ))

    if loc_str1:
        dwg.add(dwg.text(
            loc_str1,
            insert=(PAGE_W / 2, loc_offset),
            font_size="11pt",
            font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
            fill="#555",
            text_anchor="middle",
        ))
    if loc_str2:
        dwg.add(dwg.text(
            loc_str2,
            insert=(PAGE_W / 2, loc2_y),
            font_size="10pt",
            font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
            fill="#555",
            text_anchor="middle",
        ))

    dwg.add(dwg.text(
        f"Par {total_par}",
        insert=(PAGE_W / 2, par_y),
        font_size="14pt",
        font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
        fill="#212121",
        text_anchor="middle",
    ))

    if sorted_tees:
        col_gap = 4.0
        space_w = _text_width(" ", 11)
        colon_w = _text_width(":", 11)
        col_x = PAGE_W / 2 + colon_w / 6
        y = table_y
        for tee_name, yardage in sorted_tees:
            dwg.add(dwg.text(
                f"{tee_name.upper()} :",
                insert=(col_x, y),
                font_size="11pt",
                font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
                fill="#212121",
                text_anchor="end",
            ))
            dwg.add(dwg.text(
                str(yardage),
                insert=(col_x + col_gap + space_w, y),
                font_size="11pt",
                font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
                fill="#212121",
                text_anchor="start",
            ))
            y += 18

    # 3 dots at gap midpoints
    dot_cx = PAGE_W / 2
    dots = [
        (title_top + title_h + gap / 2),
        (loc_top + loc_h + gap / 2),
        (par_top + par_h + gap / 2),
    ]
    for dot_y in dots:
        dwg.add(dwg.circle(center=(dot_cx, dot_y), r=1.5, fill="#999"))

    return dwg.tostring()


def compose_back_page(
    full_course_svg: str | None = None,
) -> str:
    dwg = svgwrite.Drawing(
        size=(f"{PAGE_W}pt", f"{PAGE_CONTENT_H}pt"),
        viewBox=f"0 0 {PAGE_W} {PAGE_CONTENT_H}",
    )

    if full_course_svg:
        overview_h = PAGE_CONTENT_H - 4 * MARGIN - 24
        dwg.add(dwg.image(
            href=_svg_to_png_data_uri(full_course_svg),
            insert=(MARGIN, MARGIN),
            size=(PRINTABLE_W, overview_h),
        ))

    dwg.add(dwg.text(
        "PinSheet",
        insert=(PAGE_W / 2, PAGE_CONTENT_H - MARGIN - 20),
        font_size="18pt",
        font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
        fill="#212121",
        text_anchor="middle",
    ))

    return dwg.tostring()


def compose_chart_page(
    title: str = "Club Distances",
) -> str:
    dwg = svgwrite.Drawing(
        size=(f"{PAGE_W}pt", f"{PAGE_CONTENT_H}pt"),
        viewBox=f"0 0 {PAGE_W} {PAGE_CONTENT_H}",
    )

    dwg.add(dwg.text(
        title,
        insert=(PAGE_W / 2, 35),
        font_size="14pt",
        font_weight="bold",
        font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
        fill="#000000",
        text_anchor="middle",
    ))

    cols = 4
    rows = 15
    headers = ["Club", "Carry", "Half", "Max"]
    grid_top = 55
    grid_bottom = PAGE_CONTENT_H
    grid_left = MARGIN
    grid_right = PAGE_W - MARGIN
    gw = grid_right - grid_left
    gh = grid_bottom - grid_top
    cw = gw / cols
    rh = gh / rows

    for row in range(rows):
        for col in range(cols):
            x = grid_left + col * cw
            y = grid_top + row * rh
            dwg.add(dwg.rect(
                insert=(x, y),
                size=(cw, rh),
                fill="white",
                stroke="#000000",
                stroke_width=0.5,
            ))
            if row == 0:
                dwg.add(dwg.text(
                    headers[col],
                    insert=(x + cw / 2, y + rh / 2 + 4),
                    font_size="11pt",
                    font_family="JetBrainsMonoNL NFM, JetBrainsMono, monospace",
                    fill="#000000",
                    text_anchor="middle",
                ))

    return dwg.tostring()


def compose_notes_page() -> str:
    dwg = svgwrite.Drawing(
        size=(f"{PAGE_W}pt", f"{PAGE_CONTENT_H}pt"),
        viewBox=f"0 0 {PAGE_W} {PAGE_CONTENT_H}",
    )

    line_spacing = 24.0
    line_y = 0
    while line_y < PAGE_CONTENT_H - 6:
        dwg.add(dwg.line(
            start=(MARGIN, line_y),
            end=(PAGE_W - MARGIN, line_y),
            stroke="#000000",
            stroke_width=0.5,
        ))
        line_y += line_spacing

    return dwg.tostring()



