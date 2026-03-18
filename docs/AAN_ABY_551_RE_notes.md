# AAN / ABY / ADU 551x RE Notes — Updated 2026-03

## Chip Calibration Status Summary

| Chip | CRC32 | Calibration | Trigger |
|------|-------|-------------|---------|
| ABY 551B (895907551B) | 0xA98CB481 | ✓ REAL | Cam+Hall D01+HS |
| ADU 551C (8A0907551C) | 0x4378E077 | ✓ REAL | Cam+Hall+RS2 |
| RS2 D02 (8A0907551B) | 0xC5B30158 | ⚠ PARTIAL | Distributor D02 |
| AAN 551A (4A0907551A) | 0xF7432BB5 | ✗ BLANK | Distributor D02 |
| AAN 551AA (4A0907551AA) | 0xBF11DB48 | ✗ BLANK | Cam+Hall D03+HS |

## Firmware Code Islands (0x0000-0x2CFF)

All variants use LJMP-fill with code islands. The code structure differs by trigger type:

### 551A (Distributor trigger)
- 6,376 non-0x02 bytes
- Reset vector: 0x0000 → LJMP 0x0400
- Trigger: external interrupt via distributor
- Used as PRJmod 551A D02PMC base ROM

### 551AA (Cam + Hall sensor trigger)  
- 10,292 non-0x02 bytes (+62% vs 551A)
- Reset vector: 0x0000 → LJMP 0x1297
- INT0 ISR: 0x0003 → LJMP 0x2000 → 0x0438
- Extra code handles reference tooth detection for cam trigger
- Used as PRJmod 551AA D03PMC base ROM

### 551B / 551C (Cam + Hall, real calibration)
- ABY 551B: 2,594 non-0x02 code bytes (similar trigger to 551AA)
- Calibration starts at WH 0x2D00 (confirmed from scan)
- Code region: 0x0000-0x2CFF (LJMP fill with islands)

## Calibration Map Layout (IDENTICAL across 551A/AA/B/C)

| Map | WH Address | Dims | Confidence |
|-----|-----------|------|-----------|
| Fuel P/T | 0x2E17 | 16×16 | CONFIRMED |
| Ign Map 1 (PT primary) | 0x30AC | 16×16 | CONFIRMED |
| Ign Map 2 | 0x3263 | 16×16 | CONFIRMED |
| Ign Map 3 | 0x3387 | 16×16 | CONFIRMED |
| Ign Map 4 | 0x3598 | 16×16 | CONFIRMED |
| Ign Map 5 | 0x36BC | 16×16 | CONFIRMED |
| Ign Map 6 | 0x380D | 16×16 | CONFIRMED |
| Ign Map 7 | 0x3931 | 16×16 | CONFIRMED |
| Idle Ign Map A | 0x3D05 | 16×16 | PROVISIONAL |
| Idle Ign Map B (mirror) | 0x3E65 | 16×16 | PROVISIONAL |
| End-of-Cal RPM table | 0x3FE0 | 2×16 | PROVISIONAL |

PRJmod 551AA_0202 maps are at completely different addresses (fuel at 0x0E13).

## ABY vs ADU Fuel Calibration

164 of 256 fuel cells differ between ABY and ADU:
- ABY (S2 Coupe): fuel raw 124-170, max advance 45.7°BTDC
- ADU (RS2 Avant): fuel raw 123-163, max advance 45.7°BTDC
- Different turbocharger = different boost profile = different fuel curve
- Both reach same max advance but differ cell-by-cell

## Confirmed Calibration Area Start

ABY WH 0x2D00 scan shows non-0x02 data starting here.
First map headers (0x3A/0x3F markers) found at:
- 0x2D1D → map data at 0x2D47 (unknown map type)
- 0x2F3D → map data at 0x2F64 (unknown map type)  
- 0x3084 → Ign Map 1 at 0x30AC (confirmed fuel/ign map)
- 0x3CE0 → Idle Ign A at 0x3D05 (PROVISIONAL)
- 0x3E40 → Idle Ign B at 0x3E65 (PROVISIONAL, mirror of A)

## 034EFI Stock Rip Chip vs ABY Direct Read

The 034EFI "Stock Rip Chip" (0x956BFC9C) is a RECONSTRUCTED stock tune:
- Some high-load cells contain 0x02 (not reconstructed)
- Slightly different idle calibration vs direct ABY read
- Used as PRJmod 551AA_0202 format → maps at different addresses

The ABY 551B (0xA98CB481) is the ONLY confirmed real direct chip read.
Use it as the stock baseline for S2 Coupe tuning.
