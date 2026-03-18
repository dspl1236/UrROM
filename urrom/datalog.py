"""
urrom/datalog.py
================
Data log CSV replay for UrROM.

Parses OBD/dyno CSV logs (VCDS, OBD-II generic, wideband) and computes
which map cells were active at each timestep. Returns a coverage heatmap
showing how often each (row, col) cell was visited.

Supported CSV formats auto-detected:
  - VCDS log (semicolon-separated, German decimal comma)
  - Generic OBD-II (comma-separated, ms or s timestamps)
  - Custom wideband (comma + AFR column)

Output: dict mapping (row, col) → hit_count for a given map.
Used to highlight under-logged regions of a fuel/ign map.
"""

from __future__ import annotations
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class LogRow:
    """A single timestep from the data log."""
    time_s:  float
    rpm:     Optional[float] = None
    load:    Optional[float] = None   # raw 0-255 or normalised 0-100%
    afr:     Optional[float] = None   # wideband lambda × 14.7
    ect:     Optional[float] = None   # °C
    map_kpa: Optional[float] = None   # manifold absolute pressure kPa
    tps:     Optional[float] = None   # throttle %


@dataclass
class DataLog:
    """Parsed data log with all timesteps."""
    rows:    list[LogRow]
    source:  str           # filename
    format:  str           # detected format
    columns: list[str]     # original column names

    @property
    def duration_s(self) -> float:
        if not self.rows:
            return 0.0
        return self.rows[-1].time_s - self.rows[0].time_s

    @property
    def rpm_range(self) -> tuple[float, float]:
        rpms = [r.rpm for r in self.rows if r.rpm is not None]
        return (min(rpms), max(rpms)) if rpms else (0, 0)


# ── CSV parsers ───────────────────────────────────────────────────────────────

def _parse_vcds(path: Path) -> DataLog:
    """Parse VCDS log format (semicolon, German decimal commas)."""
    text = path.read_text(encoding='latin-1', errors='replace')
    rows_raw = []
    header = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        parts = [p.replace(',', '.') for p in line.split(';')]
        if header is None:
            header = parts
            continue
        try:
            rows_raw.append(dict(zip(header, parts)))
        except Exception:
            pass
    return _normalise_rows(rows_raw, header or [], path.name, 'vcds')


