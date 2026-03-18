# UrROM v0.7.0 — Master Reference

Open-source ROM editor for **Bosch Motronic M2.3 / M2.3.2**  
Audi 5-cylinder 20v turbo (AAN, ABY, ADU) and V8 engines.

**Repository:** https://github.com/dspl1236/UrROM  
**Test coverage:** 130 tests passing  
**CLI:** `python -m urrom.cli scan <rom.034>`

---

## Supported ECU Variants

| Software ID | ECU Part Number | Engine | Trigger | Calibration Status |
|-------------|----------------|--------|---------|-------------------|
| 551A | 4A0907551A | AAN (early) | Distributor D02 | ✗ BLANK — factory erased |
| 551AA | 4A0907551AA | AAN (late) | Cam+Hall D03+HS | ✗ BLANK — factory erased |
| 551B | 895907551B | ABY S2 Coupe | Cam+Hall D01+HS | ✓ REAL direct read |
| 551B_D02 | 8A0907551B | RS2 early | Distributor D02 | ⚠ PARTIAL (high-RPM blank) |
| 551C | 8A0907551C | ADU RS2 Avant | Cam+Hall+RS2 | ✓ REAL direct read |
| 551AA_0202 | 4A0907551AA | AAN/ABY/ADU | Any (PRJmod) | PRJmod firmware |
| 404 | 447907404AA | 3B 200 20vT | — | ✓ REAL direct read |
| 404 | 857907404B | 3B RR S2 | — | ✓ REAL direct read |
| 404V8 | 443907404A | PT V8 3.6L | Dual distributor | UNCONFIRMED |
| 557 | 4A0907557A | ABH V8 4.2L | Dual distributor | UNCONFIRMED |

### Critical: Two Firmware Families

**Stock M2.3.2 (551A/AA/B/C):**
- Fuel map: WH **0x2E17** — calibration starts at 0x2D00
- 7 ignition maps: WH 0x30AC, 0x3263, 0x3387, 0x3598, 0x36BC, 0x380D, 0x3931
- Code: LJMP fill 0x0000–0x2CFF with code islands

**PRJmod 551AA_0202 (034EFI tunes):**
- Fuel map: WH **0x0E13** — completely different firmware layout
- Ign maps: WH 0x1383, 0x125F, 0x1594, 0x16B8, 0x1809, 0x192D, 0x10A8
- 136 confirmed maps (vs 8 in stock)
- **DO NOT cross-load stock and PRJmod ROMs**

---

## Calibration Chip Status (Confirmed by Direct Binary Analysis)

### Factory-Blank Chips (calibration = all 0x02)

Both AAN chip types were used as **PRJmod base ROMs** — their calibration area was intentionally erased before the prjmod firmware was burned. The 8051 firmware code IS present.

| Chip | Code Bytes | Reset Vector | Trigger ISR | Notes |
|------|-----------|--------------|-------------|-------|
| 551A (4A0907551A) | 6,376 | 0x0000→0x0400 | Distributor | D02PMC base ROM |
| 551AA (4A0907551AA) | 10,292 | 0x0000→0x1297 | INT0→0x0438 | D03PMC base ROM, +60% code for cam trigger |

### Real Calibration Chips (confirmed direct reads)

| Chip | CRC32 | Max Fuel | Max Ign | Notes |
|------|-------|----------|---------|-------|
| ABY 551B | 0xA98CB481 | raw 170 | 45.7°BTDC | S2 Coupe reference |
| ADU 551C | 0x4378E077 | raw 163 | 45.7°BTDC | RS2 Avant reference |
| 3B 404AA | 0x0AE3CACD | — | — | 200 20vT / UrQ |
| RR 404B | — | — | — | RR S2 Coupe boost chip |

### ABY vs ADU Calibration Differences

