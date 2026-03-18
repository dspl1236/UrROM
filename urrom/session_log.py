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
               old_raw: int, new_raw: int) -> None:
        """Record a single cell edit. Skips if old == new."""
        if old_raw == new_raw:
            return
        decode = getattr(map_def, 'decode', None)
        old_dec = decode(old_raw) if decode else None
        new_dec = decode(new_raw) if decode else None
        self._records.append(EditRecord(
            timestamp  = datetime.now().strftime("%H:%M:%S"),
            map_name   = map_def.name,
            map_addr   = map_def.main_addr,
            row=row, col=col,
            old_raw=old_raw, new_raw=new_raw,
            old_dec=old_dec, new_dec=new_dec,
            unit=getattr(map_def, 'unit', '') or '',
        ))

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
