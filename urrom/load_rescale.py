"""
urrom/load_rescale.py — move the 3B/RR/S2 (404) load scale for a bigger turbo.

Background (docs/3B_load_headroom_RE.md): on the 404 main chip

    air-per-rev = MAF pulses/segment x (0x6350 + MAF offset(46h)) x GAIN(0x6351) >> exp
    air-per-rev = min(air-per-rev, CAP(0x6970)[rpm] x 25)
    LOAD (3Fh)  = air-per-rev x 0xA4 >> 12            (8-bit, wraps at 6394)

so LOAD is proportional to GAIN, the CAP table is in load counts (200 on stock =
load 200), the map load axes top out at 190, and the load limiters / closed-loop
limits are compared against the same 8-bit LOAD.

A turbo that moves more air than the stock scale can express needs the whole
load scale compressed: multiply GAIN by k (< 1), and multiply every number the
firmware compares against LOAD by the same k so the stock calibration keeps its
meaning:

  * every descriptor axis whose input is LOAD (0x3F): 22 tables on the 3B, the
    fuel and ignition maps included;
  * load limiter 1/2 (0x6951, 0x695D) and the closed-loop lambda limits
    (0x7C72, 0x7C80);
  * the CAP table (0x6970), which is then raised to `cap` (default 255) so the
    new range is actually usable.

After rescale_load(rom, k) a stock-load of L reads as k*L, and the axis top
190 becomes 190*k; the columns above it are yours to fill in the editor.
Headroom in stock-load units is 255/k (k = 0.75 -> 340, ~1.8x the stock top).

The transient-enrichment index (4Ah) and the MAF linearisation offsets are
per-pulse quantities and are left alone.  The boost board never sees LOAD.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from urrom.ecu_profiles import (VARIANT_404, apply_checksum_for, decode_descriptor_tables,
                                _descriptor_breakpoints)

GAIN_ADDR = 0x6351          # x GAIN in the air-per-rev product (stock 185)
CAP_ADDR, CAP_N = 0x6970, 4  # air-per-rev cap / 25 per rpm point (stock 200 x4)
LOAD_COMPARED = {           # 1-D tables whose VALUES are load counts
    0x6951: 5, 0x695D: 5,   # load limiter 1 / 2
    0x7C72: 6, 0x7C80: 6,   # closed-loop lambda load limit / limp
}
LOAD_INPUT = 0x3F


@dataclass
class RescaleReport:
    factor: float
    gain_before: int
    gain_after: int
    cap_before: list
    cap_after: list
    axes: list = field(default_factory=list)      # (data_addr, old_top, new_top)
    tables: list = field(default_factory=list)    # (addr, old_vals, new_vals)

    def text(self) -> str:
        out = [f"load rescale x{self.factor:g}: GAIN 0x{GAIN_ADDR:04X} {self.gain_before} -> {self.gain_after}, "
               f"CAP 0x{CAP_ADDR:04X} {self.cap_before} -> {self.cap_after}",
               f"  {len(self.axes)} load axes re-gridded (top: "
               + ", ".join(f"{o}->{n}" for _, o, n in sorted(set((0, o, n) for _, o, n in self.axes))) + ")"]
        for a, o, n in self.tables:
            out.append(f"  0x{a:04X}: {o} -> {n}")
        out.append(f"  headroom: stock-load {255 / self.factor:.0f} before the 8-bit LOAD wraps (stock 255)")
        return "\n".join(out)


def _encode_deltas(bps: list[int]) -> list[int]:
    """Inverse of _descriptor_breakpoints: bp_k = 256 - sum(delta_k..delta_n)."""
    n = len(bps)
    deltas = [0] * n
    deltas[-1] = 256 - bps[-1]
    for k in range(n - 1):
        deltas[k] = bps[k + 1] - bps[k]
    return deltas


def _scaled_axis(bps: list[int], k: float) -> list[int]:
    out, last = [], 0
    for v in bps:
        s = max(last + 1, int(v * k + 0.5))          # half-up, not banker's
        s = min(s, 255)
        out.append(s); last = s
    return out


def rescale_load(rom: bytes | bytearray, factor: float, cap: int = 255,
                 apply_checksum: bool = True) -> tuple[bytearray, RescaleReport]:
    if not 0.2 <= factor <= 1.0:
        raise ValueError("factor must be in 0.2..1.0 (compress the load scale)")
    if not 1 <= cap <= 255:
        raise ValueError("cap must be 1..255")
    out = bytearray(rom)
    rep = RescaleReport(factor, out[GAIN_ADDR], 0, list(out[CAP_ADDR:CAP_ADDR + CAP_N]), [])

    # 1. axes
    seen = set()                      # axis positions already re-encoded (the decoder also
    for t in decode_descriptor_tables(bytes(rom)):   # emits a 1-D view of every 2-D descriptor)
        desc = t["desc"]
        # descriptor layout: [xin][nx][nx deltas] ([yin][ny][ny deltas]) [data]
        xin, nx = out[desc], out[desc + 1]
        yo = desc + 2 + nx
        for inp, at, n in ((xin, desc + 2, nx),
                           ((out[yo], yo + 2, out[yo + 1]) if t["two_d"] else (None, 0, 0))):
            if inp != LOAD_INPUT or at in seen:
                continue
            seen.add(at)
            bps = _descriptor_breakpoints(list(out[at:at + n]))
            new = _scaled_axis(bps, factor)
            out[at:at + n] = bytes(_encode_deltas(new))
            rep.axes.append((t["data"], bps[-1], new[-1]))

    # 2. values compared against LOAD
    for addr, n in LOAD_COMPARED.items():
        old = list(out[addr:addr + n])
        new = [max(0, min(255, int(v * factor + 0.5))) for v in old]
        out[addr:addr + n] = bytes(new)
        rep.tables.append((addr, old, new))

    # 3. gain and cap
    rep.gain_after = max(1, min(255, int(out[GAIN_ADDR] * factor + 0.5)))
    out[GAIN_ADDR] = rep.gain_after
    out[CAP_ADDR:CAP_ADDR + CAP_N] = bytes([cap] * CAP_N)
    rep.cap_after = [cap] * CAP_N

    if apply_checksum:
        out = apply_checksum_for(out, VARIANT_404)
    return out, rep


def load_axis_of(rom: bytes, data_addr: int) -> list[int] | None:
    """The LOAD breakpoints of a 2-D map at data_addr, or None."""
    for t in decode_descriptor_tables(bytes(rom)):
        if t["data"] == data_addr:
            return t["y_axis"] if t["y_input"] == LOAD_INPUT else (t["x_axis"] if t["x_input"] == LOAD_INPUT else None)
    return None
