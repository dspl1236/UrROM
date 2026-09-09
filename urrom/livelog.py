"""
urrom/livelog.py — record KWPBridge live data and accumulate a map trace.

LiveRecorder   writes one CSV row per LiveValues sample.  Column names are
               chosen so urrom.datalog.load_log() reads the file straight
               back (time_s, rpm, load, ect, tps, map_kpa, afr ...), which
               means a recorded session can be replayed onto any map with
               the existing "Overlay data log" tools.
TraceAccumulator keeps the samples in memory and answers "how often did the
               engine sit in each cell of this map" (hit counts) plus the mean
               lambda per cell, using the map's own axes — the same nearest-
               cell rule the live cursor uses.
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CSV_COLUMNS = ["time_s", "rpm", "load", "ect", "iat", "tps", "map_kpa", "afr",
               "ign_deg", "batt_v", "ecu_pn"]


@dataclass
class Sample:
    t: float
    rpm: Optional[float]
    load: Optional[float]
    lambda_: Optional[float] = None
    ect: Optional[float] = None
    iat: Optional[float] = None
    tps: Optional[float] = None
    map_kpa: Optional[float] = None
    timing: Optional[float] = None
    battery: Optional[float] = None
    ecu_pn: str = ""


def sample_from_live(lv, t: float | None = None) -> Sample:
    """Build a Sample from a urrom.kwp.LiveValues (duck-typed)."""
    return Sample(
        t=time.time() if t is None else t,
        rpm=getattr(lv, "rpm", None), load=getattr(lv, "load", None),
        lambda_=getattr(lv, "lambda_", None), ect=getattr(lv, "ect", None),
        iat=getattr(lv, "iat", None), tps=getattr(lv, "tps", None),
        map_kpa=getattr(lv, "map_kpa", None), timing=getattr(lv, "timing", None),
        battery=getattr(lv, "battery", None), ecu_pn=getattr(lv, "ecu_pn", "") or "")


class LiveRecorder:
    """CSV recorder.  start(path) opens the file; add() appends; stop() closes."""

    def __init__(self):
        self._fh = None
        self._w = None
        self.path: Path | None = None
        self.rows = 0
        self._t0: float | None = None

    @property
    def active(self) -> bool:
        return self._fh is not None

    def start(self, path: str | Path) -> Path:
        self.stop()
        self.path = Path(path)
        self._fh = open(self.path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._fh)
        self._w.writerow(CSV_COLUMNS)
        self.rows = 0
        self._t0 = None
        return self.path

    def add(self, lv, t: float | None = None) -> None:
        if self._fh is None:
            return
        s = sample_from_live(lv, t)
        if self._t0 is None:
            self._t0 = s.t
        afr = None if s.lambda_ is None else round(s.lambda_ * 14.7, 3)
        self._w.writerow([f"{s.t - self._t0:.3f}", _n(s.rpm), _n(s.load), _n(s.ect), _n(s.iat),
                          _n(s.tps), _n(s.map_kpa), _n(afr), _n(s.timing), _n(s.battery), s.ecu_pn])
        self.rows += 1
        if self.rows % 20 == 0:
            self._fh.flush()

    def stop(self) -> Path | None:
        p = self.path
        if self._fh is not None:
            self._fh.close()
        self._fh = None
        self._w = None
        return p


def _n(v):
    return "" if v is None else (f"{v:.3f}" if isinstance(v, float) else v)


class TraceAccumulator:
    """In-memory samples → per-cell hit counts and mean lambda for any map."""

    def __init__(self, max_samples: int = 200_000):
        self.samples: list[Sample] = []
        self.max_samples = max_samples

    def add(self, lv, t: float | None = None) -> None:
        s = sample_from_live(lv, t)
        if s.rpm is None:
            return
        self.samples.append(s)
        if len(self.samples) > self.max_samples:
            del self.samples[: len(self.samples) - self.max_samples]

    def clear(self) -> None:
        self.samples.clear()

    def __len__(self) -> int:
        return len(self.samples)

    def hits_for(self, rows_axis: list, cols_axis: list) -> dict[tuple[int, int], int]:
        """Hit count per (row, col): nearest row-axis value to rpm, nearest col-axis to load."""
        hits: dict[tuple[int, int], int] = {}
        if not rows_axis or not cols_axis:
            return hits
        for s in self.samples:
            r = _nearest(rows_axis, s.rpm)
            c = _nearest(cols_axis, s.load) if s.load is not None else 0
            hits[(r, c)] = hits.get((r, c), 0) + 1
        return hits

    def lambda_for(self, rows_axis: list, cols_axis: list) -> dict[tuple[int, int], float]:
        acc: dict[tuple[int, int], list[float]] = {}
        for s in self.samples:
            if s.lambda_ is None:
                continue
            key = (_nearest(rows_axis, s.rpm), _nearest(cols_axis, s.load) if s.load is not None else 0)
            acc.setdefault(key, []).append(s.lambda_)
        return {k: sum(v) / len(v) for k, v in acc.items()}

    def to_datalog(self):
        """A urrom.datalog.DataLog over the same samples (for the CSV tools)."""
        from urrom.datalog import DataLog, LogRow
        t0 = self.samples[0].t if self.samples else 0.0
        rows = [LogRow(time_s=s.t - t0, rpm=s.rpm, load=s.load,
                       afr=None if s.lambda_ is None else s.lambda_ * 14.7,
                       ect=s.ect, map_kpa=s.map_kpa, tps=s.tps) for s in self.samples]
        return DataLog(rows=rows, source="live", format="live", columns=list(CSV_COLUMNS))


def _nearest(axis: list, v) -> int:
    return min(range(len(axis)), key=lambda i: abs(float(axis[i]) - float(v)))
