"""
urrom/xdf_import.py
===================
Import TunerPro XDF v1.50 files (XDFFORMAT XML).

Parses map addresses, dimensions, axis references and decode equations
from vwnut8392-format M2.3.2 XDFs and converts them to MapDef entries
suitable for use in UrROM.

Supported sources:
  - 8D0907551B RS2 Boost.xdf       (boost chip, vwnut8392/Matt@S&M Autosport)
  - RS2 551B fuel timing.xdf       (64KB doubled fuel/ign chip, vwnut8392)

XDF format notes:
  XDFFORMAT v1.50 uses XML.
  REGION size=0x7FFF → 32KB boost chip (direct file offsets)
  REGION size=0x10000 → 64KB fuel chip; WH offset = XDF addr − 0x8000
  All addresses in EMBEDDEDDATA mmedaddress attributes.
  Axis tables are XDFTABLE blocks; axes reference each other via XDFAXIS.
  Decode equations: "X" = raw; "X*0.6491-8.2186" = ign decode.
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# ── Regex helpers ─────────────────────────────────────────────────────────────

def _tag(block: str, tag: str) -> str:
    m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', block, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""

def _attr(block: str, attr: str) -> Optional[str]:
    m = re.search(rf'\b{attr}="([^"]*)"', block, re.IGNORECASE)
    return m.group(1) if m else None

def _hex(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.strip()
    try:
        return int(s, 16) if s.startswith("0x") or s.startswith("0X") else int(s)
    except ValueError:
        return None


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class XDFTable:
    uid:         str
    title:       str
    description: str
    address:     Optional[int]   # file offset (not WH-adjusted)
    rows:        int
    cols:        int
    equation:    str             # z-axis decode equation string
    x_uid:       str             # UID of X-axis table
    y_uid:       str             # UID of Y-axis table
    raw: str = field(default="", repr=False)


@dataclass
class XDFResult:
    tables:     list[XDFTable]
    region_size: int    # 0x7FFF=32KB boost, 0x10000=64KB fuel
    base_offset: int    # baseoffset from header
    title:       str
    author:      str
    warnings:    list[str]

    @property
    def wh_offset_delta(self) -> int:
        """Subtract this from XDF file address to get working-half offset."""
        return 0x8000 if self.region_size >= 0x10000 else 0


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_xdf(path: str | Path) -> XDFResult:
    """
    Parse a TunerPro XDF v1.50 file.

    Returns XDFResult containing all XDFTABLE entries with their
    addresses, dimensions, and decode equations.
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    warnings: list[str] = []

    # Header
    hdr_m = re.search(r'<XDFHEADER>(.*?)</XDFHEADER>', text, re.DOTALL | re.IGNORECASE)
    hdr = hdr_m.group(1) if hdr_m else ""
    title   = _tag(hdr, "deftitle")
    author  = _tag(hdr, "author")
    base_offset = _hex(_tag(hdr, "baseoffset")) or 0

    region_size = 0x10000  # default fuel/ign
    rgn_m = re.search(r'<REGION[^>]*size="(0x[0-9A-Fa-f]+)"', text)
    if rgn_m:
        region_size = int(rgn_m.group(1), 16)

    # Build UID → table map
    uid_map: dict[str, XDFTable] = {}
    for block in re.split(r'</XDFTABLE>', text):
        start = block.rfind('<XDFTABLE')
        if start == -1:
            continue
        block = block[start:]

        uid   = _attr(block, "uniqueid") or ""
        title_s = _tag(block, "title") or "Unnamed"
        desc  = _tag(block, "description").replace("&#013;","").replace("&#010;"," ").strip()

        # All EMBEDDEDDATA across all axes
        axes_blocks = re.findall(r'<XDFAXIS[^>]*id="([^"]*)"[^>]*>(.*?)</XDFAXIS>',
                                 block, re.DOTALL | re.IGNORECASE)

        address  = None
        rows     = 1
        cols     = 1
        equation = "X"
        x_uid    = ""
        y_uid    = ""

        for axis_id, axis_body in axes_blocks:
            emd_m = re.search(r'<EMBEDDEDDATA[^>]*/>', axis_body)
            if not emd_m:
                emd_m = re.search(r'<EMBEDDEDDATA[^>]*>', axis_body)
            if emd_m:
                emd = emd_m.group(0)
                addr_val  = _hex(_attr(emd, "mmedaddress"))
                rowcount  = _hex(_attr(emd, "mmedrowcount"))  or 1
                colcount  = _hex(_attr(emd, "mmedcolcount"))  or 1

                if axis_id.lower() == "z":
                    address = addr_val
                    rows    = rowcount
                    cols    = colcount
                    # Decode equation
                    math_m  = re.search(r'<MATH[^>]*equation="([^"]*)"', axis_body)
                    if math_m:
                        equation = math_m.group(1)

            # Axis references (link tables)
            link_m = re.search(r'<DALINK[^>]*uniqueid="(0x[^"]*)"', axis_body, re.IGNORECASE)
            if link_m:
                if axis_id.lower() == "x":
                    x_uid = link_m.group(1)
                elif axis_id.lower() == "y":
                    y_uid = link_m.group(1)

        if address is None:
            continue

        tbl = XDFTable(
            uid=uid, title=title_s, description=desc,
            address=address, rows=rows, cols=cols,
            equation=equation, x_uid=x_uid, y_uid=y_uid,
        )
        uid_map[uid] = tbl

    tables = list(uid_map.values())
    return XDFResult(
        tables=tables,
        region_size=region_size,
        base_offset=base_offset,
        title=title,
        author=author,
        warnings=warnings,
    )


