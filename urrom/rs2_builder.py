"""
urrom/rs2_builder.py — the "RS2 turbo on a 3B" chipset: what the 200 20V would
have shipped with if it had the RS2's K24-7200, injectors and boost profile.

Everything that can follow the RS2 does; the one deliberate exception is the
fuel map (see docs/3B_RS2_turbo_chipset.md).

Boost chip (base: the 3B's own 200 kPa chip, docs/3B_boost_chip_RE.md):
  * re-encoded for the AAN/ADU MPX4250A (250 kPa) sensor — the RS2's 14.2 psi
    peak is raw 253 on the 200 kPa scale, i.e. unusable there;
  * Boost Target A/B/C: the RS2 factory full-throttle curve by rpm (from the
    ADU/RS2 D02 boost chip, decoded on its own sensor) goes into the TPS-219
    row; every other TPS row is scaled by the same per-rpm gauge ratio so the
    3B's part-throttle shape survives;
  * N75 duty D/E/F: the RS2's per-rpm duty curve (rising to 74 % on top, where
    the 3B's K24 tables fall to 16 %) applied the same way; the duty ceiling
    is raised so it can be reached;
  * target ceiling and overboost duty-release lifted to `release_psi`.

Fuel/ign chip (base: 3B 447907404AA):
  * fuel maps 1-4 and cranking x 305/405 for the RS2 green injectors
    (0 280 150 984, 405 cc at the RS2's 3.8 bar);
  * the ADU main ignition map resampled onto the 3B axes into main maps
    2/5/6/7 (they agree within a degree anyway; this makes it literally RS2);
  * air-per-rev cap 255, load limiter 210 / 168, checksum.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

import urrom.boost_sensor as bs
from urrom.boost_rescale import convert_sensor, TARGETS, TARGET_CEILING
from urrom.ecu_profiles import (VARIANT_404, VARIANT_551B_D02, VARIANT_551C, apply_checksum_for,
                                read_map, write_map, get_axes, ign_encode_3b, normalize_rom)
from urrom.gt_builder import injector_chip
from urrom.xcompare import side_from_bytes, resample

DUTY_TABLES = (0x1A34, 0x1AB4, 0x1B34)
DUTY_CEILING = (0x1C47, 8)
RS2_GREEN_CC_AT_38BAR = 405
IGN_MAIN_MAPS_3B = (0x71F8, 0x7667, 0x77CF, 0x7937)     # maps 2 / 5 / 6 / 7


@dataclass
class RS2Report:
    lines: list = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(self.lines)


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    j = bisect.bisect_right(xs, x)
    a, b = xs[j - 1], xs[j]
    return ys[j - 1] + (ys[j] - ys[j - 1]) * (x - a) / (b - a)


def rs2_curves(rs2_boost: bytes) -> tuple[list, list, list]:
    """(rpm axis, WOT target kPa abs by rpm, WOT duty fraction by rpm) from the RS2 D02 boost chip."""
    m250 = next(s for s in bs.sensors() if s.key == "mpx4250")
    tgt = next(m for m in VARIANT_551B_D02.boost_maps if m.main_addr == 0x2520)
    dty = next(m for m in VARIANT_551B_D02.boost_maps if m.main_addr == 0x2480)
    rows, _ = get_axes(rs2_boost, tgt, VARIANT_551B_D02)
    T = read_map(rs2_boost, tgt); D = read_map(rs2_boost, dty)
    return (list(rows), [m250.kpa(max(r)) for r in T], [max(r) / 255.0 for r in D])


def build_boost(b3_boost: bytes, rs2_boost: bytes, release_psi: float = 19.0,
                duty_ceiling: int = 200) -> tuple[bytearray, RS2Report]:
    rep = RS2Report()
    old = bs.get_sensor("404"); new = next(s for s in bs.sensors() if s.key == "mpx4250")
    rpm_axis, rs2_kpa, rs2_duty = rs2_curves(rs2_boost)
    out, crep = convert_sensor(b3_boost, old, new, limits_psi=release_psi)
    rep.lines.append(crep.text())
    # per-rpm gauge ratio from the 3B's WOT row (table B, TPS 219) to the RS2 curve
    tb = next(m for m in VARIANT_404.boost_maps if m.main_addr == 0x1934)
    trows, tcols = get_axes(b3_boost, tb, VARIANT_404)
    T0 = read_map(b3_boost, tb)
    for a in TARGETS:
        m = next(x for x in VARIANT_404.boost_maps if x.main_addr == a)
        G = read_map(b3_boost, m)
        outg = []
        for i, tps in enumerate(trows):
            row = []
            for j, rpm in enumerate(tcols):
                g_old = max(0.0, old.kpa(G[i][j]) - bs.ATM_KPA)
                wot_old = max(1.0, old.kpa(T0[7][j]) - bs.ATM_KPA)
                wot_new = max(0.0, _interp(rpm_axis, rs2_kpa, rpm) - bs.ATM_KPA)
                g_new = wot_new if i == len(trows) - 1 else g_old * wot_new / wot_old
                row.append(new.raw(bs.ATM_KPA + g_new))
            outg.append(row)
        write_map(out, m, outg)
    wot = [bs.kpa_to_psi_gauge(new.kpa(v)) for v in read_map(bytes(out), tb)[7]]
    rep.lines.append("target B WOT row (psi by rpm col): " + " ".join(f"{p:.1f}" for p in wot))
    # duty
    td = next(m for m in VARIANT_404.boost_maps if m.main_addr == 0x1AB4)
    D0 = read_map(b3_boost, td)
    for a in DUTY_TABLES:
        m = next(x for x in VARIANT_404.boost_maps if x.main_addr == a)
        G = read_map(b3_boost, m)
        outd = []
        for i, tps in enumerate(trows):
            row = []
            for j, rpm in enumerate(tcols):
                wot_old = max(1, D0[7][j])
                wot_new = _interp(rpm_axis, rs2_duty, rpm) * 255.0
                v = wot_new if i == len(trows) - 1 else G[i][j] * wot_new / wot_old
                row.append(max(0, min(duty_ceiling, int(v + 0.5))))
            outd.append(row)
        write_map(out, m, outd)
    a, k = DUTY_CEILING
    out[a:a + k] = bytes([duty_ceiling] * k)
    rep.lines.append("duty E WOT row (% by rpm col): " + " ".join(f"{v / 255 * 100:.0f}" for v in read_map(bytes(out), td)[7])
                     + f"; duty ceiling -> {duty_ceiling / 255 * 100:.0f} %")
    return out, rep


def build_main(b3: bytes, adu: bytes, cap: int = 255, limiter: int = 210,
               new_cc: float = RS2_GREEN_CC_AT_38BAR) -> tuple[bytearray, RS2Report]:
    rep = RS2Report()
    out, ratio = injector_chip(b3, new_cc)
    out = bytearray(out)
    rep.lines.append(f"fuel maps 1-4 + cranking x {ratio:.3f} (305 / {new_cc:g} cc)")
    # RS2 ignition: the ADU main map resampled onto the 3B axes into maps 2/5/6/7
    i3 = next(m for m in VARIANT_404.main_maps if m.main_addr == 0x71F8)
    # ADU main map, coding set B (0x3598): the one docs/551 §3e confirms and xcompare uses
    ma = next(m for m in VARIANT_551C.main_maps if m.main_addr == 0x3598)
    sa = side_from_bytes(b3, VARIANT_404, i3)
    wh, _ = normalize_rom(adu)                    # 551: the calibration half, as xcompare does
    sb = side_from_bytes(bytes(wh), VARIANT_551C, ma)
    deg = resample(sb, sa.rows, sa.cols)
    top = [row[-1] for row in deg]
    if not all(0 <= v <= 45 for v in top):
        raise ValueError(f"ADU ignition map {ma.name} decoded implausibly: {top}")
    raw = [[ign_encode_3b(v) for v in row] for row in deg]
    for a in IGN_MAIN_MAPS_3B:
        m = next(x for x in VARIANT_404.main_maps if x.main_addr == a)
        write_map(out, m, raw)
    d = [deg[r][c] - sa.data[r][c] for r in range(16) for c in range(16)]
    rep.lines.append(f"ignition maps 2/5/6/7 <- ADU {ma.name} resampled; vs 3B map 2: mean {sum(d) / 256:+.2f} deg, "
                     f"max |{max(abs(x) for x in d):.1f}|; top-load column {[round(v, 1) for v in top[7:15]]}")
    out[0x6970:0x6974] = bytes([cap] * 4)
    out[0x6951:0x6956] = bytes([limiter] * 5)
    out[0x695D:0x6962] = bytes([int(limiter * 0.8)] * 5)
    rep.lines.append(f"air-per-rev cap {cap}, load limiter {limiter} / {int(limiter * 0.8)}")
    out = apply_checksum_for(out, VARIANT_404)
    return out, rep


def build_chipset(roms_dir: Path) -> tuple[bytearray, bytearray, RS2Report]:
    b3b = (roms_dir / "3b_boost_404aa.bin").read_bytes()
    rs2b = (roms_dir / "rs2_d02_boost_551b.bin").read_bytes()
    b3 = (roms_dir / "3b_fuel-ign_404aa.bin").read_bytes()
    adu = (roms_dir / "adu_fuel-ign_551c.bin").read_bytes()
    boost, r1 = build_boost(b3b, rs2b)
    main, r2 = build_main(b3, adu)
    rep = RS2Report(["BOOST CHIP"] + r1.lines + ["FUEL/IGN CHIP"] + r2.lines)
    return boost, main, rep
