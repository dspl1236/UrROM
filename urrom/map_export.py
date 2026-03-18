"""
urrom/map_export.py
===================
Export ROM map data to HTML or plain text.

Produces styled printable HTML tables matching UrROM's colour scheme:
  - Heat-map colours for fuel/ign maps  
  - Decoded values (°BTDC, AFR) with raw bytes in tooltips
  - Axis labels from get_axes()
  - ROM metadata header (variant, CRC32, export timestamp)
"""

from __future__ import annotations
import html
from datetime import datetime
from typing import Optional


# ── Colour helpers matching app/main.py ──────────────────────────────────────

def _lerp_hex(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3],16), int(c1[3:5],16), int(c1[5:7],16)
    r2, g2, b2 = int(c2[1:3],16), int(c2[3:5],16), int(c2[5:7],16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _ign_bg(decoded: float) -> str:
    """Dark blue (retard) → dark green (mid) → orange (advance)."""
    if decoded < 0:
        return _lerp_hex("#0a1a2e", "#102040", min(1.0, abs(decoded) / 10))
    elif decoded < 20:
        return _lerp_hex("#0a1f0a", "#1a3a1a", decoded / 20)
    else:
        return _lerp_hex("#1a3a1a", "#2a1800", min(1.0, (decoded - 20) / 25))


def _fuel_bg(raw: int) -> str:
    """Dark (lean) → neutral → orange (rich)."""
    if raw < 112:
        t = raw / 112
        return _lerp_hex("#0a1f2e", "#102040", t)
    elif raw < 144:
        return "#0d1117"
    else:
        t = min(1.0, (raw - 144) / 80)
        return _lerp_hex("#1a1000", "#2a1400", t)


def _heat_bg(raw: int, vmin: int, vmax: int) -> str:
    """Generic heat: cool (low) → warm (high)."""
    if vmax == vmin:
        return "#0d1117"
    t = (raw - vmin) / (vmax - vmin)
    return _lerp_hex("#0a1a2e", "#1a2a08", t)


def _text_for_bg(bg: str) -> str:
    r, g, b = int(bg[1:3],16), int(bg[3:5],16), int(bg[5:7],16)
    lum = 0.299*r + 0.587*g + 0.114*b
    return "#e8e8e8" if lum < 100 else "#1a1a1a"


# ── Main export function ──────────────────────────────────────────────────────

def export_map_html(
    rom: bytes,
    map_def,
    variant,
    *,
    title: str = "UrROM Map Export",
    include_raw: bool = True,
    include_header: bool = True,
) -> str:
    """
    Export a single map to a self-contained HTML string.

    Parameters
    ----------
    rom       : working-half bytes
    map_def   : MapDef instance
    variant   : ROMVariant instance
    title     : page/section title
    include_raw : show raw bytes in cell tooltip
    include_header : include ROM metadata header row
    """
    from urrom.ecu_profiles import read_map, get_axes

    raw_data = read_map(rom, map_def)
    decode   = map_def.decode
    rpm_axis, load_axis = get_axes(rom, map_def, variant)

    nrows = map_def.rows
    ncols = map_def.cols

    all_raws = [raw_data[r][c] for r in range(nrows) for c in range(ncols)]
    vmin, vmax = min(all_raws), max(all_raws)

    def _fmt(v) -> str:
        if isinstance(v, float) and v != int(v):
            return f"{v:.1f}"
        return str(int(v))

    row_labels = [_fmt(v) for v in reversed(rpm_axis)]
    col_labels = [_fmt(v) for v in load_axis]

    # Build table rows (display is inverted: row 0 = highest RPM)
    rows_html = []
    for disp_r in range(nrows):
        raw_r = nrows - 1 - disp_r
        cells = []
        for c in range(ncols):
            rv = raw_data[raw_r][c]
            if decode:
                dv = decode(rv)
                text = f"{dv:.1f}" if isinstance(dv, float) else str(dv)
                if map_def.map_type == "ign":
                    bg = _ign_bg(float(dv) if dv is not None else 0)
                elif map_def.map_type == "fuel":
                    bg = _fuel_bg(rv)
                else:
                    bg = _heat_bg(rv, vmin, vmax)
            else:
                text = str(rv)
                bg   = _heat_bg(rv, vmin, vmax)
            fg    = _text_for_bg(bg)
            tip   = f'raw={rv}' if include_raw else ''
            cells.append(
                f'<td style="background:{bg};color:{fg};text-align:center;'
                f'padding:3px 6px;font-size:11px;border:1px solid #1a2332;"'
                f' title="{html.escape(tip)}">{html.escape(text)}</td>'
            )
        rlab = html.escape(row_labels[disp_r])
        row_html = (
            f'<tr><th style="background:#131920;color:#6e7681;text-align:right;'
            f'padding:3px 8px;font-size:10px;border:1px solid #1a2332;">{rlab}</th>'
            + "".join(cells) + "</tr>"
        )
        rows_html.append(row_html)

    # Column header row
    col_headers = '<th style="background:#0d1117;border:1px solid #1a2332;"></th>'
    for lbl in col_labels:
        col_headers += (
            f'<th style="background:#131920;color:#6e7681;text-align:center;'
            f'padding:3px 6px;font-size:10px;border:1px solid #1a2332;">'
            f'{html.escape(lbl)}</th>'
        )

    # Axis labels
    rpm_label  = "RPM →" if map_def.map_type != "raw" else "Row →"
    load_label = "Load →" if map_def.map_type != "raw" else "Col →"

    unit_note = f" ({map_def.unit})" if map_def.unit else ""

    table = f"""
<table style="border-collapse:collapse;font-family:Consolas,monospace;">
  <caption style="color:#8b949e;font-size:11px;text-align:left;padding:4px 0;
    caption-side:top;">
    {html.escape(load_label)} &nbsp;&nbsp; axis: load / MAF
  </caption>
  <thead><tr>{col_headers}</tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>
<div style="color:#6e7681;font-size:10px;margin-top:4px;">
  ↕ {html.escape(rpm_label)} &nbsp;|&nbsp; values in {html.escape(map_def.unit or 'raw')}{html.escape(unit_note)}
</div>
"""

    if not include_header:
        return table

    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    name = variant.name if variant else "Unknown"
    pns  = ", ".join(variant.ecu_pns[:2]) if variant and variant.ecu_pns else "—"
    addr = f"WH 0x{map_def.main_addr:04X}"

    header = f"""
<div style="background:#131920;border:1px solid #1a2332;border-radius:4px;
  padding:12px 16px;margin-bottom:12px;font-family:sans-serif;">
  <div style="color:#c9d1d9;font-size:14px;font-weight:bold;">
    {html.escape(map_def.name)}</div>
  <div style="color:#8b949e;font-size:11px;margin-top:4px;">
    {html.escape(name)} &nbsp;·&nbsp; {html.escape(pns)} &nbsp;·&nbsp;
    {html.escape(addr)} &nbsp;·&nbsp; {nrows}×{ncols} &nbsp;·&nbsp;
    {html.escape(map_def.confidence)} &nbsp;·&nbsp; {ts}
  </div>
  <div style="color:#6e7681;font-size:10px;margin-top:4px;">
    {html.escape((map_def.description or '')[:120])}{'…' if len(map_def.description or '') > 120 else ''}
  </div>
</div>
"""
    return header + table


def export_full_rom_html(rom: bytes, variant, det=None) -> str:
    """
    Export all confirmed maps for a variant as a single HTML page.
    Suitable for printing or saving as reference.
    """
    from urrom.ecu_profiles import get_axes

    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    name = variant.name if variant else "Unknown"
    pns  = ", ".join(variant.ecu_pns[:2]) if variant and variant.ecu_pns else "—"
    crc  = f"0x{det.crc32:08X}" if det else "?"

    sections = []
    maps = [m for m in (variant.main_maps or [])
            if m.rows > 1 and m.cols > 1
            and m.confidence in ("CONFIRMED", "PROVISIONAL")]

    for m in maps:
        section = export_map_html(rom, m, variant,
                                  title=m.name, include_header=True,
                                  include_raw=True)
        sections.append(f'<div style="margin-bottom:32px;">{section}</div>')

    body = "\n".join(sections) or "<p style='color:#8b949e'>No confirmed maps.</p>"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>UrROM — {html.escape(name)}</title>
  <style>
    body {{background:#0d1117;color:#c9d1d9;font-family:Consolas,monospace;
           margin:24px;}}
    h1 {{color:#c9d1d9;font-size:16px;margin-bottom:4px;}}
    .meta {{color:#6e7681;font-size:11px;margin-bottom:24px;}}
    @media print {{body{{background:white;color:black;}}
      table{{page-break-inside:avoid;}} div{{page-break-inside:avoid;}}}}
  </style>
</head>
<body>
<h1>UrROM Map Reference — {html.escape(name)}</h1>
<div class="meta">
  ECU: {html.escape(pns)} &nbsp;·&nbsp; CRC32: {html.escape(crc)} &nbsp;·&nbsp;
  Exported: {ts} &nbsp;·&nbsp; {len(maps)} maps
</div>
{body}
</body>
</html>"""
