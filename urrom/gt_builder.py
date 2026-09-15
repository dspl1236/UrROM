"""
urrom/gt_builder.py — scaffold a 3B (404) fuel/ignition chip for a bigger turbo.

Composes the pieces traced in docs/3B_injection_path_RE.md and
docs/3B_load_headroom_RE.md into one repeatable build:

  1. rescale_load(k)         compress the load scale (GAIN, 22 axes, limiters, cap)
  2. regrid_load_axis()      give every 16x16 fuel/ignition map a new LOAD axis that
                             reaches above the old top; data bilinearly resampled so
                             every cell inside the stock range keeps its value
  3. starter fill            the new columns above the old top start as the old top
                             column, fuel enriched by `enrich` and ignition retarded by
                             `retard_deg` in proportion to how far above the old top
                             the column sits — a conservative place to begin, to be
                             corrected cell by cell from wideband logs
  4. release the load limiter (stock 174..156 -> `limiter`) so the ECU does not cut
  5. optional injector scaling: fuel maps 1-4 and the cranking table x stock_cc /
     new_cc (they are Q7 factors on the pulse).  The post-start table is a relative
     term (1 + v/128, 0x1C29) and the IAT / warm-up tables are multipliers around
     1.00, so none of those move with the injectors.
  6. 404 checksum

Nothing here is a tune.  It is the scaffold on which the tune is written.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from urrom.ecu_profiles import (VARIANT_404, apply_checksum_for, decode_descriptor_tables,
                                read_map, write_map, _descriptor_breakpoints)
from urrom.load_rescale import rescale_load, _encode_deltas, LOAD_INPUT
from urrom.xcompare import _interp1

FUEL_MAPS = (0x6A8E, 0x6C1C, 0x6D74, 0x6E98)
IGN_MAPS = (0x7076, 0x71F8, 0x731C, 0x7440, 0x7667, 0x77CF, 0x7937)
LOAD_LIMITER_1, LOAD_LIMITER_2 = 0x6951, 0x695D
CRANKING_ENRICH = (0x6BC8, 6)          # Q7 factor used INSTEAD of the map while cranking
POST_START_ENRICH = (0x6A47, 5)        # relative (1 + v/128): NOT scaled with injectors
STOCK_3B_INJECTOR_CC = 305             # Bosch 0 280 150 737, 29 lb/h at 3 bar, 16 ohm
IGN_RAW_PER_DEG = 1 / 0.75


@dataclass
class ScaffoldReport:
    factor: float
    axis_before: list
    axis_after: list
    new_columns: list
    limiter: int
    injector_ratio: float
    notes: list = field(default_factory=list)

    def text(self) -> str:
        out = [f"load scale x{self.factor:g}; LOAD axis {self.axis_before} -> {self.axis_after}",
               f"  new columns above the old top: {self.new_columns}",
               f"  load limiter 1 -> {self.limiter}, limiter 2 -> {int(self.limiter * 0.8)}",
               f"  injector ratio x{self.injector_ratio:g}" + ("  (NOT scaled: set --injector-ratio when the part numbers are known)"
                                                              if self.injector_ratio == 1.0 else "")]
        out += ["  " + n for n in self.notes]
        return "\n".join(out)


def load_axis(rom: bytes, data_addr: int) -> tuple[list[int], int]:
    """(LOAD breakpoints, address of their delta bytes) for a 16x16 map."""
    for t in decode_descriptor_tables(bytes(rom)):
        if t["data"] == data_addr and t["two_d"]:
            desc = t["desc"]; nx = rom[desc + 1]; yo = desc + 2 + nx
            assert rom[yo] == LOAD_INPUT
            return list(t["y_axis"]), yo + 2
    raise KeyError(hex(data_addr))


def gt_axis(scaled: list[int], top: int, new_cols: int = 3) -> list[int]:
    """Keep the first 16-new_cols scaled breakpoints, then space new_cols points evenly to `top`."""
    keep = scaled[:16 - new_cols]
    step = (top - keep[-1]) / new_cols
    new = [int(keep[-1] + step * (i + 1) + 0.5) for i in range(new_cols)]
    axis = keep + new
    if axis != sorted(axis) or len(set(axis)) != 16 or axis[-1] > 255:
        raise ValueError(f"bad axis {axis}")
    return axis


def regrid_load_axis(rom: bytearray, new_axis: list[int], enrich: float = 0.08,
                     retard_deg: float = 1.5) -> list[int]:
    """Re-grid every 16x16 fuel/ign map onto new_axis; returns the new columns above the old top."""
    old_top = None; new_cols = []
    for addr in FUEL_MAPS + IGN_MAPS:
        m = next(x for x in VARIANT_404.main_maps if x.main_addr == addr)
        old_axis, delta_at = load_axis(bytes(rom), addr)
        old_top = old_axis[-1]
        data = read_map(bytes(rom), m)
        rows = list(range(16))
        # resample along LOAD only (rows unchanged): clamp above the old top = last column
        out = []
        for r in rows:
            src = [float(v) for v in data[r]]
            row = []
            for c in new_axis:
                v = _interp1(old_axis, src, c)
                if c > old_top:
                    frac = (c - old_top) / (new_axis[-1] - old_top)
                    if addr in FUEL_MAPS:
                        v = v * (1.0 + enrich * frac)
                    else:
                        v = v - retard_deg * IGN_RAW_PER_DEG * frac
                row.append(max(0, min(255, int(v + 0.5))))
            out.append(row)
        write_map(rom, m, out)
        rom[delta_at:delta_at + 16] = bytes(_encode_deltas(new_axis))
        new_cols = [c for c in new_axis if c > old_top]
    return new_cols


def scale_injectors(rom: bytearray, ratio: float) -> None:
    """ratio = stock_cc / new_cc.  Fuel maps 1-4 and the cranking table are Q7 factors on the pulse."""
    if ratio == 1.0:
        return
    for addr in FUEL_MAPS:
        m = next(x for x in VARIANT_404.main_maps if x.main_addr == addr)
        write_map(rom, m, [[int(v * ratio + 0.5) for v in row] for row in read_map(bytes(rom), m)])
    a, n = CRANKING_ENRICH
    rom[a:a + n] = bytes(max(0, min(255, int(v * ratio + 0.5))) for v in rom[a:a + n])


def injector_chip(rom: bytes, new_cc: float, stock_cc: float = STOCK_3B_INJECTOR_CC) -> tuple[bytearray, float]:
    """A stock-scale chip re-fuelled for different injectors (same reference pressure), checksum applied."""
    ratio = stock_cc / new_cc
    out = bytearray(rom)
    scale_injectors(out, ratio)
    return apply_checksum_for(out, VARIANT_404), ratio


def build_scaffold(rom: bytes, factor: float = 0.75, top: int = 225, new_cols: int = 3,
                   enrich: float = 0.08, retard_deg: float = 1.5, limiter: int = 250,
                   injector_ratio: float = 1.0) -> tuple[bytearray, ScaffoldReport]:
    out, lrep = rescale_load(rom, factor, cap=255, apply_checksum=False)
    scaled, _ = load_axis(bytes(out), FUEL_MAPS[0])
    axis = gt_axis(scaled, top, new_cols)
    cols = regrid_load_axis(out, axis, enrich, retard_deg)
    out[LOAD_LIMITER_1:LOAD_LIMITER_1 + 5] = bytes([limiter] * 5)
    out[LOAD_LIMITER_2:LOAD_LIMITER_2 + 5] = bytes([int(limiter * 0.8)] * 5)
    scale_injectors(out, injector_ratio)
    out = apply_checksum_for(out, VARIANT_404)
    rep = ScaffoldReport(factor, scaled, axis, cols, limiter, injector_ratio)
    rep.notes.append(f"fuel in the new columns: old top column x (1 + {enrich:g} x fraction); "
                     f"ignition: old top column - {retard_deg:g} deg x fraction")
    rep.notes.append(f"stock-load equivalent of the new top {axis[-1]}: {axis[-1] / factor:.0f} "
                     f"(23 psi on a GT3071 needs ~280)")
    return out, rep
