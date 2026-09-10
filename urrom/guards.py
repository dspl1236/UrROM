"""
urrom/guards.py — guard rails for map edits (roadmap item 4).

Called on every cell edit with the map, the chip variant, the axes and the
old / new / stock bytes.  Returns Guards (info / warn / stop) worded for the
person typing, plus a one-line description of what the edit changes in real
units.  Nothing here blocks an edit; the editor shows the guard and lets the
tuner decide.  The rules encode what the firmware traces established:

  * boost targets are ADC counts against the fitted sensor — 255 is the sensor
    ceiling and the controller needs headroom below it;
  * the fuel/ignition maps clamp at the last load column, so that column is
    what runs at any load above the axis;
  * the boost board's knock control pulls timing back and logs a fault when
    high-load advance is pushed (docs/3B_boost_chip_RE.md, main-ECU handler
    0x27B4), so added advance above load ~130 / 3500 rpm is flagged;
  * fuel taken out under boost is flagged, fuel added is not;
  * a typed value the byte cannot hold is reported as clamped;
  * 404 / 551 chips carry a checksum UrROM rewrites on save (info only).
"""
from __future__ import annotations

from dataclasses import dataclass

KNOCK_LOAD_MIN = 130     # 3B load axis units; 551 axes are on the same scale
KNOCK_RPM_MIN = 3500
BOOST_HEADROOM = 5       # counts below 255 the controller still needs
IGN_STEP_DEG = 6.0       # a jump vs. neighbours that will feel like a step
FUEL_LEAN_RAW = 5        # raw counts leaner than stock under boost


@dataclass
class Guard:
    level: str     # "info" | "warn" | "stop"
    text: str


def _fmt(v) -> str:
    if v is None:
        return "?"
    return f"{v:.1f}" if isinstance(v, float) and abs(v - round(v)) > 1e-9 else f"{v:.0f}"


def _unit_value(map_def, variant, raw: int):
    """(decoded value, unit) for a byte, using the boost-sensor scale for pressure tables."""
    chip = getattr(map_def, "chip", "main")
    mt = getattr(map_def, "map_type", "raw")
    if chip == "boost" and mt == "boost":
        from urrom import boost_sensor
        fam = boost_sensor.family_of(getattr(variant, "software_id", ""))
        return boost_sensor.decode(raw, fam), boost_sensor.unit(fam)
    dec = getattr(map_def, "decode", None)
    if dec:
        try:
            return dec(raw), (map_def.unit or "")
        except Exception:
            pass
    return raw, "raw"


def describe_edit(map_def, variant, old_raw: int, new_raw: int) -> str:
    """'raw 49 → 52  =  14.2 → 16.5 °BTDC  (+2.3)'"""
    ov, unit = _unit_value(map_def, variant, old_raw)
    nv, _ = _unit_value(map_def, variant, new_raw)
    s = f"raw {old_raw} → {new_raw}"
    if unit and unit != "raw":
        d = nv - ov
        s += f"  =  {_fmt(ov)} → {_fmt(nv)} {unit}  ({'+' if d >= 0 else ''}{_fmt(d)})"
    elif getattr(map_def, "map_type", "") == "fuel" and old_raw:
        s += f"  ({(new_raw - old_raw) / old_raw * 100:+.1f} %)"
    return s


