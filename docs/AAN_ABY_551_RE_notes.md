# Bosch M2.3.2 — AAN / ABY 551-Series ECU RE Notes
## Firmware Comparison: 551a (ABY/early-AAN) vs 551aa (AAN)

Session date: 2026-03-18  
Files analysed: `aby_fuel-ign_551aa.bin`, `aby_boost_551aa.bin`, `aan_fuel-ign_551aa.bin`

---

## 1. FILE INVENTORY

| File | Size | CRC32 | Working half | Build |
|---|---|---|---|---|
| `aby_fuel-ign_551aa.bin` | 65536 (64KB doubled) | 0x97D26DD1 | Lower 32KB | 0x0274 |
| `aby_boost_551aa.bin` | 32768 (32KB mirrored) | 0xF6E33043 | Lower 16KB | 0x0202 |
| `aan_fuel-ign_551aa.bin` | 65535 (truncated by 1B) | 0xB9A49F8A | Lower 32KB | 0x0812 |

> Note: `aan_fuel-ign_551aa.bin` is 65535 bytes — one byte short of a complete
> 64KB read. The lower 32KB working half is intact and usable. Upper half
> should be re-read for completeness.

### Working Half Layout
- **Fuel/ign chips**: 64KB physical file, **lower 32KB is the valid working half**
  (upper half is uninitialized/garbage — confirmed by nonsense reset vector in upper half)
- **Boost chip**: 32KB physical file, **lower 16KB is the working half** (mirrored)

---

## 2. HALL SENSOR ARCHITECTURE EVOLUTION

The AAN engine underwent a hardware revision mid-production affecting how
the ECU reads crank/cam position:

### Early AAN / ABY (file: `aby_fuel-ign_551aa.bin`)
- Hall sensor mounted in the **distributor** (same physical location as 3B/RR)
- Engine codes: ABY (S2 Coupe), early AAN
- ECU variant: **551a** (Bosch internal, maps to `aby_` filename prefix here)
- Firmware build: **0x0274**
- Reset vector: `LJMP 0x1329`

### Late AAN (file: `aan_fuel-ign_551aa.bin`)
- Hall sensor relocated to **camshaft timing belt pulley** 
- Coil-on-plug ignition (no distributor)
- Engine codes: AAN (UrS4 / S6)
- ECU variant: **551aa**
- Firmware build: **0x0812**
- Reset vector: `LJMP 0x1297`

---

## 3. INTERRUPT VECTOR TABLE

All variants (3B, RR, ABY, AAN) share the **same interrupt vector layout**
with the sole exception of the reset vector address:

| Vector | Address | 3B/RR | ABY | AAN |
|---|---|---|---|---|
| Reset | 0x0000 | `LJMP 0x0F99` | `LJMP 0x1329` | `LJMP 0x1297` |
| INT0 (crank) | 0x0003 | `LJMP 0x2000` | `LJMP 0x2000` | `LJMP 0x2000` |
| Timer0 | 0x000B | `LJMP 0x2010` | `LJMP 0x2010` | `LJMP 0x2010` |
| INT1 (cam) | 0x0013 | `LJMP 0x2030` | `LJMP 0x2030` | `LJMP 0x2030` |
| Timer1 | 0x001B | `LJMP 0x2050` | `LJMP 0x2050` | `LJMP 0x2050` |
| Serial | 0x0023 | `LJMP 0x2060` | `LJMP 0x2060` | `LJMP 0x2060` |
| Timer2 | 0x002B | `LJMP 0x2070` | `LJMP 0x2070` | `LJMP 0x2070` |
| INT2 | 0x0043 | `LJMP 0x2080` | `LJMP 0x2080` | `LJMP 0x2080` |
| INT3 | 0x004B | `LJMP 0x2090` | `LJMP 0x2090` | `LJMP 0x2090` |

