# UrROM  v0.5.0

Open-source ROM editor for **Bosch Motronic M2.3 / M2.3.2** — the ECU family
used in Audi's 5-cylinder 2.2 20v turbo and V8 engines.

---

## Supported ECUs

### 5-cylinder 2.2 20vT — dual EPROM (main chip + boost chip)

| Engine | ECU Part Number | Software | Application |
|--------|----------------|----------|-------------|
| 3B     | 447 907 404 AA | 404      | Audi 200 20vT / UrQuattro / S2 early |
| 3B/RR  | 857 907 404 B  | 404      | RR S2 Coupe |
| AAN    | 4A0 907 551 AA | 551AA    | UrS4 / UrS6 |
| AAN    | 4A0 907 551 A  | 551A     | UrS4 early (distributor trigger) |
| ABY    | 895 907 551 B  | 551B     | S2 Coupe (cam trigger) |
| ADU    | 8A0 907 551 C  | 551C     | RS2 Avant (cam trigger, 300kPa MAP) |
| RS2 D02| 8A0 907 551 B  | 551B_D02 | RS2 Avant early (distributor trigger) |

### V8 32v — single EPROM

| Engine | ECU Part Number | Software | Application |
|--------|----------------|----------|-------------|
| PT     | 443 907 404 A  | 404V8    | Audi V8 3.6L |
| ABH    | 4A0 907 557 A  | 557      | Audi V8 4.2L |

### 034EFI prjmod (551AA\_0202)

| Firmware | ECU PN | Application |
|---|---|---|
| 4A0 907 551 AA + prjmod | Any 551AA ECU | 034EFI RipChip / prjmod base ROM |

All 034EFI `.034` Rip Chip files (Stage 1, Stage 1 variants, big turbo WMI, etc.)
are auto-detected and descrambled on open.

---

## Features

### ROM management
- Open `.bin` and `.034` (034EFI Rip Chip) files — `.034` descrambled automatically
- Save as `.bin` or re-scramble back to `.034` format
- PRJmod checksum computed and applied on save for all tuned variants
- Stock Bosch chips: ASCII ID string preserved (no computed checksum)
- Dual-EPROM: main chip (fuel/ign) + boost chip loaded and edited independently

### Map editor
- Heat-mapped editable 16×16 (or N×M) fuel and ignition tables
- **Real axis labels** — RPM and load values read from Bosch map descriptor headers
- Decoded display: fuel as AFR, ignition as °BTDC
- Live KWP1281 cursor overlay — current operating cell highlighted while driving
- **Right-click context menu:**
  - Copy / Paste (TSV — paste into Excel or between maps)
  - Scale selection (multiply all cells by factor)
  - Linear interpolate rows
  - 3-point smooth
  - Fill with value
  - Invert (255 − x)

### Speed-density (SD) mode
- VE table editor (WH 0x2074, 16×16) for prjmod SD builds
- SD active detection: notice bar when VE table is non-blank
- Warning when editing fuel P/T map while SD mode is active

### Boost chip
- Boost pressure, N75 duty cycle, characteristic map, limit tables
- All 9 confirmed / provisional tables from vwnut8392 XDF
- Separate boost chip file load

### Hardware detection
- MAP sensor type identified from boost chip calibration constants
- Firmware patch detection: SD mode, LC/NLS, MFTS bypass, load decap
- **LC/NLS scalar panel** — 8 scalars with decoded RPM values (prjmod 0x0202 only)

### Compare / diff
- Side-by-side A vs B comparison for any map
- Delta cells show decoded units (°BTDC, AFR) — not raw byte deltas
- Changed cell count + max/min Δ in summary bar
- Accepts `.034` for ROM B

### Live dashboard (requires KWPBridge)
- 10-gauge panel: RPM, ECT, Load, Lambda, Timing, MAP, N75, IAT, Speed, Knock
- 5-channel per-cylinder knock display
- Status strip with compact live readout

### Overview tab
- Full map inventory with address, size, unit, confidence
- Double-click any map → jumps directly to editor
- Chip info: ECU PN, EPROM type, boost chip status
- Checksum state and tuning warnings

---

## ROM status

> **Before writing any ROM** — always read and save the original chip first.
> Read it twice, compare the files, keep both copies.

| Variant | Fuel map | Ign maps | Boost | Status |
|---|---|---|---|---|
| 3B / RR (404) | ✓ Confirmed | ✓ Confirmed | Provisional | Safe to edit |
| AAN (551AA) | ✓ Confirmed | ✓ Confirmed | ✓ Confirmed | Safe to edit |
| ABY (551B) | ✓ Confirmed | ✓ Confirmed | ✓ Confirmed | Safe to edit |
| ADU (551C) | ✓ Confirmed | ✓ Confirmed | ✓ Confirmed | Safe to edit |
| 034EFI (551AA\_0202) | ✓ 132 maps | ✓ Full PRJ XDF | ✓ Confirmed | Full feature set |
| V8 PT / ABH | ❌ Unconfirmed | ❌ Unconfirmed | N/A | Read-only until verified |

**Map address notes:**
- Stock ABY/ADU calibration occupies WH 0x2D00–0x3FFF (code fills 0x0000–0x2CFF)
- PRJmod (034EFI) uses a different firmware layout with maps starting at WH 0x0000
- AAN direct chip read still pending to confirm addresses match ABY layout

---

## Hardware reference

See `docs/M232_ECU_Hardware_Reference.md` for:
- Two-board architecture (Zusatzplatte + Grundplatte) with full IC inventory
- SAB80C535 CPU spec, ADC SFRs, ADC channel map (all 8 channels confirmed)
- Firmware ID string format and full variant table
- PRJmod checksum algorithm, WinlogDriver decode formulas, MAP sensor constants
- Hardware modification procedures (R660 removal, R201 resistor swap, socket install)
- LC/NLS scalar addresses and EC tuning reference

---

## Quick start

```bash
pip install PyQt5
python main.py
```

Open a `.bin` or `.034` file via the toolbar or File menu.