def check_edit(map_def, variant, r: int, c: int, old_raw: int, new_raw: int, orig_raw: int,
               rows_axis: list | None, cols_axis: list | None, grid: list | None = None,
               typed_value: float | None = None) -> list[Guard]:
    out: list[Guard] = []
    sw = getattr(variant, "software_id", "") if variant is not None else ""
    chip = getattr(map_def, "chip", "main")
    mt = getattr(map_def, "map_type", "raw")
    rows_axis = rows_axis or []
    cols_axis = cols_axis or []
    rpm = rows_axis[r] if (chip == "main" and r < len(rows_axis)) else None
    load = cols_axis[c] if (chip == "main" and c < len(cols_axis)) else None

    # ── typed value the byte cannot hold ────────────────────────────────
    if typed_value is not None:
        held, unit = _unit_value(map_def, variant, new_raw)
        try:
            if abs(float(held) - float(typed_value)) > max(0.6, abs(float(typed_value)) * 0.01) and unit != "raw":
                out.append(Guard("warn", f"{_fmt(typed_value)} {unit} does not fit the byte — stored as {_fmt(held)} {unit} (raw {new_raw})"))
        except (TypeError, ValueError):
            pass
        if new_raw in (0, 255) and typed_value is not None and unit == "raw" and float(typed_value) != new_raw:
            out.append(Guard("warn", f"clamped to {new_raw}: a byte holds 0…255"))

    # ── boost targets: sensor ceiling ───────────────────────────────────
    if chip == "boost" and mt == "boost":
        from urrom import boost_sensor
        fam = boost_sensor.family_of(sw)
        sens = boost_sensor.get_sensor(fam)
        kpa = sens.kpa(new_raw)
        gauge = boost_sensor.kpa_to_bar_gauge(kpa)
        if new_raw >= 255:
            out.append(Guard("stop", f"255 is the sensor's full scale ({_fmt(sens.kpa(255))} kPa on {sens.name}); the controller can never reach or hold it"))
        elif new_raw > 255 - BOOST_HEADROOM:
            out.append(Guard("warn", f"within {255 - new_raw} counts of the sensor ceiling — {_fmt(kpa)} kPa / {gauge:+.2f} bar leaves the controller almost no headroom"))
        if new_raw > orig_raw + 12:
            out.append(Guard("warn", f"+{new_raw - orig_raw} raw over stock here (+{_fmt(sens.kpa(new_raw) - sens.kpa(orig_raw))} kPa) — check fuel at the top load column and knock"))
        if sens.key == "bosch200":
            out.append(Guard("info", "pressure shown at the assumed 200 kPa scale; a logged boost reading confirms it"))

    # ── ignition: knock region and steps ────────────────────────────────
    if mt == "ign" and chip == "main":
        dec = map_def.decode or (lambda x: x)
        try:
            d_stock = dec(new_raw) - dec(orig_raw)
        except Exception:
            d_stock = 0
        if load is not None and rpm is not None and load >= KNOCK_LOAD_MIN and rpm >= KNOCK_RPM_MIN and d_stock > 1.6:
            out.append(Guard("warn", f"+{_fmt(d_stock)}° over stock at load {_fmt(load)} / {_fmt(rpm)} rpm — the knock control on the boost board will pull this back and log a knock fault if it detonates"))
        if grid:
            neigh = []
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= rr < len(grid) and 0 <= cc < len(grid[0]):
                    neigh.append(dec(grid[rr][cc]))
            if neigh:
                step = max(abs(dec(new_raw) - v) for v in neigh)
                if step >= IGN_STEP_DEG:
                    out.append(Guard("warn", f"{_fmt(step)}° step against a neighbouring cell — the interpolation will feel it as a jerk"))

    # ── fuel: leaning out under boost, top-column clamp ─────────────────
    if mt == "fuel" and chip == "main":
        if load is not None and load >= 154 and new_raw < orig_raw - FUEL_LEAN_RAW:
            out.append(Guard("warn", f"{orig_raw - new_raw} raw leaner than stock at load {_fmt(load)} — the S2 and RR factory chips never lean the top columns"))
        if cols_axis and c == len(cols_axis) - 1:
            out.append(Guard("info", f"last load column ({_fmt(load)}): this cell runs at every load above the axis, i.e. all of full boost"))

    # ── checksum reminder (once per edit, cheap) ────────────────────────
    try:
        from urrom.ecu_profiles import checksum_kind
        kind = checksum_kind(variant)
    except Exception:
        kind = None
    if kind == "404" and chip == "main":
        out.append(Guard("info", "checksum at 0x7F00 is recomputed on save (the ECU checks it at boot)"))
    elif kind == "551" and chip == "main":
        out.append(Guard("info", "PRJmod checksum at 0x3FFA is recomputed on save"))
    return out


def worst(guards: list[Guard]) -> str:
    order = {"stop": 2, "warn": 1, "info": 0}
    return max((g.level for g in guards), key=lambda l: order[l], default="")
