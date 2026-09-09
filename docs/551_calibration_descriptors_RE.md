# 551-Series Main Chip — Split-Bank Layout and Firmware Descriptor Tables

*Session 2026-09-08. Sources: prj/m232 `src/idb/Fuel_Ign_ADU_551b.idb` and
`aduboost.idb` (IDA databases, read with python-idb), `tools/dis8051.py`, and
every 551 image in `roms/` plus prj's `stock_AANABY_27c512.bin`.*

## 1. The 64 KB chip is split-bank, not mirrored

Every 551 main-chip image we hold has the same shape:

| Half | Content | Evidence |
|---|---|---|
| Lower 32 KB (0x0000–0x7FFF) | **8051 firmware** | starts `02 13 29` / `02 12 97` / `02 11 7A` (LJMP reset), 5–8 % of bytes are 0x02 |
| Upper 32 KB (0x8000–0xFFFF) | **calibration** (UrROM's "working half") | 71–80 % 0x02 filler, starts `78 D6 E2` or `02 04 00`, maps from ~0x2D00 (ADU/ABY) or ~0x0B00 (RS2 D02 / AAN) |

The halves are never identical. The old model ("upper half = working half,
lower half = mirror") was wrong; the layout is the same as the V8 557 chips.
Consequences fixed in this session:

- **Save** used to write the calibration into both halves → destroyed firmware.
  `assemble_output()` now keeps the firmware half.
- **Firmware patches and LC/NLS scalars** (MFTS at 0x1254, LC/NLS signature at
  0x062E, load decap at 0x3679, rev limit at 0x0617) live in the *lower* half.
  The Hardware panel now receives the firmware half; `_write_wh` no longer
  mirrors to +0x8000.
- prj's IDB image is byte-for-byte the lower half of `rs2_d02_fuel-ign_551b.bin`
  (2 bytes differ). PRJmod's base files differ from it by ~600 bytes — i.e.
  **PRJmod = stock 8A0907551B firmware with ~600 bytes patched**.

## 2. How the firmware finds its maps

prj named the map reader `READ_MAP` (0x0FF9) and `READ_MAP_NOINTERP` (0x10DD).
Calling convention:

```
R2         = map index
RAM 77h:78h = index-table base   (calibration half, e.g. 0xA000 / 0x8000)
RAM 75h:76h = pointer-table base (calibration half, e.g. 0xA800 / 0x8800)

off  = idx_table[R2]           ; 0xFF = no map; bit0 = 1 → 2-D map
ptr  = BE16(ptr_table[off & 0xFE])   ; big-endian → descriptor address
```

The base pairs are loaded by small `MOV 75h,#..; MOV 76h,#..; MOV 77h,#..;
MOV 78h,#..` stubs (0x4DA9–0x4F5B in the 551B firmware). ADU/ABY firmware uses
44 pairs around 0xA000/0xA800; RS2 D02 and AAN use 0x8000/0x8800.

### Descriptor format

```
[X input RAM addr][nX][nX delta bytes]  [Y input RAM addr][nY][nY deltas]  [data...]
```

- Input RAM addresses (prj's names): `3Ah` RPM, `3Fh` LOAD, `38h` ECT, `37h` IAT,
  `36h` UBAT, `39h` MFTS. The "3A/3F header markers" in older notes are exactly
  these bytes.
- Axis breakpoints: `breakpoint_k = 256 − Σ(delta_k … delta_n)`; RPM ×40.
  ADU fuel-map RPM deltas `0A 06 07 06 06 0D 0C 0D 0C 0F 0F 0D 07 0D 11 4C`
  decode to 600 1000 1240 … 7200, matching the axis vwnut8392's XDF listed.
- Data is row-major `[X][Y]` → rows = RPM, cols = LOAD for the 16×16 maps.

`urrom.ecu_profiles.decode_descriptor_tables(full_rom)` and
`python -m urrom.cli maps rom.bin` enumerate every referenced map.

## 3. Firmware-confirmed 16×16 RPM×LOAD maps

| Chip | Fuel | Ign 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| ADU 8A0907551C | 0x2E17 | 0x30AC | 0x3263 | 0x3387 | 0x3598 | 0x36BC | 0x380D | 0x3931 |
| ABY 895907551B | 0x2E17 | 0x30A8 | 0x325F | 0x3383 | 0x3594 | 0x36B8 | 0x3809 | 0x392D |
| RS2 D02 8A0907551B | 0x0E13 | 0x10A8 | 0x125F | 0x1383 | 0x1594 | 0x16B8 | 0x1809 | 0x192D |
| AAN 4A0907551AA | 0x0DEA | 0x106D | 0x1224 | 0x1348 | 0x155F | 0x1683 | 0x17D4 | 0x18F8 |

(All working-half offsets. ABY = ADU − 4 from 0x3026 onward. The RS2 D02 row
is the PRJmod layout; PRJ's XDF role names — overrun 0x10A8, no-knock 0x125F,
knock-L1 0x1594 — are used for that family.)

The seven ADU/ABY ignition maps were downgraded to UNVERIFIED in March 2026
because "0x30AC–0x3931 contain 8051 opcodes". That analysis was done on the
wrong half. They are firmware-referenced RPM×LOAD tables; downgrade reverted.

Also confirmed: the "idle ignition" blocks are 4×6 (RPM×LOAD) at ADU 0x3D00 /
0x3E60 (ABY 0x3CFC / 0x3E5C); the older 3×6 view began one row in.

## 3b. The same mechanism on the 3B / RR / S2 (404) fuel/ign chip

The M2.3 404 firmware has a byte-for-byte structural twin of READ_MAP at
`0x0D92` (same PUSH sequence, same 77h:78h index / 75h:76h pointer bases, same
2-D flag in bit 0). Base-pointer stubs start at `0x3423`
(`MOV 75h,#65h; MOV 76h,#D0h; MOV 77h,#60h; MOV 78h,#00h`): index tables at
`0x6000+`, pointer tables at `0x65D0+`, all flat 32 KB addresses. 58 base
pairs → 126 descriptors, identical set on 3B 404AA, 404A, RR 404B and S2.

Eleven 16×16 RPM×LOAD maps — 4 fuel + **7 ignition**, the 551's structure:

| Fuel | 0x6A8E | 0x6C1C | 0x6D74 | 0x6E98 | | | |
|---|---|---|---|---|---|---|---|
| **Ign** | 0x7076 | **0x71F8** | **0x731C** | **0x7440** | 0x7667 | 0x77CF | 0x7937 |

The three bold ignition maps were missing from the MapFinder-derived list.
Ign map 1 is identical on 3B / RR / S2; map 3 (0x731C) differs from the S2
chip in 251 of 256 cells. Axes decode exactly: RPM 600…7200 (same deltas as
the 551), load 14…190. `python -m urrom.cli maps roms/3b_fuel-ign_404aa.bin`
lists everything, including the 4×6 idle-ignition family at 0x7C06–0x7D16,
the 12×12 RPM×UBAT (dwell-shaped) table at 0x68BA and the ECT×IAT warm-up
tables.

## 3c. 3B ignition map selection (traced 2026-09-08)

The ignition calculation (`0x1601`–`0x16F3`, READ_MAP call at `0x16FB`) picks a
slot in the active ignition table set:

| Step | Code | Slot → map |
|---|---|---|
| Any fault flag XRAM `F3.0 / F4.0 / D9.0 / D9.1` set | `0x1611–0x162F` | slot 04 → **Ign Map 1** (fault fallback; identical on all three chips) |
| `20h.1 \| 27h.3 \| 27h.0` set | `0x1632–0x163C` | slot 06 (small table) |
| Load / RPM window from 1-D tables slots 1C / 1D (hysteresis flags 2Fh.0/2Fh.1) | `0x163F–0x167B` | high load → slot **12** (main map), or slot 18 (**Ign Map 3**) if XRAM `CAh.0`; low load → slot 0A/0E (idle family), slot −1 if 2Fh.0 |

The ignition table set (which map sits in slot 12) is chosen by the chain at
`0x34B7`–`0x3537`, pointer table `0x6628`:

| boost-board bit `20h.2` | coding bit `A0h.6` | slot 12 → | table sets |
|---|---|---|---|
| 0 | 0 | **Ign Map 2** (0x71F8) | 0x6093 / 0x60B7 (± `A0h.2`) |
| 0 | 1 | **Ign Map 5** (0x7667) | 0x60DB / 0x60FF |
| 1 | 0 | **Ign Map 6** (0x77CF) | 0x6123 / 0x6147 (`20h.4`=1), 0x61B3 / 0x61D7 (`20h.4`=0) |
| 1 | 1 | **Ign Map 7** (0x7937) | 0x616B / 0x618F, 0x61FB / 0x621F |

- `20h` = byte read from the boost board over the inter-board bus at
  `0xA040`, XOR 0x0E (`0x1376–0x1384`, `0x4A31`). The boost MCU also hosts
  knock detection, so bit 2 being a knock / retard-active flag is the prime
  candidate. **Unconfirmed** — the boost-side composition (`0x08A8` → `5Fh` →
  P4 nibble) still needs tracing.
- `A0h` = coding-plug class: ADC channel 4 (`0x3637`) binned against
  thresholds `0x3669` (`FF DC CD A9 85 66 3D 32 1F`, 9 bands), then
  `A0h = table 0x3672[band]` = `4C 04 14 08 00 44 40 10 20`. Bit 6 set for
  bands 0, 5, 6; bit 2 for bands 0, 1, 5. `21h.0` and `20h.2` further
  offset a variant number written to `9Eh` (`0x3657–0x3667`).
- **Ign Map 3** (slots 0F / 18 in every set) is added as a correction in
  routine `0x1B2A` (`0x1B38`, plus two more slots when `2Eh.6`), and is the
  base map when XRAM `CAh.0` is set — that flag is only zeroed by init in
  running code, so it looks tester-controlled.
- **Ign Map 4** (slot 15) is a scaled correction: `0x1B5D`, gated by
  `2Eh.4` (mirrored to XRAM `7Ch.0`), scaled by `0x6750[XRAM 15B]`.

So for the S2-chip question: map 1 and map 4 are identical on all chips; the
S2 differs in the main maps 2 / 5 / 6 / 7 (~90 cells each) and massively in
the map-3 correction (251 cells). Which of maps 2 / 5 / 6 / 7 the car
actually runs depends on the coding plug and on the boost-board flag.

## 4. Open items

- Which of the seven ignition maps is active under which condition
  (trace `IGNITION_CALC` 0x2118 / `j_IGNITION_CALC` 0x18D2 in the IDB).
- Ignition decode formula: RS2.xdf says `×0.6491 − 8.2186`, PRJ's XDF says
  `×0.75 − 22.5`. Both are kept per family; one of them is wrong for one family.
- AAN 4A0907551A (D02, reset 0x117A): calibration blank in our sample, so its
  descriptor tables could not be decoded; RS2 D02 layout assumed (PROVISIONAL).
- The 0xA0xx / 0x83xx / 0x85xx / 0x8Axx `MOV DPTR` immediates in the firmware
  are external-RAM (MOVX) addresses (IPC with the boost board at 0xA040/0xA080),
  not calibration.