Both chips share the same ECU family and trigger system but have **substantially different calibration**:
- **164 of 256 fuel cells differ** — different turbocharger, injector sizing, boost level
- ABY (S2 Coupe): fuel raw 124–170, smaller turbo
- ADU (RS2 Avant): fuel raw 123–163, RS2 turbo + 300kPa MAP sensor
- All 7 ign maps are at **identical addresses** — shared layout confirmed
- Both reach 45.7°BTDC max advance but with different cell-by-cell distribution

**Tuning baseline selection:**
- S2 Coupe tuning → use ABY 551B baseline
- RS2 Avant tuning → use ADU 551C baseline
- DO NOT cross-use (different injector/boost calibration)

---

## 034EFI Chipset Fingerprints

All 034EFI `.034` files are fingerprinted by CRC32. Every chip pair has been validated.

### Fuel Chips (551AA_0202 variant, WH 0x0E13)

| CRC32 | Label | Turbo | Injectors | Boost | whp |
|-------|-------|-------|-----------|-------|-----|
| 0x956BFC9C | Stock Rip Chip | K24 | RS2 replica | stock | — |
| 0xA47011AB | Stage 1+ K24 | K24 | RS2 replica | 20psi OB | +40 |
| 0x9A8A6B4E | Stage 1 GT28RS R2 | GT28RS | 550cc | 27psi OB | 285 |
| 0x6F3AE675 | Stage 1 GT3071 R8 42lb | GT3071 | 440cc | 26psi OB | 346 |
| 0x07DA1752 | Stage 1 GT3071 R9.1 550cc | GT3071 | 550cc | 26psi OB | — |
| 0x2EB58546 | Stage 1 GT2871 R9.1 550cc EV14 | GT2871 | 550cc EV14 | 26psi OB | 330 |
| 0xA77BB88E | Stage 1 GT2871 R9 440cc Siemens | GT2871 | 440cc | 26psi OB | 330 |
| 0xB9F0FD51 | Stage 1 GT3071 R9 440cc Siemens | GT3071 | 440cc | 26psi OB | 346 |
| 0x28C04D7B | RS2 91Oct | RS2 turbo | 440cc | — | — |

**All AAN/ABY/ADU tunes require:** 3.0 BAR MAP sensor, 5.0 BAR FPR, stock MAF.

### Boost Chips (551AA_0202_boost variant, build 0x0054)

| CRC32 | Label | Compatible fuel chips |
|-------|-------|----------------------|
| 0x69156B3A | GT2871 Stage 1 | 0x2EB58546, 0xA77BB88E, 0x9A8A6B4E |
| 0x39DC67DA | GT3071 Stage 1 26-23psi | 0x6F3AE675, 0x07DA1752, 0xB9F0FD51 |

### 7A Hitachi ECU Chips (different ECU family — for 7A 20v Tuner project)

| CRC32 | ECU | Application |
|-------|-----|-------------|
| 0x84B0504E | 893906266B (early) | 7A NA Big MAF 91Oct R2 |
| 0xC075767F | 893906266B (early) | 7A Stage 1 91Oct R1 |
| 0xA01C4EDA | 893906266D (late) | 7A Turbo Stage 2 550cc |
| 0x55177DDB | 893906266B (early) | 7A Turbo Kit Stage 1 R2 |

Note: Two 54903-byte 266D files use a non-standard scramble format — not supported.

### AAH V6 (MMS-200 ECU — different ECU family)

| CRC32 | ECU | Application |
|-------|-----|-------------|
| 0x4818FA0B | 8A0906266A MMS-200 | AAH/AKH 12v V6 Stage 1+ |

Requires: MMS-200 ECU, big bore MAF (Audi 078 133 471A). MMS300+ cannot be chipped.

---

## Boost Chip Architecture

### Map Addresses (All 551x variants, confirmed from vwnut8392 XDF + binary RE)

