"""
urrom/boost_rescale.py — move a 3B/RR/S2 (404) boost chip to a different MAP sensor.

The boost MCU works entirely in raw ADC counts (docs/3B_boost_chip_RE.md):

  * the Boost Target A/B/C tables and the overboost thresholds are ABSOLUTE
    sensor counts (the controller subtracts its own measured ambient, 42h);
  * the target ceiling per rpm band, the target correction table, the adaptive
    offset steps and the I-term dead-band are boost-above-ambient DELTAS;
  * the duty tables, duty ceiling and duty correction are % duty and do not
    care what the sensor is.

Fitting a 3-bar sensor without touching the tables therefore multiplies every
target by the span ratio (a stock RR peak of 153 kPa would become ~2.0 bar of
boost).  convert_sensor() re-encodes the count-valued tables so every table
keeps its physical meaning on the new sensor:

    absolute:  raw_new = new.raw(old.kpa(raw_old))
    delta:     raw_new = raw_old * old.span / new.span

and can then raise the limits for a bigger turbo (limits_psi).  There is no
checksum on the 404 boost chip.  The P/I gains (tables at 0x1BBE / 0x1C0D /
0x1C1C indexed by 4Ch) are per-count, so duty per kPa rises by the span ratio
on a bigger sensor; scale_gains=True compensates by the inverse ratio.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import urrom.boost_sensor as bs

TARGETS = (0x18B4, 0x1934, 0x19B4)          # 8 x 16 absolute counts each
OVERBOOST_THR = (0x1EC7, 8)                  # per rpm band, absolute; [0x1EC6] = debounce count
PLAUSIBILITY_ABS = (0x1892, 0x1894)          # 0x1890 list entries compared with 72h
AMBIENT_FLOOR = 0x1C57                       # lowest ambient the controller will believe
TARGET_CEILING = (0x1C4F, 8)                 # boost-above-ambient limit per rpm band
TARGET_CORR = (0x1C6A, 16)                   # subtracted from the target (delta)
ADAPT_CAP = 0x1EA2                           # 7Ah cap (delta)
ADAPT_STEP = (0x1EA3, 8)                     # 7Ah step per rpm band (delta)
I_DEADBAND = 0x1DEB                          # error below which the I-term is cleared (delta)
GAIN_TABLES = ((0x1BBE, 15), (0x1C0D, 15), (0x1C1C, 15))   # P, I, I' by 4Ch


@dataclass
class BoostConvertReport:
    old: str
    new: str
    ratio: float
    targets: list = field(default_factory=list)   # (addr, old_min, old_max, new_min, new_max)
    lines: list = field(default_factory=list)

    def text(self) -> str:
        out = [f"boost sensor {self.old} -> {self.new} (delta ratio {self.ratio:.3f})"]
        for a, omn, omx, nmn, nmx in self.targets:
            out.append(f"  target 0x{a:04X}: raw {omn}..{omx} -> {nmn}..{nmx}")
        out += ["  " + l for l in self.lines]
        return "\n".join(out)


def _sensor(key_or_obj):
    if isinstance(key_or_obj, bs.BoostSensor):
        return key_or_obj
    return next(s for s in bs.sensors() if s.key == key_or_obj)


def convert_sensor(rom: bytes | bytearray, old, new, limits_psi: float | None = None,
                   scale_gains: bool = False) -> tuple[bytearray, BoostConvertReport]:
    o, n = _sensor(old), _sensor(new)
    out = bytearray(rom)
    ratio = o.span_kpa / n.span_kpa
    rep = BoostConvertReport(o.key, n.key, ratio)

    def abs_conv(raw: int) -> int:
        return n.raw(o.kpa(raw))

    def delta_conv(raw: int) -> int:
        return max(0, min(255, int(raw * ratio + 0.5)))

    for a in TARGETS:
        old_v = list(out[a:a + 128]); new_v = [abs_conv(v) for v in old_v]
        out[a:a + 128] = bytes(new_v)
        rep.targets.append((a, min(old_v), max(old_v), min(new_v), max(new_v)))
    a, k = OVERBOOST_THR
    old_v = list(out[a:a + k]); out[a:a + k] = bytes(abs_conv(v) for v in old_v)
    rep.lines.append(f"overboost thresholds 0x{a:04X}: {old_v} -> {list(out[a:a + k])}")
    for a in PLAUSIBILITY_ABS:
        out[a] = abs_conv(out[a])
    old_f = out[AMBIENT_FLOOR]; out[AMBIENT_FLOOR] = abs_conv(old_f)
    rep.lines.append(f"ambient floor 0x{AMBIENT_FLOOR:04X}: {old_f} -> {out[AMBIENT_FLOOR]}")
    for a, k in (TARGET_CEILING, TARGET_CORR, ADAPT_STEP):
        old_v = list(out[a:a + k]); out[a:a + k] = bytes(delta_conv(v) for v in old_v)
        rep.lines.append(f"delta table 0x{a:04X}: {old_v} -> {list(out[a:a + k])}")
    for a in (ADAPT_CAP, I_DEADBAND):
        out[a] = delta_conv(out[a])
    if scale_gains:
        for a, k in GAIN_TABLES:
            out[a:a + k] = bytes(max(0, min(255, int(v * ratio + 0.5))) for v in out[a:a + k])
        rep.lines.append(f"P/I gain tables scaled x{ratio:.3f} (same duty per kPa as before)")

    if limits_psi is not None:
        amb = n.raw(bs.ATM_KPA)
        ceil = min(255, n.raw(bs.ATM_KPA + limits_psi * bs.KPA_PER_PSI) - amb)
        thr = min(255, n.raw(bs.ATM_KPA + (limits_psi + 3.0) * bs.KPA_PER_PSI))
        a, k = TARGET_CEILING; out[a:a + k] = bytes([ceil] * k)
        a, k = OVERBOOST_THR; out[a:a + k] = bytes([thr] * k)
        rep.lines.append(f"limits raised for {limits_psi:g} psi: target ceiling {ceil} counts above ambient "
                         f"({bs.kpa_to_psi_gauge(n.kpa(amb + ceil)):.1f} psi), overboost duty-release at raw {thr} "
                         f"({bs.kpa_to_psi_gauge(n.kpa(thr)):.1f} psi)")
    return out, rep


def target_summary(rom: bytes, sensor) -> dict:
    """min/max of each target table in kPa abs and psi gauge on the given sensor."""
    s = _sensor(sensor); res = {}
    for a in TARGETS:
        v = rom[a:a + 128]
        res[a] = (round(s.kpa(min(v)), 1), round(s.kpa(max(v)), 1), bs.kpa_to_psi_gauge(s.kpa(max(v))))
    return res
