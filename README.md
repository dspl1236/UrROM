# UrROM  v0.7.0

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

### 3B / RR — Single EPROM

| Engine | ECU Part Number | Software | Application |
|--------|----------------|----------|-------------|
| 3B | 447907404AA | 404 | Audi 200 20vT / UrQuattro / S2 early |
| 3B/RR | 857907404B | 404 | RR S2 Coupe |

### V8 32v — Single EPROM (map addresses unconfirmed, need ROMs)

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
- Import TunerPro XDF v1.50 (auto-inject for 551AA_0202)
- Export map as HTML / full ROM reference (printable)
- Export session changelog (per-cell edit history)
- Export diff report (.txt)

### CLI
```bash
python -m urrom.cli scan rom.034           # exit 2 on errors
python -m urrom.cli scan rom.034 --json    # machine-readable
python -m urrom.cli info rom.034           # identification
```

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
| **551AA_0202 maps** | DTC Classes 60×60 definition overlaps fuel/ign map addresses — may be a container or wrong dimensions | Needs investigation |
| **551B_D02 variant** | Currently using 551AA map addresses — needs its own verified address list | TODO |
| **Boost chip save** | Boost chip edits are not written to disk on save — only main EPROM is saved | Not implemented |
| **V8 split-bank save** | Save writes both halves but architecture is fragile if bank sizes change | Works, needs hardening |
| **KWP overlay colours** | `_text_colour` called with string instead of QColor — crashes on some Qt versions | Bug |

## Community Resources

- **S2Forum** — hardware threads, schematic discussions
- **vwnut8392/M232-Firmware** — PRJmod base ROMs and XDFs
- **034EFI** — `.034` Rip Chip tuning packages (GT2871/GT28RS/GT3071/K24)
- **HachiRom / DigiTool** — reference ROM editors (UrROM aims for parity+)

---

## License

MIT — see LICENSE file.