def _parse_generic_csv(path: Path) -> DataLog:
    """Parse generic OBD-II CSV (comma-separated, ms or s timestamps)."""
    with open(path, newline='', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return DataLog([], path.name, 'generic', [])
        rows_raw = list(reader)
        return _normalise_rows(rows_raw, list(reader.fieldnames), path.name, 'generic')


def _col_match(header: list[str], *patterns) -> Optional[str]:
    """Find the first column name matching any of the given patterns (case-insensitive)."""
    for pat in patterns:
        r = re.compile(pat, re.IGNORECASE)
        for h in header:
            if r.search(h):
                return h
    return None


def _normalise_rows(rows_raw: list[dict], header: list[str],
                    name: str, fmt: str) -> DataLog:
    """Map raw CSV rows to LogRow objects using flexible column detection."""
    time_col = _col_match(header, r'time', r'zeit', r't\b', r'ms\b', r'seconds')
    rpm_col  = _col_match(header, r'rpm', r'drehzahl', r'eng.*speed')
    load_col = _col_match(header, r'load', r'load.*\b', r'engine.load', r'maf',
                          r'rel.*load', r'calc.*load')
    afr_col  = _col_match(header, r'afr', r'wbo2', r'lambda', r'a/f', r'stoich')
    ect_col  = _col_match(header, r'ect', r'coolant', r'water.*temp', r'kuehlmittel')
    map_col  = _col_match(header, r'\bmap\b', r'boost.*kpa', r'manifold.*press',
                          r'intake.*press', r'ansaugdruck')
    tps_col  = _col_match(header, r'tps', r'throttle', r'drosselklap')

    def _f(row, col) -> Optional[float]:
        if col is None:
            return None
        v = row.get(col, '').strip().replace(',', '.')
        try:
            return float(v)
        except (ValueError, AttributeError):
            return None

    log_rows = []
    t0 = None
    for row in rows_raw:
        t = _f(row, time_col)
        if t is None:
            continue
        if t0 is None:
            t0 = t
        # Convert ms to seconds if values look like milliseconds
        t_s = (t - t0) / 1000.0 if t > 10000 else (t - t0)
        log_rows.append(LogRow(
            time_s  = t_s,
            rpm     = _f(row, rpm_col),
            load    = _f(row, load_col),
            afr     = _f(row, afr_col),
            ect     = _f(row, ect_col),
            map_kpa = _f(row, map_col),
            tps     = _f(row, tps_col),
        ))

    return DataLog(rows=log_rows, source=name, format=fmt, columns=header)


def load_log(path: Path) -> DataLog:
    """Auto-detect format and parse a data log CSV."""
    try:
        peek = path.read_bytes()[:2048].decode('latin-1', errors='replace')
    except Exception:
        return DataLog([], path.name, 'unknown', [])

    if ';' in peek and ('Zeit' in peek or 'Drehzahl' in peek or 'Group' in peek):
        return _parse_vcds(path)
    return _parse_generic_csv(path)


# ── Coverage computation ──────────────────────────────────────────────────────

def compute_coverage(log: DataLog, map_def, variant,
                     rom: bytes) -> dict[tuple[int,int], int]:
    """
    Compute which (row, col) cells in map_def were active at each log timestep.

    Returns dict: (raw_row, col) → hit_count
    raw_row = 0 is lowest RPM (NOT display row, which is inverted).
    """
    from urrom.ecu_profiles import get_axes, _RPM_AXIS_551, _LOAD_AXIS_551, _BOOST_RPM_AXIS, _BOOST_LOAD_AXIS

    rpm_axis, load_axis = get_axes(rom, map_def, variant)

    # Validate axes — get_axes may return live MCU data (not real RPM values)
    # Fall back to static known-good axes when the data looks wrong
    sw = getattr(variant, 'software_id', '') if variant else ''
    if not rpm_axis or max(rpm_axis) < 500:   # real RPM axis always ≥ 600
        if 'boost' in sw.lower() or map_def.main_addr < 0x1000:
            rpm_axis = _BOOST_RPM_AXIS[:map_def.rows]
        else:
            rpm_axis = _RPM_AXIS_551[:map_def.rows]
    if not load_axis or max(load_axis) < 5:
        if 'boost' in sw.lower() or map_def.main_addr < 0x1000:
            load_axis = _BOOST_LOAD_AXIS[:map_def.cols]
        else:
            load_axis = _LOAD_AXIS_551[:map_def.cols]

    if not rpm_axis or not load_axis:
        return {}

    hits: dict[tuple[int,int], int] = {}

    for lr in log.rows:
        rpm  = lr.rpm
        load = lr.load

        if rpm is None:
            continue

        # Find closest RPM row
        row = min(range(len(rpm_axis)), key=lambda i: abs(rpm_axis[i] - rpm))

        # Find closest load column (if load data available)
        if load is not None and load_axis:
            col = min(range(len(load_axis)), key=lambda i: abs(load_axis[i] - load))
        else:
            col = 0

        key = (row, col)
        hits[key] = hits.get(key, 0) + 1

    return hits


def coverage_stats(hits: dict[tuple[int,int], int],
                   map_def) -> dict:
    """Compute coverage statistics for a map."""
    total_cells = map_def.rows * map_def.cols
    hit_cells   = len(hits)
    total_hits  = sum(hits.values())
    max_hits    = max(hits.values()) if hits else 0
    pct_covered = 100.0 * hit_cells / total_cells if total_cells else 0

    # Find cells with fewer than 5 hits (under-sampled)
    sparse = [(r, c, n) for (r, c), n in hits.items() if n < 5]
    # Find cells with zero hits (never visited)
    zero_cells = [(r, c) for r in range(map_def.rows)
                  for c in range(map_def.cols)
                  if (r, c) not in hits]

    return {
        'total_cells':   total_cells,
        'hit_cells':     hit_cells,
        'pct_covered':   pct_covered,
        'total_hits':    total_hits,
        'max_hits':      max_hits,
        'sparse_cells':  sparse,
        'unvisited':     zero_cells,
    }


# ── Wideband AFR overlay ──────────────────────────────────────────────────────

@dataclass
class AFRSample:
    rpm:  float
    load: float
    afr:  float
    time_s: float


def compute_afr_overlay(log: DataLog, map_def, variant,
                         rom: bytes,
                         target_afr: float = 14.7) -> dict[tuple[int,int], dict]:
    """
    Compute average measured AFR per map cell.

    Returns dict: (raw_row, col) → {
        'afr_mean': float,
        'afr_min': float,
        'afr_max': float,
        'count': int,
        'delta': float,    # measured - target (positive = lean)
        'note': str,
    }
    """
    from urrom.ecu_profiles import get_axes, _RPM_AXIS_551, _LOAD_AXIS_551, _BOOST_RPM_AXIS, _BOOST_LOAD_AXIS

    rpm_axis, load_axis = get_axes(rom, map_def, variant)
    sw = getattr(variant, 'software_id', '') if variant else ''
    if not rpm_axis or max(rpm_axis) < 500:
        rpm_axis = _RPM_AXIS_551[:map_def.rows]
    if not load_axis or max(load_axis) < 5:
        load_axis = _LOAD_AXIS_551[:map_def.cols]

    if not rpm_axis or not load_axis:
        return {}

    cell_samples: dict[tuple[int,int], list[float]] = {}

    for lr in log.rows:
        if lr.rpm is None or lr.afr is None:
            continue
        # Only log under-load measurements (filter idle/coast)
        if lr.tps is not None and lr.tps < 20:
            continue  # skip light throttle (not a meaningful fuel cell)

        row = min(range(len(rpm_axis)), key=lambda i: abs(rpm_axis[i] - lr.rpm))
        col = 0
        if lr.load is not None and load_axis:
            col = min(range(len(load_axis)), key=lambda i: abs(load_axis[i] - lr.load))

        key = (row, col)
        cell_samples.setdefault(key, []).append(lr.afr)

    result = {}
    for (r, c), afrs in cell_samples.items():
        mean = sum(afrs) / len(afrs)
        delta = mean - target_afr
        if delta > 1.0:
            note = f"Lean {delta:+.1f} AFR"
        elif delta < -1.0:
            note = f"Rich {delta:+.1f} AFR"
        else:
            note = f"~Target {mean:.1f}"
        result[(r, c)] = {
            'afr_mean': round(mean, 2),
            'afr_min':  round(min(afrs), 2),
            'afr_max':  round(max(afrs), 2),
            'count':    len(afrs),
            'delta':    round(delta, 2),
            'note':     note,
        }

    return result
