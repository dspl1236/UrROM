"""
urrom/tuning_checks.py
======================
Automated tuning sanity checks for M2.3.2 ROMs.

Checks for common tuning errors and unsafe conditions:
  - Fuel map values outside safe AFR range (lean/rich extremes)
  - Ignition timing above safe limits for engine variant
  - Identical consecutive rows (sign of copy-paste without interpolation)
  - Abrupt cell-to-cell jumps (> threshold delta)
  - Boost pressure exceeding sensor range
  - Checksum invalid on prjmod ROM
  - SD VE table active but fuel maps appear un-tuned

Each check returns a list of TuningIssue namedtuples.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class TuningIssue:
    severity:    str        # 'error', 'warning', 'info'
    category:    str        # 'fuel', 'ignition', 'boost', 'checksum', 'structure'
    map_name:    str
    description: str
    cell:        Optional[tuple[int,int]] = None   # (row, col) if cell-specific
    value:       Optional[float] = None


# ── Individual check functions ────────────────────────────────────────────────

def check_fuel_range(rom: bytes, variant, *, lean_threshold=170, rich_threshold=80) -> list[TuningIssue]:
    """
    Flag cells outside safe AFR range.
    For M2.3.2: raw 128 ≈ 14.7 λ=1.0 (stoich)
    raw < 80  → richer than ~21.5:1 in old mapping (check decode)
    raw > 170 → lean risk (actual: depends on injector calibration)
    Default thresholds are conservative — override for tuned injectors.
    """
    from urrom.ecu_profiles import read_map
    issues = []
    # The 3B/RR/S2 (404) and V8 fuel maps use a different raw scale from the
    # 551x family: stock 3B chips span raw 117–205 (S2 B3 up to 205, 3B AA to
    # 199).  The 551-derived 170 "lean" threshold flags every high-load cell of
    # a stock 3B chip, so widen the window for the 404 family unless the caller
    # overrode the defaults.
    sw = getattr(variant, "software_id", "") if variant else ""
    if sw in ("404", "404V8") and lean_threshold == 170 and rich_threshold == 80:
        lean_threshold, rich_threshold = 215, 100
    # Only check primary tunable fuel maps — skip failsafe, race fuel, correction, LPG
    _SKIP_FUEL = {"failsafe", "race fuel", "lpg", "correction", "warmup",
                  "decel", "overrun", "wall film", "lambda", "cat"}
    fuel_maps = [m for m in (variant.main_maps or [])
                 if m.map_type == 'fuel' and m.confidence == 'CONFIRMED' and m.rows > 1
                 and not any(k in m.name.lower() for k in _SKIP_FUEL)]
    for m in fuel_maps:
        try:
            data = read_map(rom, m)
        except Exception:
            continue
        for r in range(m.rows):
            for c in range(m.cols):
                rv = data[r][c]
                if rv < rich_threshold and rv > 0:
                    decode = m.decode
                    dv = decode(rv) if decode else rv
                    issues.append(TuningIssue(
                        severity='warning', category='fuel',
                        map_name=m.name,
                        description=f"Very rich cell: raw={rv} ({dv:.1f} {m.unit})",
                        cell=(r, c), value=float(rv)))
                elif rv > lean_threshold:
                    decode = m.decode
                    dv = decode(rv) if decode else rv
                    issues.append(TuningIssue(
                        severity='error', category='fuel',
                        map_name=m.name,
                        description=f"Potentially lean: raw={rv} ({dv:.1f} {m.unit})",
                        cell=(r, c), value=float(rv)))
    return issues


def check_ign_advance(rom: bytes, variant,
                      *, warn_advance: float = 52.0,
                         error_advance: float = 58.0,
                         min_advance: float = -12.0) -> list[TuningIssue]:
    """
    Flag ign timing cells outside plausible range.
    ABY/AAN/ADU stock maps reach 45.7°BTDC at light load — that is normal.
    Warn threshold 52°: significantly above OEM max, could indicate copy error.
    Error threshold 58°: almost certainly a data error.
    min_advance -12°: heavy retard (cold start maps excluded by CONFIRMED filter).
    """
    from urrom.ecu_profiles import read_map
    issues = []
    # Only check primary ign maps — skip correction, warmup, overrun, LPG etc.
    _SKIP_IGN = {"failsafe", "overrun", "lpg", "race fuel", "correction",
                 "warmup", "decel", "coolant", "iat", "closed throttle",
                 "cat converter", "adaptive", "trim"}
    ign_maps = [m for m in (variant.main_maps or [])
                if m.map_type == 'ign' and m.confidence in ('CONFIRMED', 'PROVISIONAL')
                and m.rows > 1 and m.decode
                and not _is_expected_flat(m.name)
                and not any(k in m.name.lower() for k in _SKIP_IGN)]
    for m in ign_maps:
        try:
            data = read_map(rom, m)
        except Exception:
            continue
        for r in range(m.rows):
            for c in range(m.cols):
                rv = data[r][c]
                dv = m.decode(rv)
                if dv is None:
                    continue
                if dv > error_advance:
                    issues.append(TuningIssue(
                        severity='error', category='ignition',
                        map_name=m.name,
                        description=f"Extreme advance: {dv:.1f}°BTDC — likely data error (raw {rv})",
                        cell=(r, c), value=dv))
                elif dv > warn_advance:
                    issues.append(TuningIssue(
                        severity='warning', category='ignition',
                        map_name=m.name,
                        description=f"High advance: {dv:.1f}°BTDC — verify intentional (raw {rv})",
                        cell=(r, c), value=dv))
                elif dv < min_advance:
                    issues.append(TuningIssue(
                        severity='warning', category='ignition',
                        map_name=m.name,
                        description=f"Heavy retard: {dv:.1f}°BTDC (raw {rv})",
                        cell=(r, c), value=dv))
    return issues


# Map name fragments that are expected to have repeated/flat rows by design
_EXPECTED_FLAT_MAPS = {
    "failsafe", "overrun", "lpg", "race fuel", "ve table",
    "dtc", "wgdc pilot", "launch", "flat", "decel",
}

def _is_expected_flat(map_name: str) -> bool:
    """Return True if this map type is expected to have repeated rows."""
    name_lower = map_name.lower()
    return any(k in name_lower for k in _EXPECTED_FLAT_MAPS)


def check_repeated_rows(rom: bytes, variant, *, max_maps=None) -> list[TuningIssue]:
    """
    Detect maps where consecutive rows are identical — sign of copy-paste
    without interpolation. Common cause of flat spots in power delivery.
    Skips maps that are expected to be flat (failsafe, overrun, VE blank, etc).
    """
    from urrom.ecu_profiles import read_map
    issues = []
    maps = [m for m in (variant.main_maps or [])
            if m.rows > 1 and m.cols > 1 and m.confidence == 'CONFIRMED'
            and not _is_expected_flat(m.name)]
    if max_maps:
        maps = maps[:max_maps]
    for m in maps:
        try:
            data = read_map(rom, m)
        except Exception:
            continue
        # Skip if entire map is uniform (e.g. blank VE table)
        all_vals = [data[r][c] for r in range(m.rows) for c in range(m.cols)]
        if len(set(all_vals)) <= 2:
            continue
        for r in range(1, m.rows):
            if data[r] == data[r-1]:
                issues.append(TuningIssue(
                    severity='info', category='structure',
                    map_name=m.name,
                    description=f"Rows {r-1}+{r} identical — verify interpolation needed",
                    cell=(r, None)))
    return issues


def check_large_jumps(rom: bytes, variant, *, max_raw_jump=50) -> list[TuningIssue]:
    """
    Find adjacent cells with raw difference > threshold.
    Threshold 50 raw (≈32° for ign decode) — flags only truly abrupt steps.
    Boundary cells (first/last column) are excluded; the load-axis boundary
    intentionally has a large retard step in stock maps.
    """
    from urrom.ecu_profiles import read_map
    issues = []
    maps = [m for m in (variant.main_maps or [])
            if m.map_type in ('ign', 'fuel') and m.rows > 1 and m.cols > 1
            and m.confidence == 'CONFIRMED']
    for m in maps:
        try:
            data = read_map(rom, m)
        except Exception:
            continue
        for r in range(1, m.rows - 1):      # skip top/bottom boundary rows
            for c in range(2, m.cols - 2):  # skip load-axis boundary cols
                delta = abs(int(data[r][c]) - int(data[r][c-1]))
                if delta > max_raw_jump:
                    decode = m.decode
                    dv = decode(data[r][c]) if decode else data[r][c]
                    dv_str = f"{dv:.1f} {m.unit}" if decode else str(dv)
                    issues.append(TuningIssue(
                        severity='warning', category=m.map_type,
                        map_name=m.name,
                        description=(f"Abrupt step: {delta} raw at row {r} col {c-1}→{c} "
                                     f"(val={dv_str}) — check interpolation"),
                        cell=(r, c), value=float(delta)))
    return issues


def check_boost_range(boost_rom: bytes, variant) -> list[TuningIssue]:
    """Check boost pressure map for values above sensor range."""
    from urrom.ecu_profiles import read_map
    issues = []
    boost_maps = [m for m in (variant.boost_maps or [])
                  if m.map_type == 'boost' and m.rows > 1]
    for m in boost_maps:
        try:
            data = read_map(boost_rom, m)
        except Exception:
            continue
        for r in range(m.rows):
            for c in range(m.cols):
                rv = data[r][c]
                # raw 255 = ~100% of sensor range
                # For 300kPa sensor: 255/255*300 = 300kPa (absolute) = ~200kPa gauge
                if rv > 245:
                    issues.append(TuningIssue(
                        severity='warning', category='boost',
                        map_name=m.name,
                        description=f"Near sensor limit: raw={rv} at [{r},{c}]",
                        cell=(r, c), value=float(rv)))
    return issues


def check_checksum(rom: bytes, variant, crc32: int = 0) -> list[TuningIssue]:
    """
    Check PRJmod checksum validity.
    Skips check for known stock ROMs (which use Bosch ASCII ID, not computed checksum).
    Only errors on tuned/prjmod ROMs where checksum is expected.
    """
    import zlib
    from urrom.ecu_profiles import verify_checksum, KNOWN_CRCS
    # Detect if this is a known stock ROM — they use ASCII part-number, no checksum
    actual_crc = crc32 or (zlib.crc32(rom[:0x8000]) & 0xFFFFFFFF)
    known = KNOWN_CRCS.get(actual_crc, (None, ""))
    if known[1].startswith("Stock"):
        return []  # stock chips — no computed checksum expected

    from urrom.ecu_profiles import CHECKSUM_VARIANTS
    sw = variant.software_id if variant else ""
    if sw not in CHECKSUM_VARIANTS:
        return []
    if not verify_checksum(rom):
        return [TuningIssue(
            severity='error', category='checksum',
            map_name='ROM',
            description='PRJmod checksum INVALID — ROM was edited without updating checksum. '
                        'UrROM applies checksum automatically on save (Ctrl+S).')]
    return []


# ── Master runner ─────────────────────────────────────────────────────────────

def run_all_checks(rom: bytes, variant,
                   boost_rom: bytes | None = None,
                   crc32: int = 0) -> list[TuningIssue]:
    """Run all checks and return sorted list of issues (errors first)."""
    issues: list[TuningIssue] = []
    issues += check_checksum(rom, variant, crc32=crc32)
    issues += check_ign_advance(rom, variant)
    issues += check_fuel_range(rom, variant)
    issues += check_repeated_rows(rom, variant)
    issues += check_large_jumps(rom, variant)
    if boost_rom:
        issues += check_boost_range(boost_rom, variant)

    # Sort: errors first, then warnings, then info
    order = {'error': 0, 'warning': 1, 'info': 2}
    issues.sort(key=lambda i: order.get(i.severity, 3))
    return issues