**INT0 (0x2000) and INT1 (0x2030) are the crank and cam trigger handlers.**
All 551-series variants redirect INT0 → `0x0438` and INT1 → `0x03CA`.
The 3B/RR redirects INT0 → `0x0339` and INT1 → `0x0211` — different handler
addresses, consistent with the different distributor-based trigger hardware.

### Reset Handler Code Comparison

The reset handler prologue reveals the 3B vs 551 divergence clearly:

```
3B/RR  @ 0x0F99: D2 BE  D2 DE  75 83 A0  75 82 81  74 01 F0  75 82 10  74 21 F0 ...
AAN    @ 0x1297: D2 BE  D2 DE  75 83 A0  75 82 81  74 01 F0  75 82 10  74 21 F0 ...
ABY    @ 0x1329: E5 A9  A2 E6  92 D5 D2 BE  D2 DE  75 83 A0  75 82 81  74 01 F0 ...
```

- 3B and AAN share **identical reset prologue** (`D2 BE D2 DE 75 83 A0...`)
- ABY has **two extra bytes** at entry (`E5 A9 A2 E6 92 D5`) before the shared
  prologue — likely reading/testing a hardware configuration register to detect
  distributor vs cam-pulley hall sensor variant at runtime
- One byte difference at offset +0x11: 3B/RR has `74 88`, AAN has `74 18`,
  ABY has `74 18` → AAN and ABY share this constant; 3B differs

---

## 4. FIRMWARE DIVERGENCE: ABY vs AAN

ABY(551a) vs AAN(551aa) differ by **92.9%** of the working half bytes —
these are not minor calibration variants, they are substantially different
firmware builds with different code layout throughout.

| Region | Size | Description |
|---|---|---|
| 0x0001–0x0002 | 2B | Reset vector address only |
| 0x00D8–0x018D | 182B | Startup / hardware init sequence |
| 0x0335–0x0361 | 45B | MOVX address constants (hardware-specific) |
| 0x05AC–0x1F47 | 6556B | **Main code body** — largest block |
| 0x20E1–0x7FFE | 24350B | Calibration tables + code (entire upper half) |

The 6556-byte main code body diff at `0x05AC–0x1F47` is where the
distributor vs cam-pulley trigger handling diverges. This region contains
the RPM computation, ignition scheduling, and crank/cam synchronisation logic.

---

## 5. BOOST CHIP COMPARISON

| Field | 3B/RR | ABY/AAN (551) |
|---|---|---|
| Physical size | 8KB | 32KB (mirrored, 16KB core) |
| Working half | 8KB (entire chip) | 16KB (lower half) |
| Architecture | Executable 8051 MCU code | Executable 8051 MCU code |
| Startup | `CLR EA` (0xC2 0xAF) at 0x0000 | `CLR EA` (0xC2 0xAF) at 0x0000 |
| Build (ABY) | 0x0254 (3B) | 0x0202 |
| Code similarity | — | ~0% (completely different) |

Both 3B and 551-series boost chips start with `C2 AF` (`CLR EA`) at address
0x0000 — the same inline startup pattern — confirming the **dual-MCU
architecture is present on both 3B and 551 variants**.

The 551 boost chip is 4× the size (16KB core vs 8KB) — more code space for
the camshaft-based boost scheduling that the coil-on-plug AAN requires.

---

## 6. IPC REGISTERS

All 551-series fuel chips access the same inter-processor communication
registers as the 3B:

| Address | AAN refs | ABY refs | 3B refs |
|---|---|---|---|
| 0xA040 | 4× | 4× | 8×+ |
| 0xA080 | 1× | 1× | 8×+ |
| 0xA021 | 13× | 14× | — |
| 0xBE00/01 | 4×/3× | 4×/5× | 4×/4× |

`0xA021` appears frequently in both 551 variants but not in 3B — this may be
the cam position register used by the coil-on-plug ignition sequencer.
`0x8515` (AAN) / `0xA522` (ABY) each appear 30× — likely the main MAP/load
register, but addresses differ between variants (different hardware layout).

