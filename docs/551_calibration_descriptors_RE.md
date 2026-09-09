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
