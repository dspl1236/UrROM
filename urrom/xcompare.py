"""
urrom/xcompare.py — Cross-family map comparison (e.g. 3B/404 vs AAN/ABY 551).

The in-app Compare tab needs two chips with the same layout.  Porting a
calibration between ECU generations needs the opposite: take two maps that
mean the same thing on different chips, decode each with its own formula,
put them on one set of axes, and look at the difference in real units.

    python -m urrom.cli xcompare roms/3b_fuel-ign_404aa.bin roms/aby_fuel-ign_551aa.bin
    python -m urrom.cli xcompare A.bin B.bin --role ign --csv out.csv

Roles pick "the main fuel map" / "the main ignition map" per variant via
ROLE_MAPS below; --a-map / --b-map override by address.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import bisect

from urrom.ecu_profiles import (
    normalize_rom, detect_rom, get_axes, read_map, MapDef, ROMVariant,
)

# Which map plays which role on each variant (working-half data addresses).
# 3B: ign map 2 is the main high-load map (docs 551_calibration_descriptors_RE.md §3c).
# 0E13-family / prjmod: PRJ XDF "Ign P/T (no knock)" 0x125F.  Best current guesses.
ROLE_MAPS: dict[str, dict[str, int]] = {
    "404":        {"fuel": 0x6A8E, "ign": 0x71F8},
    # 551 0x2E17 family (selector traced, docs §3e): main map = slot 0D of the coding-
    # plug-selected set → map 2 / 4 / 6.  Set B (map 4) is the middle coding class;
    # override with --b-map for a known coding.  Maps 3/5/7 are the alternates.
    "551C":       {"fuel": 0x2E17, "ign": 0x3598},
    "551B":       {"fuel": 0x2E17, "ign": 0x3594},
    "551D":       {"fuel": 0x2E17, "ign": 0x3598},
    "551B_D02":   {"fuel": 0x0E13, "ign": 0x125F},
    "551AA":      {"fuel": 0x0DEA, "ign": 0x1224},
    "551A":       {"fuel": 0x0E13, "ign": 0x125F},
    "551AA_0202": {"fuel": 0x0E13, "ign": 0x125F},
}


@dataclass
class Side:
    path: Path
    variant: ROMVariant
    wh: bytes
    map_def: MapDef
    rows: list          # row axis (RPM)
    cols: list          # column axis (load)
    data: list[list[float]]   # decoded

    @property
    def label(self) -> str:
        return f"{self.path.name} [{self.variant.software_id}] {self.map_def.name} @0x{self.map_def.main_addr:04X}"


def _load_side(path: Path, role: str, addr_override: int | None, raw_values: bool = False) -> Side:
    raw = path.read_bytes()
    if path.suffix.lower() == ".034":
        from urrom.descramble import descramble_034
        raw = bytes(descramble_034(raw))
    wh, _ = normalize_rom(raw)
    wh = bytes(wh)
    det = detect_rom(wh)
    if det.variant is None:
        raise SystemExit(f"{path.name}: variant not recognised")
    v = det.variant
    addr = addr_override if addr_override is not None else ROLE_MAPS.get(v.software_id, {}).get(role)
    if addr is None:
        raise SystemExit(f"{path.name}: no '{role}' map known for {v.software_id}; use --a-map/--b-map")
    m = next((m for m in v.main_maps if m.main_addr == addr), None)
    if m is None:
        raise SystemExit(f"{path.name}: no MapDef at 0x{addr:04X} in {v.software_id}")
    rows, cols = get_axes(wh, m, v)
    raw_grid = read_map(wh, m)
    dec = (lambda x: float(x)) if raw_values else (m.decode or (lambda x: float(x)))
    data = [[float(dec(c)) for c in r] for r in raw_grid]
    return Side(path, v, wh, m, list(rows), list(cols), data)


def _interp1(axis: list, values: list[float], x: float) -> float:
    """Linear interpolation with clamping at the ends (axis ascending)."""
    if x <= axis[0]:
        return values[0]
    if x >= axis[-1]:
        return values[-1]
    i = bisect.bisect_right(axis, x)
    x0, x1 = axis[i - 1], axis[i]
    t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
    return values[i - 1] + t * (values[i] - values[i - 1])


def resample(src: Side, rows: list, cols: list) -> list[list[float]]:
    """Bilinear-resample src.data onto (rows, cols)."""
    # first along columns for each source row, then along rows
    out = []
    for r in rows:
        col_vals = []
        for c in cols:
            per_row = [_interp1(src.cols, src.data[i], c) for i in range(len(src.rows))]
            col_vals.append(_interp1(src.rows, per_row, r))
        out.append(col_vals)
    return out


@dataclass
class XCompare:
    a: Side
    b: Side
    b_on_a: list[list[float]]
    delta: list[list[float]]        # b - a

    def summary(self) -> dict:
        flat = [d for row in self.delta for d in row]
        row_means = [sum(r) / len(r) for r in self.delta]
        col_means = [sum(self.delta[i][j] for i in range(len(self.delta))) / len(self.delta)
                     for j in range(len(self.delta[0]))]
        return {
            "mean": sum(flat) / len(flat),
            "min": min(flat), "max": max(flat),
            "row_means": row_means, "col_means": col_means,
        }


def xcompare(a_path: Path, b_path: Path, role: str = "fuel",
             a_map: int | None = None, b_map: int | None = None,
             raw_values: bool = False) -> XCompare:
    """raw_values=True compares undecoded bytes — use it when the two variants'
    decode formulas are not both trusted (e.g. the 551 ignition formula dispute)."""
    a = _load_side(a_path, role, a_map, raw_values)
    b = _load_side(b_path, role, b_map, raw_values)
    b_on_a = resample(b, a.rows, a.cols)
    delta = [[b_on_a[i][j] - a.data[i][j] for j in range(len(a.cols))] for i in range(len(a.rows))]
    return XCompare(a, b, b_on_a, delta)


def format_report(x: XCompare, unit: str = "") -> str:
    s = x.summary()
    a, b = x.a, x.b
    unit = unit or a.map_def.unit or ""
    lines = [
        f"A: {a.label}",
        f"   axes rows(RPM) {a.rows[0]}..{a.rows[-1]} x{len(a.rows)}  cols(load) {a.cols[0]}..{a.cols[-1]} x{len(a.cols)}  unit {a.map_def.unit}",
        f"B: {b.label}",
        f"   axes rows(RPM) {b.rows[0]}..{b.rows[-1]} x{len(b.rows)}  cols(load) {b.cols[0]}..{b.cols[-1]} x{len(b.cols)}  unit {b.map_def.unit}",
        f"B resampled onto A's axes.  delta = B - A  ({unit})",
        f"   mean {s['mean']:+.2f}   min {s['min']:+.2f}   max {s['max']:+.2f}",
        "",
        "row (rpm)  " + " ".join(f"{c:>6}" for c in a.cols),
    ]
    for i, r in enumerate(a.rows):
        lines.append(f"{r:>9}  " + " ".join(f"{d:+6.1f}" for d in x.delta[i]) + f"   | mean {s['row_means'][i]:+.2f}")
    lines.append("col means  " + " ".join(f"{m:+6.1f}" for m in s["col_means"]))
    return "\n".join(lines)


def write_csv(x: XCompare, path: Path) -> None:
    import csv
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rpm\\load"] + list(x.a.cols))
        w.writerow(["A: " + x.a.label])
        for i, r in enumerate(x.a.rows):
            w.writerow([r] + [f"{v:.2f}" for v in x.a.data[i]])
        w.writerow(["B (resampled): " + x.b.label])
        for i, r in enumerate(x.a.rows):
            w.writerow([r] + [f"{v:.2f}" for v in x.b_on_a[i]])
        w.writerow(["delta B-A"])
        for i, r in enumerate(x.a.rows):
            w.writerow([r] + [f"{v:+.2f}" for v in x.delta[i]])
