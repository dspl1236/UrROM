"""
urrom/maf_swap.py — re-linearise a 3B/RR/S2 (404) chip for a different MAF sensor.

How the 3B linearises its hot-wire (docs/3B_load_headroom_RE.md §2, code
0x1CE8–0x1D4D and 0x0735–0x0761):

    rate  = MAF pulses per crank segment / crank period       (a pulse *rate*)
    4Bh   = rate auto-ranged:  rate <  0x10 -> x16 (class A, table 0x7000)
                               rate <  0x40 -> x4  (class B, table 0x7019)
                               else            x1  (class C, table 0x7032)
    46h   = table[class](4Bh)                                  offset vs rate
    air   = (0x00F4 + 46h) x GAIN(0x6351) x pulses >> exp       air per rev

so the three tables are ONE curve — offset versus pulse rate — split into three
auto-ranged pieces, 10 / 13 / 10 points, indexed by RAM 4Bh (descriptor input
0x4B).  M(x) = 244 + offset(x) is the sensor's linearisation multiplier.

For a different sensor, at the same true air the pulse count p' and rate x'
differ from the hot-wire's p and x, and the requirement is

    M'(x') = M(x) * p / p'          (same air per rev from different pulses)

which is exactly what a pair of logs gives: the stock sensor's (x, p) and the
new sensor's (x', p') at matched operating points.  fit_from_logs() bins the
new-sensor samples onto the three tables' axes, solves for M', spreads any
overflow into GAIN, and apply() writes the bytes and the checksum.

uniform() is the shortcut for a sensor that is just a scaled version of the
hot-wire (p' = p / r): M' = M * r everywhere.  It is a bench-less first pass,
not a calibration.

CSV logs: columns  point, rpm, rate, pulses  (+ optional load, lambda).
`point` is a label for the steady state the sample came from (idle, cruise2k,
cruise3k, wot...) so the two logs can be matched; KWPBridge's 3B RAM group 101
logs rate (4Bh) and pulses (42h:43h) directly.
"""
from __future__ import annotations

import csv
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from urrom.ecu_profiles import VARIANT_404, apply_checksum_for, read_descriptor_axis_1d

BASE = 0x00F4                       # 0x634F:0x6350 on every stock chip
GAIN_ADDR, EXP_ADDR = 0x6351, 0x6352
TABLES = ((0x7000, 10, 16), (0x7019, 13, 4), (0x7032, 10, 1))   # (data, points, 4Bh scale)
CLASS_LIMITS = (0x10, 0x40)        # rate < 0x10 -> A, < 0x40 -> B, else C


@dataclass
class MafReport:
    lines: list = field(default_factory=list)
    gain_before: int = 0
    gain_after: int = 0

    def text(self) -> str:
        return "\n".join(self.lines)


def tables(rom: bytes) -> list[tuple[int, list[int], list[int]]]:
    """[(data_addr, axis in 4Bh units, offsets)] for the three range tables."""
    out = []
    for addr, n, _ in TABLES:
        axis = read_descriptor_axis_1d(rom, addr, n)
        if axis is None:
            raise ValueError(f"no 1-D descriptor for the MAF table at 0x{addr:04X}")
        out.append((addr, list(axis), list(rom[addr:addr + n])))
    return out


def class_of(rate: float) -> int:
    return 0 if rate < CLASS_LIMITS[0] else (1 if rate < CLASS_LIMITS[1] else 2)


def _interp(axis: list, vals: list, x: float) -> float:
    if x <= axis[0]:
        return vals[0]
    if x >= axis[-1]:
        return vals[-1]
    for i in range(1, len(axis)):
        if x <= axis[i]:
            a, b = axis[i - 1], axis[i]
            return vals[i - 1] + (vals[i] - vals[i - 1]) * (x - a) / (b - a)
    return vals[-1]


def multiplier(rom: bytes, rate: float) -> float:
    """M(rate) = BASE + offset, using the same class/auto-range the firmware uses."""
    c = class_of(rate)
    addr, axis, offs = tables(rom)[c]
    k4b = rate * TABLES[c][2]
    return BASE + _interp(axis, offs, k4b)


