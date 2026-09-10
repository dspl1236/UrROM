"""
urrom/session_log.py
====================
Session edit changelog — tracks every cell change made during a tuning session.

Each edit is recorded with:
  - timestamp
  - map name and address
  - cell (row, col)
  - old decoded value → new decoded value
  - old raw → new raw

Exportable as plain text or HTML diff report.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class EditRecord:
    timestamp:  str
    map_name:   str
    map_addr:   int
    row:        int
    col:        int
    old_raw:    int
    new_raw:    int
    old_dec:    Optional[float]
    new_dec:    Optional[float]
    unit:       str
    row_label:  Optional[float] = None     # axis value of the row (rpm on main chips, TPS on the boost chip)
    col_label:  Optional[float] = None     # axis value of the column (load / rpm)
    chip:       str = "main"
    note:       str = ""

    @property
    def where(self) -> str:
        """'4600 rpm / load 174' (main chip) or 'TPS 219 / 4886 rpm' (boost chip)."""
        def f(v):
            try:
                return f"{float(v):.0f}"
            except (TypeError, ValueError):
                return "?"
        if self.row_label is None and self.col_label is None:
            return f"cell [{self.row},{self.col}]"
        if self.chip == "boost":
            return f"TPS {f(self.row_label)} / {f(self.col_label)} rpm"
        return f"{f(self.row_label)} rpm / load {f(self.col_label)}"

    def sentence(self) -> str:
        if self.old_dec is not None and self.new_dec is not None and self.unit and self.unit != "raw":
            d = self.new_dec - self.old_dec
            return (f"{self.map_name} at {self.where}: {_fmt(self.old_dec)} → {_fmt(self.new_dec)} "
                    f"{self.unit} ({'+' if d >= 0 else ''}{_fmt(d)})")
        d = self.new_raw - self.old_raw
        return f"{self.map_name} at {self.where}: raw {self.old_raw} → {self.new_raw} ({d:+d})"


def _fmt(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}"


class SessionLog:
    """
    Records all cell edits made during a tuning session.
    One instance per loaded ROM — resets on new ROM load.
    """

    def __init__(self, rom_name: str = "", variant_name: str = ""):
        self.rom_name     = rom_name
        self.variant_name = variant_name
        self.started_at   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._records: list[EditRecord] = []

    def record(self, map_def, row: int, col: int,
               old_raw: int, new_raw: int,
               row_axis=None, col_axis=None, chip: str = "main",
               variant=None, note: str = "") -> None:
        """Record a single cell edit with its axis position. Skips if old == new."""
        if old_raw == new_raw:
            return
        unit = getattr(map_def, 'unit', '') or ''
        old_dec = new_dec = None
        try:
            from urrom.guards import _unit_value     # boost-sensor aware, pure
            old_dec, unit = _unit_value(map_def, variant, old_raw)
            new_dec, _ = _unit_value(map_def, variant, new_raw)
            if unit == "raw":
                old_dec = new_dec = None
        except Exception:
            decode = getattr(map_def, 'decode', None)
            old_dec = decode(old_raw) if decode else None
            new_dec = decode(new_raw) if decode else None
        rl = row_axis[row] if row_axis is not None and row < len(row_axis) else None
        cl = col_axis[col] if col_axis is not None and col < len(col_axis) else None
        self._records.append(EditRecord(
            timestamp  = datetime.now().strftime("%H:%M:%S"),
            map_name   = map_def.name,
            map_addr   = map_def.main_addr,
            row=row, col=col,
            old_raw=old_raw, new_raw=new_raw,
            old_dec=old_dec, new_dec=new_dec,
            unit=unit, row_label=rl, col_label=cl, chip=chip, note=note,
        ))

    # ── readable log (roadmap item 5) ─────────────────────────────────────

    @property
    def records(self) -> list[EditRecord]:
        return list(self._records)

    def collapsed(self) -> list[EditRecord]:
        """One record per cell: first old value → last new value, in first-edit
        order; cells edited back to their starting value drop out."""
        first: dict[tuple, EditRecord] = {}
        last: dict[tuple, EditRecord] = {}
        order: list[tuple] = []
        for r in self._records:
            key = (r.chip, r.map_addr, r.row, r.col)
            if key not in first:
                first[key] = r; order.append(key)
            last[key] = r
        out = []
        for key in order:
            a, b = first[key], last[key]
            if a.old_raw == b.new_raw:
                continue
            out.append(EditRecord(timestamp=b.timestamp, map_name=a.map_name, map_addr=a.map_addr,
                                  row=a.row, col=a.col, old_raw=a.old_raw, new_raw=b.new_raw,
                                  old_dec=a.old_dec, new_dec=b.new_dec, unit=a.unit,
                                  row_label=a.row_label, col_label=a.col_label, chip=a.chip, note=b.note))
        return out

    def sentences(self, collapse: bool = True) -> list[str]:
        return [r.sentence() for r in (self.collapsed() if collapse else self._records)]

    def summary_by_map(self) -> list[dict]:
        by: dict[str, list[EditRecord]] = {}
        for r in self.collapsed():
            by.setdefault(r.map_name, []).append(r)
        out = []
        for name, recs in by.items():
            if recs[0].old_dec is not None and recs[0].unit and recs[0].unit != "raw":
                ds = [r.new_dec - r.old_dec for r in recs]; unit = recs[0].unit
            else:
                ds = [float(r.new_raw - r.old_raw) for r in recs]; unit = "raw"
            out.append({"map": name, "chip": recs[0].chip, "cells": len(recs), "unit": unit,
                        "mean": sum(ds) / len(ds), "min": min(ds), "max": max(ds)})
        return out

    def commit_message(self, max_lines: int = 60) -> str:
        """A git-style message: one-line title, blank line, per-map summary, then the sentences."""
        cells = self.collapsed()
        maps = {r.map_name for r in cells}
        title = (f"tune: {len(cells)} cell{'s' if len(cells) != 1 else ''} in {len(maps)} map{'s' if len(maps) != 1 else ''}"
                 + (f" — {self.variant_name}" if self.variant_name else "")
                 + (f" ({self.rom_name})" if self.rom_name else ""))
        body = []
        for m in self.summary_by_map():
            body.append(f"- {m['map']} [{m['chip']}]: {m['cells']} cell{'s' if m['cells'] != 1 else ''}, "
                        f"mean {m['mean']:+.1f}, {m['min']:+.1f}…{m['max']:+.1f} {m['unit']}")
        sents = self.sentences()
        shown = sents[:max_lines]
        body.append("")
        body += [f"  {x}" for x in shown]
        if len(sents) > max_lines:
            body.append(f"  … and {len(sents) - max_lines} more")
        return title + "\n\n" + "\n".join(body).rstrip() + "\n"

    def undo_last(self) -> Optional[EditRecord]:
        """Pop and return the most recent raw edit (the editor applies the revert)."""
        return self._records.pop() if self._records else None

    def clear(self) -> None:
        self._records.clear()

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def changed_maps(self) -> set[str]:
        return {r.map_name for r in self._records}

    def to_text(self) -> str:
        lines = [
            f"UrROM Session Changelog",
            f"ROM:     {self.rom_name}",
            f"Variant: {self.variant_name}",
            f"Started: {self.started_at}",
            f"Edits:   {self.count}",
            "=" * 60,
            "",
        ]
        # Group by map
        by_map: dict[str, list[EditRecord]] = {}
        for r in self._records:
            by_map.setdefault(r.map_name, []).append(r)

        for map_name, recs in by_map.items():
            lines.append(f"{map_name}  (WH 0x{recs[0].map_addr:04X})  "
                         f"— {len(recs)} edits")
            for r in recs:
                if r.old_dec is not None:
                    o = f"{r.old_dec:.2f}"
                    n = f"{r.new_dec:.2f}"
                    diff = (r.new_dec or 0) - (r.old_dec or 0)
                    sign = "+" if diff >= 0 else ""
                    lines.append(f"  {r.timestamp}  [{r.row:2d},{r.col:2d}]  "
                                 f"{o} → {n} {r.unit}  ({sign}{diff:.2f})")
                else:
                    lines.append(f"  {r.timestamp}  [{r.row:2d},{r.col:2d}]  "
                                 f"raw {r.old_raw} → {r.new_raw}")
            lines.append("")

        lines.append(f"Total: {self.count} cell edits across {len(by_map)} maps")
        return "\n".join(lines)

    def to_html(self) -> str:
        import html as _html
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        by_map: dict[str, list[EditRecord]] = {}
        for r in self._records:
            by_map.setdefault(r.map_name, []).append(r)

        sections = []
        for map_name, recs in by_map.items():
            rows_html = []
            for r in recs:
                if r.old_dec is not None:
                    diff = (r.new_dec or 0) - (r.old_dec or 0)
                    colour = "#2dff6e" if diff > 0 else "#ff4444" if diff < 0 else "#6e7681"
                    sign = "+" if diff >= 0 else ""
                    cell_html = (
                        f"<tr>"
                        f"<td>{r.timestamp}</td>"
                        f"<td>[{r.row},{r.col}]</td>"
                        f"<td>{r.old_dec:.2f} {_html.escape(r.unit)}</td>"
                        f"<td>→</td>"
                        f"<td>{r.new_dec:.2f} {_html.escape(r.unit)}</td>"
                        f"<td style='color:{colour};'>{sign}{diff:.2f}</td>"
                        f"</tr>"
                    )
                else:
                    cell_html = (
                        f"<tr><td>{r.timestamp}</td><td>[{r.row},{r.col}]</td>"
                        f"<td>{r.old_raw}</td><td>→</td><td>{r.new_raw}</td>"
                        f"<td>raw</td></tr>"
                    )
                rows_html.append(cell_html)

            sections.append(
                f"<h3 style='color:#c9d1d9;margin-top:20px;'>"
                f"{_html.escape(map_name)}"
                f"  <span style='color:#6e7681;font-size:11px;font-weight:normal;'>"
                f"WH 0x{recs[0].map_addr:04X} — {len(recs)} edits</span></h3>"
                f"<table style='border-collapse:collapse;width:100%;font-size:11px;'>"
                f"<tr style='color:#6e7681;'>"
                f"<th>Time</th><th>Cell</th><th>Old</th><th></th><th>New</th><th>Δ</th></tr>"
                + "".join(rows_html)
                + "</table>"
            )

        return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<title>UrROM Session Changelog</title>
<style>body{{background:#0d1117;color:#c9d1d9;font-family:Consolas,monospace;padding:20px;}}
h1{{color:#c9d1d9;font-size:16px;}} .meta{{color:#6e7681;font-size:11px;margin-bottom:16px;}}
table td{{padding:3px 8px;border-bottom:1px solid #1a2332;}}
table tr:hover{{background:#131920;}}
@media print{{body{{background:white;color:black;}}
table td{{border-bottom:1px solid #ddd;}}}}</style>
</head><body>
<h1>UrROM Session Changelog</h1>
<div class='meta'>
ROM: {_html.escape(self.rom_name)} &nbsp;·&nbsp;
Variant: {_html.escape(self.variant_name)} &nbsp;·&nbsp;
Started: {self.started_at} &nbsp;·&nbsp;
Exported: {ts} &nbsp;·&nbsp;
{self.count} edits in {len(by_map)} maps
</div>
{"".join(sections) if sections else "<p style='color:#6e7681;'>No edits recorded.</p>"}
</body></html>"""