---

## 7. BUILD NUMBER SUMMARY — ALL VARIANTS

| Variant | Engine | Fuel build | Boost build | Cal tag |
|---|---|---|---|---|
| 3B AA | 3B (200 20vT) | 0xF004 | 0x0254 | 0x029B |
| RR B | RR (UrQuattro) | 0xF004 | 0x0255 | 0x0253 |
| ABY 551a | ABY / early AAN | 0x0274 | 0x0202 | 0x7F02 |
| AAN 551aa | AAN (UrS4/S6) | 0x0812 | — (not yet read) | 0x0202 |

The 3B firmware build `0xF004` stands completely apart from the 551-series
builds (`0x0274`, `0x0812`). This is consistent with 3B/RR being an older
M2.3 codebase and AAN/ABY being M2.3.2 — a genuine firmware generation gap,
not a minor revision.

---

## 8. MAP ADDRESS DIFFERENCES

The 3B map addresses (`0x6A6A`, `0x7052` etc.) are **all in code space** in
the 551-series working halves — the 551 firmware has a completely different
memory layout. Map addresses must be located separately for each variant.

Known 551-series map addresses (from prior UrROM sessions with XDF data):
- These are documented in `VARIANT_551AA` / `VARIANT_551C` in `ecu_profiles.py`
- The 3B MapFinder addresses do **not** apply to 551 variants

---

## 9. URROM IMPLICATIONS

### VARIANT_551AA / VARIANT_551C updates
- Confirm `working_half_offset = 0` (lower half, not upper half)
- ABY boost chip: 32KB physical, 16KB working half — update `BOOST_CHIP_WORKING`
  for 551 variants (currently set to 0x2000 = 8KB, should be 0x4000 = 16KB)
- `0xA021` appears as a high-frequency MOVX target unique to 551 — likely
  the cam-pulley hall sensor register; worth investigating in Ghidra

### New CRC32 fingerprints
```python
0x0808B2E5: ("551a_ABY",  "ABY/early-AAN fuel/ign working half, build 0x0274"),
0xBF11DB48: ("551aa_AAN", "AAN fuel/ign working half, build 0x0812 (lower 32KB)"),
0xF6E33043: ("551a_boost","ABY boost chip, 32KB mirrored, build 0x0202"),
```

### Ghidra RE priorities for 551 vs 3B hall sensor difference
1. Disassemble `0x0438` (INT0 handler, both ABY and AAN) — this is the
   crank trigger ISR, should show how RPM/position is computed
2. Disassemble `0x03CA` (INT1 handler) — cam trigger ISR
3. Compare with 3B INT0 @ `0x0339` and INT1 @ `0x0211`
4. The ABY 2-byte prologue (`E5 A9 A2 E6 92 D5`) at reset entry is likely
   a hardware detect — worth decoding to understand how ABY handles the
   distributor/cam-pulley variant selection at boot
5. Investigate `0xA021` (551 only) vs absence in 3B — likely the
   cam-pulley position latch register on the SAB80C535

---

## 8. ADU / RS2 — 551C Variant

### Identity

```
ID string @ WH 0x7F00:
  'To8A0907551C  2,2l R5 MOTR.RHV RS2D01PMC 0261203543 1267358668'

  ECU PN:       8A0907551C  (8A0 prefix = RS2 Avant)
  Engine:       2,2l R5
  Descriptor:   MOTR.RHV RS2  (explicit RS2 callout vs ABY's plain MOTR.RHV)
  Trigger:      D01PMC  (cam-referenced, same D01 as ABY, no 'HS' flag)
  Bosch ECU PN: 0261203543  (vs ABY's 0261203643 — suffix 43 vs 43)
  ROM PN:       1267358668
  Firmware build: 0x4533 (@ WH 0x3FFE)
  Cal tag:      0xA252 (@ WH 0x7FFE)
  Reset vector: LJMP 0x1329
```