| Map | Address | Dims | Unit | Confidence |
|-----|---------|------|------|-----------|
| Boost Pressure Target | 0x2520 | 10×16 | kPa | CONFIRMED |
| N75 Wastegate Duty | 0x2480 | 10×16 | %DC | CONFIRMED |
| Max Boost Pressure | 0x2A96 | 8×1 | kPa | CONFIRMED |
| Characteristic Map | 0x264B | 8×10 | raw | CONFIRMED |
| Correction Table | 0x2218 | 25×8 | raw | CONFIRMED |
| Boost Unknown A | 0x2ACE | 10×16 | raw | PROVISIONAL |
| Boost Unknown B | 0x2B6E | 10×16 | raw | PROVISIONAL |
| Boost Unknown C | 0x2C0E | 10×16 | raw | PROVISIONAL |
| Boost Limit Detail | 0x2A8C | 2×9 | kPa | PROVISIONAL |

All maps have mirrors at address + 0x4000 (A15 state irrelevant).

### Axis Values (estimated — NOT stored as data table in ROM)

The boost chip MCU accesses axis values via embedded code (no MOVC table found).

```
RPM axis (10 pts): 600, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 7200
Load axis (16 pts): 1–16 (MAF-proportional relative load)
```

Requires Ghidra disassembly to extract precisely from the MCU code.

### Decode

- Boost pressure: `raw / 255 × sensor_range_kPa` (300kPa for 034EFI, 200kPa for stock)
- N75 duty: `raw / 255 × 100` = %DC
- For ABY stock boost: N75 rows 0-4 all zero (no duty below 2000 RPM)

---

## 3B / RR Boost Chip Architecture

| Chip | Size | Build | Map count |
|------|------|-------|-----------|
| 3B 447907404AA | 8KB | 0x0254 | 4 ign + data |
| RR 857907404B | 8KB | 0x0255 | 4 ign + data |

Maps confirmed from PRJ MapFinder output. Executable MCU chip — tables in upper data region.

---

## Application Features (v0.7.0)

### Map Editing
- **Edit cells** — double-click, type decoded value (°BTDC, AFR, or raw)
- **Copy/paste** — Ctrl+C/V, TSV format (Excel-compatible)
- **Select all** — Ctrl+A
- **Undo/redo** — Ctrl+Z/Y (30-level stack)
- **Delete** — Del key clears to 0
- **Right-click context menu:**
  - Scale selection (× factor with unit hints)
  - Interpolate rows (linear between first/last col)
  - Interpolate columns (linear between first/last row)
  - Smooth (3-point running average)
  - Fill with value
  - Invert (255-x)
  - Add cell note (annotation with `*` marker)
  - Copy map from another ROM (.bin or .034)
  - Undo/redo with count badges

### Display
- **Decoded/raw toggle** — switch between °BTDC/AFR/% and raw bytes
- **Heat map colours** — ign (blue→green→orange), fuel (dark→warm), generic
- **Axis labels** — read dynamically from ROM descriptor (runtime-updated)
- **Cell tooltips** — raw + decoded value + annotation
- **Hover status** — pushes cell info to status bar
- **Statistics strip** — min/max/mean/range for selection or all cells
- **Grid view** — all maps as thumbnail heat-maps, click to jump

### Analysis
- **Find in maps (Ctrl+F)** — search all confirmed maps by decoded value with operator (> ≥ < ≤ = ≠)
- **Tuning health scan (Ctrl+Shift+S)** — 7 automated checks:
  - Checksum validity (PRJmod only, skip stock chips)
  - Ign advance out of range (warn >52°, error >58°)
  - Fuel lean/rich extremes
  - Repeated rows (copy-paste indicator)
  - Abrupt cell-to-cell jumps
  - Boost pressure near sensor limit
  - Results double-clickable → jump to cell
- **Compare tab** — 3-panel diff (ROM A / Δ / ROM B)
  - Decoded delta (°BTDC, AFR units)
  - Max/min Δ stats
  - Delta intensity strip (visual per-map change indicator)
  - Jump to most-changed map
  - Export diff report (.txt)