# ── Equation → decode/encode helpers ─────────────────────────────────────────

def equation_to_decode(eq: str):
    """
    Convert a TunerPro equation string to a Python decode callable.

    Supported forms:
      "X"                  → identity
      "X*a+b" / "X*a-b"   → linear
      "(X*a)+b"            → parenthesised (TunerPro v1.50 style)
      "a*X+b"              → reversed
      "a/X"                → reciprocal (AFR = 1881.6/X)
    Returns None if equation is identity or unrecognized.
    """
    eq = eq.strip().replace(" ", "")
    if eq in ("X", ""):
        return None  # identity

    # Strip outer parens: "(X*a)-b" → "X*a-b"
    if eq.startswith("(") and ")" in eq:
        inner = eq[1:eq.index(")")]
        rest  = eq[eq.index(")")+1:]
        eq = inner + rest

    # X*a+b or X*a-b
    m = re.fullmatch(r'X\*([0-9.]+)([+-][0-9.]+)?', eq)
    if m:
        scale  = float(m.group(1))
        offset = float(m.group(2) or 0)
        return lambda x, s=scale, o=offset: round(x * s + o, 3)

    # a*X+b
    m = re.fullmatch(r'([0-9.]+)\*X([+-][0-9.]+)?', eq)
    if m:
        scale  = float(m.group(1))
        offset = float(m.group(2) or 0)
        return lambda x, s=scale, o=offset: round(x * s + o, 3)

    # a/X  (e.g. AFR = 1881.6/X)
    m = re.fullmatch(r'([0-9.]+)/X', eq)
    if m:
        numer = float(m.group(1))
        return lambda x, n=numer: round(n / x, 3) if x else None

    return None


def equation_to_encode(eq: str):
    """Inverse of equation_to_decode — raw = (decoded − b) / a."""
    eq = eq.strip().replace(" ", "")
    if eq in ("X", ""):
        return None

    # Strip parens
    if eq.startswith("(") and ")" in eq:
        inner = eq[1:eq.index(")")]
        rest  = eq[eq.index(")")+1:]
        eq = inner + rest

    m = re.fullmatch(r'X\*([0-9.]+)([+-][0-9.]+)?', eq)
    if not m:
        m = re.fullmatch(r'([0-9.]+)\*X([+-][0-9.]+)?', eq)
    if m:
        scale  = float(m.group(1))
        offset = float(m.group(2) or 0)
        if scale == 0:
            return None
        return lambda d, s=scale, o=offset: max(0, min(255, round((d - o) / s)))

    # a/X → encode: raw = a/decoded
    m = re.fullmatch(r'([0-9.]+)/X', eq)
    if m:
        numer = float(m.group(1))
        return lambda d, n=numer: max(1, min(255, round(n / d))) if d else 128

    return None


# ── Conversion to MapDef ──────────────────────────────────────────────────────

def xdf_to_mapdefs(result: XDFResult,
                   filter_min_cells: int = 4,
                   filter_max_cells: int = 65536) -> list:
    """
    Convert XDFResult tables to a list of MapDef-compatible dicts.

    Returns list of dicts with keys:
      name, description, main_addr (WH offset), rows, cols,
      map_type, unit, decode, encode, confidence, notes
    """
    from urrom.ecu_profiles import MapDef

    delta = result.wh_offset_delta
    out: list[MapDef] = []

    for t in result.tables:
        if t.address is None:
            continue
        cells = t.rows * t.cols
        if cells < filter_min_cells or cells > filter_max_cells:
            continue

        wh_addr = t.address - delta
        if wh_addr < 0:
            continue

        # Infer map type from equation
        eq = t.equation
        decode_fn = equation_to_decode(eq)
        encode_fn = equation_to_encode(eq)

        if "0.6491" in eq:
            map_type = "ign"
            unit     = "°BTDC"
        elif "0.0078125" in eq:
            map_type = "fuel"
            unit     = "relative"
        else:
            map_type = "raw"
            unit     = "raw"

        out.append(MapDef(
            name        = t.title,
            description = t.description or f"XDF import from {result.title}",
            main_addr   = wh_addr,
            rows        = t.rows,
            cols        = t.cols,
            map_type    = map_type,
            unit        = unit,
            decode      = decode_fn,
            encode      = encode_fn,
            confidence  = "PROVISIONAL",
            notes       = f"Imported from XDF. Equation: {eq}",
        ))

    return out


if __name__ == "__main__":
    import sys, pprint
    if len(sys.argv) < 2:
        print("Usage: python -m urrom.xdf_import <file.xdf>")
        sys.exit(1)
    r = parse_xdf(sys.argv[1])
    print(f"Title:  {r.title}")
    print(f"Author: {r.author}")
    print(f"Region: 0x{r.region_size:X}  (WH delta: 0x{r.wh_offset_delta:X})")
    print(f"Tables: {len(r.tables)}")
    for t in r.tables[:20]:
        wh = t.address - r.wh_offset_delta if t.address else None
        wh_s = f"WH 0x{wh:04X}" if wh is not None else "??"
        print(f"  {wh_s}  {t.rows}×{t.cols}  {t.equation:<25}  {t.title}")