| File | CRC32 | Notes |
|---|---|---|
| `adu_fuel-ign_551c.bin` (WH) | `0x4378E077` | Direct chip read |
| `adu_fuel-ign_551c.bin` (full 64KB) | `0x1529520A` | |
| `adu_boost_551c.bin` (32KB) | `0x4EE87833` | Boost chip direct read |

### Firmware Relationship to ABY 551B

ADU and ABY are **calibration-only siblings** — identical firmware, different cal tables:

| | ADU 551C | ABY 551B |
|---|---|---|
| Firmware build | `0x4533` | `0x0274` |
| Reset vector | LJMP 0x1329 | LJMP 0x1329 (identical) |
| WH diff vs ABY | 4172 bytes (12.7%) | — |
| Diff location | All clusters ≥ WH 0x2377 (calibration zone) | — |
| Code identity | Zero code diffs | — |
| Trigger | D01 (no HS flag) | HS D01 |

Build number differs (`0x4533` vs `0x0274`) despite zero code changes — Bosch used a
separate build sequence for the RS2 calibration branch.

The 'HS' flag present in ABY but absent in ADU is cosmetic (both use the same
physical trigger system). The RS2 descriptor field explicitly includes 'RS2'.

### RS2.xdf — Confirmed as Valid Bosch M2.3 XDF

`RS2.xdf` (165KB, 113 TABLE entries) is a TunerPro XDF for the ADU chip.
**Not a GM ECM file** — previous UrROM notes were incorrect on this point.

XDF address space: `BinSize=0x4000` (16KB working half).
XDF addresses are offset by `+0x8000` from WH base:
- XDF `0xAE17` → WH `0x2E17` (fuel map)
- XDF `0xB0AC` → WH `0x30AC` (ign map 1)

**Confirmed map addresses and decode formulas (verified against direct chip read):**

| Map | XDF addr | WH addr | Formula | Verified |
|---|---|---|---|---|
| Fuel RPM×LOAD | 0xAE17 | 0x2E17 | `X×0.0078125` (stoich=1.000) | ✓ |
| Ign PT map 1 | 0xB0AC | 0x30AC | `X×0.6491−8.2186 °BTDC` | ✓ 23.6° at PT |
| Ign PT map 2 | 0xB263 | 0x3263 | same formula | ✓ |
| Ign PT map 3 | 0xB387 | 0x3387 | same formula | ✓ |
| Ign PT map 4 | 0xB598 | 0x3598 | same formula | ✓ |
| Ign PT map 5 | 0xB6BC | 0x36BC | same formula | ✓ |
| Ign PT map 6 | 0xB80D | 0x380D | same formula | ✓ |
| Ign PT map 7 | 0xB931 | 0x3931 | same formula | ✓ |

These WH addresses are **identical to ABY 551B** — confirming shared memory layout.
Full XDF contains 113 TABLE entries including additional fuel tables (different sizes/axes),
smaller ign RPM×LOAD maps, lambda timing, and 1D correction tables.

### XDF Checksum Entry
`DataStart=0x08, DataEnd=0x3FFF, StoreAddr=0x06, CalcMethod=0x0`

This is the PRJmod/TunerPro checksum scheme for **tuned ROMs**.
Stock ADU chips do not carry a computed checksum at WH[0x0006] — that location
contains live code/data. The `To` prefix visible at WH[0x7F00] is the leading
ASCII characters of the ECU PN (`To8A0907551C...`), not a checksum value.

### Boost Chip
- Size: 32KB (27C256), build `0x0202` at `0x1FFE`
- Architecture: same as ABY/AAN — 8KB MCU code (0x0000–0x1FFF) + 24KB cal tables
- 4150/32768 bytes differ vs ABY boost (12.7%), 68 clusters
- Many clusters appear mirrored (same diff pattern at +0x4000 offset, confirming
  the boost chip is a doubled/mirrored 27C256 image)