- **Compare to stock baseline** — auto-loads matching stock chip by variant
- **Data log overlay** — load CSV log, annotate cells with hit count
  - Auto-detects VCDS (semicolon, German locale) and generic OBD-II CSV
  - Shows coverage %: visited/unvisited cells
  - Annotates: ✓ (well-logged), ~ (sparse), ⚠ (few hits), ✗ (never logged)

### Import/Export
- **Open** — .bin, .034 (auto-descramble), drag & drop
- **Save** — .bin or .034 Rip Chip format, checksum auto-applied
- **Import XDF** — TunerPro XDF v1.50 (File menu), session-only injection
  - Auto-inject for 551AA_0202: loads RS2 551B fuel timing XDF automatically
- **Export map as HTML** — styled heat-map table, printable
- **Export full ROM reference (HTML)** — all confirmed maps, single page
- **Export session changelog** — HTML or text, per-cell edit history with timestamps
- **Export diff report** — text format, all changed cells with decoded units
- **CLI scan** — `python -m urrom.cli scan rom.034 [--strict] [--json]`

### Tuning Tools (Tools menu)
- **Injector scaling wizard** — guided cc → cc rescaling of all fuel maps
  - Optional FPR pressure adjustment (flow ∝ √P)
  - Shows effective flow, scale factor, max duty cycle
- **Fuel pressure calculator** — standalone FPR + injector sizing calculator
- **Data log overlay** — CSV import → cell hit count annotation

### Hardware Tab
- **LC/NLS scalar panel** (551AA_0202 only) — 8 editable scalars:
  - Hard RPM limit (WH 0x0617, ×40 = RPM)
  - LC speed threshold (WH 0x0620, ×2 = km/h)
  - LC ign retard RPM, LC ign angle, NLS min RPM, NLS ign angle
  - Spark cut knock RPM, LC ign cut RPM
  - All write back to ROM and trigger save-dirty
- **MAP sensor detection** — identifies 200/250/300/400 kPa from boost chip
- **Patch detection** — SD mode, MFTS bypass, load decap, etc.
- **Boost chip pairing** — validates fuel+boost pair, warns on mismatch

### Overview Tab
- 5-column map inventory with confidence badges (double-click → jump to map)
- Chip info: ECU PN, EPROM type, boost chip status
- Hardware requirements: MAP sensor, injectors, FPR, boost from KNOWN_CRCS
- **Health badge** — ✓ clean / ⚠ N warnings / ✗ N errors
- Boost chip pairing requirement indicator

### Quality of Life
- **Recent files** — last 8 ROMs, QSettings-persisted
- **Dirty indicator** — `•` in window title for unsaved changes
- **Window title** — shows ROM filename and variant after load
- **Axis editor** — view/edit RPM and load axis values
- **Help menu** — keyboard shortcuts, About dialog
- **Session log** — per-cell edit tracking, exportable

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+O | Open ROM |
| Ctrl+S | Save ROM |
| Ctrl+F | Find in maps |
| Ctrl+Shift+S | Scan for issues |
| Ctrl+Z | Undo |
| Ctrl+Y / Ctrl+Shift+Z | Redo |
| Ctrl+C | Copy selection (TSV) |
| Ctrl+V | Paste |
| Ctrl+A | Select all |
| Del | Clear selection |
| Double-click cell | Edit (type decoded value) |
| Right-click | Context menu |
| Overview row double-click | Jump to map editor |

---

## File Structure

