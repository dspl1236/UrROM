# UrROM  v0.7.0

**[⬇ Download UrROM.exe (Windows)](https://github.com/dspl1236/UrROM/releases/latest/download/UrROM.exe)**

Open-source ROM editor for **Bosch Motronic M2.3 / M2.3.2** — the ECU family
used in Audi's 5-cylinder 2.2 20v turbo and V8 engines.

> **⚠ Work in Progress — Use at Your Own Risk**
>
> This tool is under active development. Features may be incomplete, map
> addresses may be unverified, and patches may not have been tested on all
> hardware variants. **Always read and back up your original ROM before making
> any changes.** Read it twice, compare the files, keep both copies safe.
>
> If you find a bug, incorrect address, or have a ROM dump to contribute,
> please [open an issue](https://github.com/dspl1236/UrROM/issues).


**130 tests passing** · Python 3.12 + PyQt5 · Windows / Linux / macOS

---

**How-to guide:** [docs/UrROM_How_To.pdf](docs/UrROM_How_To.pdf) walks through every tab with real chips (rebuild it with `python tools/build_guide.py`, needs `pip install reportlab pillow`).

## Supported ECUs

### I5 20v Turbo — Dual EPROM (fuel/ign + boost chip)

| Engine | ECU Part Number | Software | Trigger | Notes |
|--------|----------------|----------|---------|-------|
| AAN (early) | 4A0907551A | 551A | Distributor D02 | Factory-blank calibration |
| AAN (late) | 4A0907551AA | 551AA | Cam+Hall D03+HS | Factory-blank calibration |
| ABY S2 Coupe | 895907551B | 551B | Cam+Hall D01+HS | ✓ Real calibration confirmed |
| RS2 D02 (early) | 8A0907551B | 551B_D02 | Distributor D02 | Partial calibration |
| ADU RS2 Avant | 8A0907551C | 551C | Cam+Hall+RS2 | ✓ Real calibration confirmed |
| AAN/ABY/ADU (034EFI) | 4A0907551AA | 551AA_0202 | Any | PRJmod firmware, 136 maps |

### 3B / RR — Dual EPROM (32KB fuel/ign + 8KB boost)

| Engine | ECU Part Number | Software | Application |
|--------|----------------|----------|-------------|
| 3B | 447907404AA | 404 | Audi 200 20vT / UrQuattro / S2 early |
| 3B/RR | 857907404B | 404 | RR S2 Coupe |

### V8 32v — Single EPROM, no boost chip (map addresses unconfirmed, need ROMs)

| Engine | ECU Part Number | Software | Application |
|--------|----------------|----------|-------------|
| PT | 443907404A | 404V8 | Audi V8 3.6L |
| ABH | 4A0907557A | 557 | Audi V8 4.2L |

### 034EFI prjmod (551AA_0202)

Complete set of 034EFI `.034` Rip Chip files fingerprinted:
GT2871 / GT28RS / GT3071 / K24 Stage 1/1+, RS2 91Oct, Stock Rip Chip.
All files fingerprinted by CRC32, boost chip pairing validated automatically.

---

## Features

### Map Editing
- Double-click cell, type decoded value (°BTDC, AFR, raw)
- Copy/paste TSV (Excel-compatible), Ctrl+C/V/A
- **Undo/redo** 30-level stack (Ctrl+Z/Y)
- Right-click: Scale ×, Interpolate rows/cols, Smooth, Fill, Invert
- **Copy map from another ROM** (.bin or .034 source)
- Cell annotations (notes with `*` marker and tooltip)
- Decoded/raw display toggle

### Analysis
- **Find in maps (Ctrl+F)** — search all maps by value with operator
- **Tuning health scan (Ctrl+Shift+S)** — 7 automated checks, click-to-jump
- **Compare tab** — decoded delta (°BTDC/AFR), jump to most-changed map
- **Compare to stock baseline** — auto-loads matching reference chip
- **Data log overlay** — load VCDS/OBD CSV, annotate cells with hit count
- **Grid view** — all-maps thumbnail heat-map overview

### Hardware Tab
- Editable LC/NLS scalars (551AA_0202): RPM limit, LC speed, NLS angle
- MAP sensor detection from boost chip
- Boost chip pairing validation (warns on mismatched fuel+boost chip)
- Patch detection: SD mode, MFTS bypass, load decap

### Import/Export
- Open .bin / .034 (auto-descramble), drag & drop
- Save .bin or .034 Rip Chip, checksum auto-applied
- **File → Write 27C512 images**: fills a 64KB 27C512 with the native image repeated (8KB boost ×8, 32KB 3B/RR ×2) — 27C512s are what you can still buy. Loading a chip burned that way folds it back automatically.
- **File → Save Boost Chip…** (edited or not), same 27C512 option
- Import TunerPro XDF v1.50 (auto-inject for 551AA_0202)
- Export map as HTML / full ROM reference (printable)
- Export session changelog (per-cell edit history)
- Export diff report (.txt)

### CLI
```bash
python -m urrom.cli scan rom.034           # exit 2 on errors
python -m urrom.cli scan rom.034 --json    # machine-readable
python -m urrom.cli info rom.034           # identification
python -m urrom.cli maps rom.bin           # every map the 551 firmware references
python -m urrom.cli chip boost.bin         # 8KB/32KB image → 64KB 27C512 image (--to native folds back)
python -m urrom.cli coding rom.bin --volts 2.5   # coding-plug bands → ignition set (3B/RR/S2 and 551)
# Boost editor: Sensor selector (200 / MPX4250 / 300 / MPXH6400A / custom) + kPa abs or bar gauge — see docs/3B_boost_chip_RE.md
```

### Live recording and trace
With KWPBridge connected, **Tools → Start recording live data…** writes every sample to a CSV that `Tools → Overlay data log on map…` replays onto any map. **Tools → Live trace on current map** paints where the engine actually runs: cells lighten with their hit count on the table (hover for the count), and the Heat / 3D views draw the same trace as sized markers. The trace follows map switches and survives until cleared.

### Bench mode
**Tools → Bench mode (simulated engine)** starts KWPBridge's mock ECU for the loaded chip inside UrROM (3B/RR/S2 → the M2.3 mock with group 000 and the RAM window; 551 → the M2.3.2 mock) and connects the live layer to it exactly as to a car: the cursor walks the maps through cold start, warm idle, cruise, a boost run and decel, the trace fills in, and the badge reads BENCH. Use it to learn the maps, test overlays and views, or rehearse a session before the KKL cable goes on. It refuses to start if a real KWPBridge is already on the port.

### Session log
**Tools → Session log… (Ctrl+L)** lists every edit of the session as a sentence with its axis position and real units, e.g. `Ignition Map 2 (main, coding A) at 4600 rpm / load 174: 9.8 → 15.8 °BTDC (+6)`, collapsed to one line per cell (edits back to the starting value drop out). **Copy as commit message** gives a git-style title, a per-map summary and the sentences; **Undo last edit** reverts the most recent logged change in whichever tab holds that map; **Save…** writes text or HTML. Boost-chip edits are logged in the sensor's units.

### Guard rails
Every edit gets an **Edit** line under the map: the change in raw and in real units (°BTDC, kPa abs or bar at the selected sensor, fuel %), followed by anything the firmware traces say it risks: a boost target within a few counts of the sensor's full scale, added advance in the knock region (load ≥ 130 at ≥ 3500 rpm) that the boost board's knock control will pull back, a step against neighbouring cells, fuel taken out under boost, the last load column that runs at all of full boost, a typed value the byte cannot hold, and the checksum that will be rewritten on save. Flagged cells turn amber or red and carry the same text in their tooltip. Nothing is blocked; the tuner decides.

### Provenance
Every map shows where it came from: a line under the description and the tail of every cell's tooltip give the address source (firmware descriptor tables, XDF, or diff), the real chips it was confirmed on, the decode formula and where that formula was established, the axis source, any open caveat, and the docs section to read. `urrom/provenance.py` holds the chains per chip family; they cite the 2026-09 reverse-engineering notes in `docs/`.

### Compare
The Compare tab takes any second chip, even from the other family: a 551 map is paired with the 3B map by role (fuel / ignition main map) and bilinear-resampled onto the A chip's axes, so the delta is in real units on A's grid. Views: the three tables (A, B−A, B), a Heat map or a 3D surface of the **Difference**, or a **Blend** with a slider that morphs A into B. A raw-bytes toggle compares undecoded values. The summary line gives cells differing, mean, rms, min and max.

### Map views
Every map in the editor and boost tabs has **Table / Heat / 3D** buttons: the table stays the place to edit; Heat is a 2D heat map of the decoded values with changed cells outlined; 3D is a rotatable surface (drag to orbit) with the contour projected on the floor. Both follow edits, the Decoded/Raw toggle, the boost-sensor scale and the live KWPBridge cursor. Rendered with matplotlib, no OpenGL needed. The choice persists per tab.

### Tools Menu
- **Injector scaling wizard** — rescale all fuel maps for new cc size + FPR
- **Fuel pressure calculator** — effective flow + duty cycle estimate

---

## Calibration Status

Only two direct chip reads with real stock calibration are confirmed:
- **ABY 551B** (895907551B) — S2 Coupe, cam trigger
- **ADU 551C** (8A0907551C) — RS2 Avant, cam+RS2 trigger

The AAN chips (551A, 551AA) are factory-erased — calibration area is all 0x02.
Use the 034EFI Stock Rip Chip as the nearest AAN baseline.

---

## Installation

```bash
pip install PyQt5
python app/main.py
```

---

## Architecture

```
app/main.py             # PyQt5 workbench: map tree dock · editor/compare · inspector dock
urrom/ui/theme.py       # Shared palette + stylesheet
urrom/ui/map_tree.py    # Map navigator (grouped by chip/category, live filter)
urrom/ui/health_panel.py# Tuning health scan results panel
urrom/ecu_profiles.py   # All 551x/404/V8 variant definitions + KNOWN_CRCS
urrom/tuning_checks.py  # 7 automated health checks
urrom/map_export.py     # HTML export (single map + full ROM reference)
urrom/session_log.py    # Per-cell edit changelog
urrom/datalog.py        # CSV data log parsing + coverage overlay
urrom/xdf_import.py     # TunerPro XDF v1.50 parser
urrom/kwp.py            # KWP2000 live data dashboard
urrom/cli.py            # Headless CLI scan mode
```

Full documentation: `docs/UrROM_Master_Reference.md`

---

## Known Limitations

The following items are known and tracked for future work:

| Area | Issue | Status |
|------|-------|--------|
| **Ign Maps 1-7** | ~~0x30AC–0x3931 contain firmware code~~ — that analysis was done on the wrong half of the 64KB image. The stock firmware's own descriptor tables reference all seven maps (ADU 0x30AC…0x3931, ABY 0x30A8…0x392D). See `docs/551_calibration_descriptors_RE.md` | CONFIRMED |
| **551 chip layout** | 64KB main chip is split-bank: lower 32KB firmware, upper 32KB calibration. Not mirrored. Save and firmware patches now handle the halves correctly | Fixed |
| **551B_D02 variant** | Own map list decoded from the chip's firmware descriptor tables (0x0E13 family = PRJmod layout) | Fixed |
| **Boost chip save** | ~~Not saved~~ — Fixed. Boost chip now saved alongside main EPROM when edits detected | Fixed |
| **V8 split-bank save** | Save writes both halves but architecture is fragile if bank sizes change | Works, needs hardening |
| **KWP overlay colours** | `_text_colour` called with string instead of QColor — crashes on some Qt versions | Bug |
| **Firmware patches** | MFTS bypass, load decap, lambda delay — apply/revert from Hardware tab. Variant-gated: each patch only enabled on confirmed firmware bases (551AA_0202 for load decap/lambda, broader for MFTS) | New |
| **LC/NLS offset** | Entry point corrected from 0x0610 to 0x062E (confirmed via binary analysis) | Fixed |

## Community Resources

- **S2Forum m232.org subforum** — https://s2forum.com/forum/technical/m232-org (PRJmod, boost PID, logging threads)
- **m232.org wiki** — https://m232.org/index.php/Main_Page (PRJmod features, boost control, hardware limits; M2.3.2 / 551-series only, no 3B/RR)
- **S2Forum thread 65435 “Modifying Motronic 2.3.2 ECU hardware and software”** (vwnut8392 / prj) — AAN stock MAP = 250 kPa, RS2 = 300 kPa, R201 = 5.6 kΩ 1 % 1206
- **S2Forum** — hardware threads, schematic discussions
- **vwnut8392/M232-Firmware** — PRJmod base ROMs and XDFs
- **034EFI** — `.034` Rip Chip tuning packages (GT2871/GT28RS/GT3071/K24)
- **HachiRom / DigiTool** — reference ROM editors (UrROM aims for parity+)

---

## License

GPL-3.0 — see [LICENSE](LICENSE). The launch-control routine applied by the Hardware tab is
vwnut8392's S&M Msport V1.01 (his m232 suite is GPL-3.0), prj's base ROM is MIT, and the
factory chip images in `roms/` are Bosch / Audi firmware published for interoperability and
research as the m232 community already does; they are not covered by this license and will be
removed on request from the rights holder.

### Reverse-engineering notes
- [docs/3B_KW1281_RE.md](docs/3B_KW1281_RE.md) — the 3B's K-line dialect: ID blocks (coding number in block 3), 0x12 group read → 0xF4 ten-byte block, read-RAM window; KWPBridge `--ecu 3b` mock
- [docs/3B_launch_control_RE.md](docs/3B_launch_control_RE.md) — vwnut8392's spark-cut launch control (S&M Msport V1.01) diffed and ported: Hardware tab apply/revert + editable launch rpm / throttle / spark / dwell; the 404's 16-bit checksum at 0x7F00
- [docs/3B_stage1_notes.md](docs/3B_stage1_notes.md) — 3B / RR / S2 chip differences in numbers, the OEM combo (RR boost + 3B fuel/ign), a conservative stage-1 boost chip, and why the 200 kPa sensor caps it near 1.0 bar