def read_log(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({"point": (r.get("point") or "").strip(),
                             "rpm": float(r["rpm"]), "rate": float(r["rate"]), "pulses": float(r["pulses"]),
                             "lambda": float(r["lambda"]) if r.get("lambda") not in (None, "") else 1.0})
            except (KeyError, ValueError):
                continue
    if not rows:
        raise ValueError(f"{path}: no usable rows (need point, rpm, rate, pulses)")
    return rows


def _rpm_bin(rpm: float, width: float = 250.0) -> int:
    return int(rpm // width)


def fit_from_logs(rom: bytes, stock_log: list[dict], new_log: list[dict],
                  rpm_bin: float = 250.0) -> tuple[dict[int, list[int]], int, MafReport]:
    """
    Solve M'(x') = M(x) * p / p' at matched (point, rpm bin) operating points and
    bin the results onto the three tables' axes.  Returns ({table_addr: new offsets},
    new GAIN, report).  Points the logs never reached keep the stock value scaled by
    the nearest fitted ratio.
    """
    rep = MafReport()
    key = lambda r: (r["point"], _rpm_bin(r["rpm"], rpm_bin))
    stock: dict[tuple, list[dict]] = {}
    for r in stock_log:
        stock.setdefault(key(r), []).append(r)
    # each matched new-sensor sample -> (class', 4Bh', required M')
    samples: list[tuple[int, float, float]] = []
    for r in new_log:
        ref = stock.get(key(r))
        if not ref:
            continue
        p_old = statistics.median(s["pulses"] for s in ref)
        x_old = statistics.median(s["rate"] for s in ref)
        lam = statistics.median(s["lambda"] for s in ref) / (r["lambda"] or 1.0)
        m_old = multiplier(rom, x_old)
        if r["pulses"] <= 0:
            continue
        m_new = m_old * p_old / r["pulses"] * lam
        c = class_of(r["rate"])
        samples.append((c, r["rate"] * TABLES[c][2], m_new))
    if not samples:
        raise ValueError("no operating points matched between the two logs (check the `point` labels and rpm)")
    rep.lines.append(f"{len(samples)} matched samples over {len({key(r) for r in new_log})} operating points")

    new_tables: dict[int, list[float]] = {}
    ratios = []
    for c, (addr, axis, offs) in enumerate(tables(rom)):
        per_point: dict[int, list[float]] = {}
        for cc, k4b, m_new in samples:
            if cc != c:
                continue
            # attribute the sample to the nearest axis point
            i = min(range(len(axis)), key=lambda j: abs(axis[j] - k4b))
            per_point.setdefault(i, []).append(m_new)
        fitted = {i: statistics.median(v) for i, v in per_point.items()}
        for i, m in fitted.items():
            ratios.append(m / (BASE + offs[i]))
        if fitted:
            # unfitted points: stock value x the nearest fitted point's ratio
            vals = []
            for i in range(len(axis)):
                if i in fitted:
                    vals.append(fitted[i])
                else:
                    j = min(fitted, key=lambda k: abs(k - i))
                    vals.append((BASE + offs[i]) * fitted[j] / (BASE + offs[j]))
            new_tables[addr] = vals
            rep.lines.append(f"table 0x{addr:04X}: {len(fitted)}/{len(axis)} points fitted")
        else:
            new_tables[addr] = [float(BASE + o) for o in offs]
            rep.lines.append(f"table 0x{addr:04X}: no samples in this range, left at stock (scaled below)")
    overall = statistics.median(ratios) if ratios else 1.0
    for addr, _, offs in tables(rom):
        if addr in new_tables and all(abs(v - (BASE + o)) < 1e-9 for v, o in zip(new_tables[addr], offs)):
            new_tables[addr] = [(BASE + o) * overall for o in offs]     # no samples: stock shape x overall ratio
    return _finish(rom, new_tables, rep)


def uniform(rom: bytes, ratio: float) -> tuple[dict[int, list[int]], int, MafReport]:
    """M' = M * ratio everywhere (a sensor that gives 1/ratio times the hot-wire's pulses)."""
    rep = MafReport([f"uniform ratio x{ratio:g} on every point of the three range tables"])
    new = {addr: [(BASE + o) * ratio for o in offs] for addr, _, offs in tables(rom)}
    return _finish(rom, new, rep)


def _finish(rom: bytes, m_tables: dict[int, list[float]], rep: MafReport):
    """
    Turn required multipliers M' into offset bytes and a GAIN.  The offset can only
    add to the fixed BASE (244), so a sensor that needs LESS multiplier than BASE
    somewhere, or MORE than BASE+255, is handled by scaling every table by g and
    GAIN by 1/g (air = M x GAIN x pulses is unchanged).  If the sensor's curve spans
    more than (BASE+255)/BASE ~ 2.04x, the ends are clamped and reported.
    """
    gain = rom[GAIN_ADDR]
    lo = min(min(v) for v in m_tables.values()); hi = max(max(v) for v in m_tables.values())
    g = 1.0
    if lo < BASE:
        g = BASE / lo                       # lift the low end onto the offset floor
    if hi * g > BASE + 255:
        g = (BASE + 255) / hi               # ... unless that overflows the top: favour the top
    new_gain = max(1, min(255, int(gain / g + 0.5)))
    offs = {addr: [max(0, min(255, int(v * g - BASE + 0.5))) for v in vals] for addr, vals in m_tables.items()}
    rep.gain_before, rep.gain_after = gain, new_gain
    for addr, _, old in tables(rom):
        rep.lines.append(f"  0x{addr:04X}: {old} -> {offs[addr]}")
    rep.lines.append(f"  GAIN 0x{GAIN_ADDR:04X}: {gain} -> {new_gain}" + ("" if g == 1.0 else f"  (tables scaled x{g:.3f}, gain x{1 / g:.3f})"))
    clipped = sum(1 for addr, vals in m_tables.items() for v in vals if not (BASE <= v * g <= BASE + 255))
    if clipped:
        rep.lines.append(f"  WARNING: {clipped} points clipped — the sensor's curve spans more than the offset "
                         f"range can hold ({(BASE + 255) / BASE:.2f}x); the exponent byte 0x6352 would have to move")
    if not 1 <= gain / g <= 255:
        rep.lines.append("  WARNING: GAIN out of range; the exponent byte 0x6352 would have to move (not automated)")
    return offs, new_gain, rep


def apply(rom: bytes, offs: dict[int, list[int]], gain: int) -> bytearray:
    out = bytearray(rom)
    for addr, vals in offs.items():
        out[addr:addr + len(vals)] = bytes(vals)
    out[GAIN_ADDR] = gain
    return apply_checksum_for(out, VARIANT_404)