```
UrROM/
├── app/
│   └── main.py              # Main application (Qt UI, ~4500 lines)
├── urrom/
│   ├── ecu_profiles.py      # All variant definitions, KNOWN_CRCS, map addresses
│   ├── descramble.py        # .034 Rip Chip scramble/descramble
│   ├── hw_patches.py        # Hardware patch detection (SD mode, MFTS bypass etc.)
│   ├── tuning_checks.py     # Automated tuning health scanner (7 checks)
│   ├── map_export.py        # HTML map export (single + full ROM reference)
│   ├── session_log.py       # Per-cell edit changelog with HTML/text export
│   ├── datalog.py           # CSV data log parsing and cell coverage overlay
│   ├── xdf_import.py        # TunerPro XDF v1.50 parser
│   ├── kwp.py               # KWP2000 live data (dashboard + map overlay)
│   ├── cli.py               # Headless CLI (scan, info commands)
│   └── version.py           # Version string
├── roms/                    # Bundled reference chips (direct reads)
│   ├── aby_fuel-ign_551aa.bin     # ABY 551B — REAL ✓
│   ├── adu_fuel-ign_551c.bin      # ADU 551C — REAL ✓
│   ├── 3b_fuel-ign_404aa.bin      # 3B 404AA — REAL ✓
│   ├── rs2_d02_fuel-ign_551b.bin  # RS2 D02 551B_D02 — PARTIAL ⚠
│   ├── aan_fuel-ign_551a.bin      # AAN 551A — BLANK ✗
│   └── aan_fuel-ign_551aa.bin     # AAN 551AA — BLANK ✗
├── docs/
│   ├── M232_ECU_Hardware_Reference.md  # Full hardware RE notes
│   ├── AAN_ABY_551_RE_notes.md         # 551A/AA specific findings
│   ├── 3B_RR_ECU_RE_notes.md           # 3B boost chip notes
│   ├── M232_Master_Reference.md        # Schematic + MCU reference
│   └── UrROM_Master_Reference.md       # This document
├── tests/
│   └── test_ecu_profiles.py  # 130 tests
└── rs2_xdf/                  # Community XDF files
    ├── RS2 551B fuel timing.xdf  # PRJmod 551AA_0202 XDF (314 tables)
    └── 8D0907551B RS2 Boost.xdf  # Boost chip XDF (20 tables)
```

---

## Known Limitations / Open Research Items

| Item | Status | Notes |
|------|--------|-------|
| Boost chip RPM axis | ⚠ Estimated | Not stored as data table; requires Ghidra disassembly |
| V8 PT/ABH map addresses | ✗ Unconfirmed | Need actual ROM binaries |
| AAN stock calibration | ✗ Missing | Both AAN chips are factory-blank |
| RS2 D02 high-RPM area | ⚠ Blank | Partial calibration only |
| 551AA overrun fuel map (0x2D47) | ⚠ Provisional | Function not confirmed from code |
| Idle ign maps 0x3D05/0x3E65 | ⚠ Provisional | Mirror pair, 13-22°BTDC at ~2320 RPM |
| KWP group batching | ✗ Not implemented | Currently requests sequentially |
| Wideband lambda map overlay | ✗ Not implemented | AFR CSV → fuel map colour overlay |

---

## CLI Reference

```bash
# Scan ROM for tuning issues
python -m urrom.cli scan rom.034
python -m urrom.cli scan rom.bin --strict     # exit 1 on warnings
python -m urrom.cli scan rom.034 --json       # machine-readable output

# ROM identification
python -m urrom.cli info rom.034

# Exit codes
# 0 = clean (no issues)
# 1 = warnings found (with --strict)
# 2 = errors found
# 3 = ROM unrecognised or load failure
```

---

## Tuning Check Thresholds

| Check | Severity | Threshold | Exclusions |
|-------|----------|-----------|-----------|
| Checksum | Error | Invalid PRJmod checksum | Stock chips (CRC in KNOWN_CRCS as "Stock") |
| Ign advance high | Error | >58°BTDC | overrun, failsafe, LPG, correction, coolant, IAT maps |
| Ign advance warn | Warning | >52°BTDC | Same as above |
| Ign retard | Warning | <-18°BTDC | Same as above |
| Fuel lean | Error | raw >170 | failsafe, race fuel, LPG, correction, warmup maps |
| Fuel rich | Warning | raw <80 | Same as above |
| Repeated rows | Info | Consecutive identical rows | failsafe, VE table, LPG, overrun, etc. |
| Large jumps | Warning | >50 raw between adjacent interior cells | correction, coolant, IAT, adaptive maps |
| Boost near limit | Warning | raw >245 | — |
