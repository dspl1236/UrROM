# UrROM — Stock ROM Collection

Reference ROM binaries for Bosch Motronic M2.3 / M2.3.2 ECUs fitted to
Audi 5-cylinder 20v Turbo and V8 engines (1986–1997).

All chips verified by direct EPROM read or from trusted ISMF (Internet Swap
Meet Forum) archives. CRC32 values and ROM ID strings are confirmed from binary
analysis. See `urrom/ecu_profiles.py` → `KNOWN_CRCS` for the full catalogue
including blank/erased chips not stored here.

---

## 5-Cylinder 20v Turbo — 3B / 7A / RR / AAN / ABY / ADU

### M2.3 — 447/857 series (dual chip: 32KB fuel/ign + 8KB boost MCU)

| File | Part Number | Application | ROM PN | CRC32 | Cal |
|------|-------------|-------------|--------|-------|-----|
| `3b_fuel-ign_404aa.bin` | 447907404AA | Audi 200 20vT, UrQ, S2 early | 1267356462 | `0x0AE3CACD` | ✓ REAL |
| `3b_fuel-ign_404a.bin` | 447907404A | Same applications, earlier rev | 1267356259 | `0xE66098C8` | ✓ REAL |
| `3b_boost_404aa.bin` | 447907404AA boost | 3B boost MCU (8KB exec code) | — | `0xF50660DA` | ✓ REAL |
| `s2_fuel-ign_404.bin` | 895907404 (Bosch 0261200484) | Audi S2 B3 Coupe (3B engine) | 1267356530 | `0x9245FA10` | ✓ REAL |
| `s2_boost_404.bin` | 895907404 boost | S2 B3 boost MCU (8KB exec code), cal tag 0xA027 | — | `0x604AB965` | ✓ REAL |
| `rr_fuel-ign_404b.bin` | 857907404B | UrQuattro RR S2 Coupe | 1267356261 | `0xFBE0A74A` | ✓ REAL |
| `rr_boost_404b.bin` | 857907404B boost | RR boost MCU (8KB exec code) | — | `0xEA8D46DF` | ✓ REAL |

**Notes — M2.3 (404 family):**
- Single 27C256 (32KB) fuel/ign chip per ECU, plus a separate 27C64 (8KB) boost chip
- The boost chip is an **executable 8051 MCU**, not a data-only ROM — it runs on a
  second independent processor on the MAP sub-board
- `404A` and `404AA` have 4,862 byte differences including both firmware code and
  calibration — they are different tunes on different hardware revisions
- 3B and RR share **identical firmware** (0 code differences); only calibration differs
  (~5,976 bytes) — RR runs leaner mid-range
- **S2 B3 (895907404, 0261200484)** fuel/ign chip is firmware-identical to the 3B 447907404AA
  (0 code bytes differ, 1,578 cal bytes differ). Ign map 1 is byte-identical across S2/3B/RR;
  ign maps 2–4 differ by ~90 cells (S2 ≈ +0.2° mean). Fuel: S2 mean raw 140.4, 3B 141.4, RR 135.5.
  The RR fuel chip has 4,753 bytes of *different firmware code* at 0x4893–0x5B23 vs both 3B and S2.
- **All three 8KB boost chips (3B / RR / S2) run identical MCU code.** Only calibration differs:
  six 8×16 tables at 0x18B4 / 0x1934 / 0x19B4 / 0x1A34 / 0x1AB4 / 0x1B34 (all three differ),
  plus 0x1C60–0x1DFC and 0x1E20–0x1EC8 (S2 differs from 3B=RR), plus the 6-byte tail at 0x1FFA.
  The 8×8 block at 0x1650 previously listed as "Boost Target" is identical on all three chips.
  Bosch pairs each boost chip with its fuel chip by consecutive cal tags (S2: 0xA027 / 0xA028).

---

### M2.3.2 — 551 series (dual chip: 64KB fuel/ign + 32KB boost MCU)

| File | Part Number | Application | Trigger | ROM PN | CRC32 (WH) | Cal |
|------|-------------|-------------|---------|--------|------------|-----|
| `aby_fuel-ign_551aa.bin` | 895907551B | ABY — Audi S2 Coupe | D01PMC cam | 1267358375 | `0xA98CB481` | ✓ REAL |
| `aby_boost_551b.bin` | 895907551B boost | ABY boost chip | — | — | `0xF6E33043` | ✓ REAL |
| `adu_fuel-ign_551c.bin` | 8A0907551C | ADU — RS2 Avant (RS2D01PMC) | RS2 D01 cam | 1267358668 | `0x4378E077` | ✓ REAL |
| `rs2_d02_fuel-ign_551b.bin` | 8A0907551B | RS2 D02 — early RS2 dist. | D02PMC dist | 1267358289 | `0xC5B30158` | ⚠ PARTIAL |
| `rs2_d02_boost_551b.bin` | 8A0907551B boost | RS2 D02 boost chip | — | — | `0x288CBFBC` | ✓ REAL |
| `aan_fuel-ign_551a.bin` | 4A0907551A | AAN — UrS4/UrS6 D02PMC dist. | D02PMC dist | — | `0xF7432BB5` | ✗ BLANK |
| `aan_fuel-ign_551aa.bin` | 4A0907551AA | AAN — UrS4/UrS6 D03PMC cam | D03PMC cam | — | `0xBF11DB48` | ✗ BLANK |
| `aan_boost_551aa.bin` | 4A0907551AA boost | AAN boost chip (32KB) | — | — | `0x16707F66` | ✓ REAL |

**Notes — M2.3.2 (551 family):**
- Fuel/ign chip is 27C512 (64KB), split-bank: lower 32KB = firmware, upper 32KB =
  calibration (working half). Not mirrored.
- Boost chip is 27C256 (32KB) executable 8051 MCU — same architecture as 3B boost
- **AAN chips are factory-blank** — Bosch shipped them unerased, calibration was
  burned during vehicle production. The blank 0x02 fill is correct and expected.
- `aan_fuel-ign_551a.bin`: upper half = blank D02PMC calibration (CRC `0xF7432BB5`),
  lower half = the 4A0907551A firmware (reset→0x117A). Earlier notes called the lower
  half a "PRJmod base ROM"; it is simply the chip's firmware half.
- `rs2_d02_fuel-ign_551b.bin` is partial — high-RPM calibration area blank
  (read error on original chip, not ECU damage)
- ADU `8A0907551C` (RS2D01PMC) and the S6 `4A0907551C` (D01PMC) share the same
  part-number suffix but are **completely different calibrations** (251/256 fuel cells differ)
- ABY/ADU calibration differences at 0x2D9E–0x2DAF follow a consistent 1.25× ratio —
  this is the MAP sensor range correction (RS2 uses 300 kPa vs S2's 250 kPa sensor)

---

## V8 — ABH 4.2L / PT 3.6L

### M2.3 V8 — 557 series (single chip: 65536B split-bank)

| File | Part Number | Application | Trigger | ROM PN | CRC32 (WH) | Cal |
|------|-------------|-------------|---------|--------|------------|-----|
| `abh_fuel-ign_557a.bin` | 4A0907557A | ABH 4.2L V8 280HP Audi 100/A6 | D02PMC dist | 1267357764 | `0x5B911FDE` | ✓ REAL |
| `s6v8_fuel-ign_557c.bin` | 4A0907557C | ABH 4.2L V8 290HP Audi S6 C4 | D02PMC dist | 1267355858 | `0x976CA7AB` | ✓ REAL |
| `v8q_fuel-ign_557e.bin` | 441907557E | ABH V8Q (Type 44 body) | D01PMC cam | 1267357441 | `0xDECDF5C4` | ✓ REAL |

### M2.3 V8 — 404 series (single chip: 32KB)

| File | Part Number | Application | ROM PN | CRC32 | Cal |
|------|-------------|-------------|--------|-------|-----|
| `pt_fuel-ign_404h.bin` | 441907404H | PT V8Q 3.6L manual — US market | 1267356322 | `0x594F97FB` | ✓ REAL |
| `pt_fuel-ign_abt_tune.bin` | 441907404 | PT V8Q 3.6L manual — ABT tune | 1267355684 | `0x750A9EB0` | ✓ REAL |

**Notes — M2.3 V8 (557 / 404V8 family):**
- Confirmed **Bosch Motronic M2.3** — same 8051 CPU family as 5-cyl 404/551x.
  NOT M3 (Intel 80C196) or M5. Vector table, opcode ratios, interrupt structure
  all confirmed 8051.
- The 557 series uses a **split-bank 27C512** (65536B):
  - Lower 32KB (0x0000–0x7FFF) = firmware code only (10,321+ code bytes)
  - Upper 32KB (0x8000–0xFFFF) = calibration data (3A/3F header format)
  - The 5-cyl 551x 27C512 uses the same split-bank layout (firmware low, calibration high)
- **Dual distributor architecture**: INT0 → bank 1 (cyl 1–4), INT1 → bank 2 (cyl 5–8)
  ABH 557A and S6 557C share an **identical INT0 handler** at 0x0431
- The PT 404V8 is a single 32KB chip (27C256), same format as 3B/RR 404 family
- **The ABT tune** (441907404, no H suffix) is NOT a modified PT stock chip —
  it uses a different Bosch PN (0261200183 vs 0261200198), different firmware
  (reset→0x11EF vs 0x128B), and different chip hardware. ABT sourced their own EPROM.
  It has significantly more mid-range advance (+5–18°) than PT stock but conservative
  top-end (−17–29°). This is the sought-after chip US V8Q owners swapped into auto
  ABH 4.2L cars (also requiring the PT 3.6L intake manifold).
- V8Q 441907557E uses D01PMC (cam trigger), unlike the D02PMC (distributor) of
  557A/557C — and comes from the Type 44 V8Q body (441 prefix) not 4A/100/A6

---

## Calibration Status Key

| Symbol | Meaning |
|--------|---------|
| ✓ REAL | Direct chip read with confirmed calibration data |
| ⚠ PARTIAL | Read is genuine but some areas blank (read error or factory partial) |
| ✗ BLANK | Factory-erased chip — firmware present, calibration all 0x02 |

**All blank chips are intentionally blank.** Bosch manufactured EPROMs without
pre-loading calibration; calibration was burned separately during vehicle assembly.
These chips are documented here because the firmware code is real and useful for
reverse engineering the trigger/timing algorithm.

---

## File Format Notes

**32KB files (3B/RR/PT/ABT):** Direct 27C256 EPROM read. Byte 0 = address 0x0000.
Firmware and calibration share the same address space.

**64KB files (ABY/ADU/RS2/AAN):** 27C512 EPROM read, stored as full 65536B image,
**split-bank**: lower 32KB = 8051 firmware (starts with the reset LJMP), upper 32KB =
calibration (UrROM's "working half", ~75 % 0x02 filler around the maps). The halves
are never mirrors — see `docs/551_calibration_descriptors_RE.md`. Earlier notes calling
the lower half a "mirror" or "PRJmod base ROM" were wrong; the lower half is the
firmware the calibration runs on.

**64KB V8 files (ABH/S6/V8Q):** Split-bank 27C512. Lower 32KB = firmware code.
Upper 32KB = calibration. Do not mirror the halves — they contain different data.

---

## Map Addresses — 5-Cyl 551 Family (confirmed)

Addresses are working-half (WH) offsets, i.e. offsets within the upper 32KB of the
64KB file.

| Map | WH Address | Dimensions | Decode |
|-----|-----------|------------|--------|
| Fuel (P/T primary) | 0x2E17 | 16×16 | raw; 128 = stoich ref |
| Ign Map 1 (primary) | 0x30AC | 16×16 | raw × 0.6491 − 8.22 = °BTDC |
| Ign Map 2 | 0x3263 | 16×16 | same decode |
| Ign Map 3 | 0x3387 | 16×16 | same decode |
| Ign Map 4 | 0x3598 | 16×16 | same decode |
| Ign Map 5 | 0x36BC | 16×16 | same decode |
| Ign Map 6 | 0x380D | 16×16 | same decode |
| Ign Map 7 | 0x3931 | 16×16 | same decode |
| Pre-cal scalars | 0x2D00 | various | 3A/3F headers |
| End-of-cal RPM table | 0x3FE0 | 2×16 | raw × 40 = RPM |

ABY (551B) uses the same layout **minus 4 bytes from WH 0x3026** (ign 0x30A8 … 0x392D).
RS2 D02 (8A0907551B) and AAN 551AA use the other family: fuel 0x0E13 / 0x0DEA, ign
0x10A8… / 0x106D… — decoded from each chip's firmware descriptor tables
(`python -m urrom.cli maps <rom>`; see docs/551_calibration_descriptors_RE.md).

## Map Addresses — 3B/RR 404 Family (confirmed)

Addresses are direct ROM offsets in the 32KB chip.

| Map | Address | Dimensions |
|-----|---------|------------|
| Fuel Map 1 (primary) | 0x6A8E | 16×16 |
| Fuel Map 2 (mirror) | 0x6C1C | 16×16 |
| Fuel Map 3 (mirror) | 0x6D74 | 16×16 |
| Fuel Map 4 (WOT enrichment) | 0x6E98 | 16×16 |
| Ign Map 1 (identical 3B/RR/S2) | 0x7076 | 16×16 |
| Ign Map 2 | 0x71F8 | 16×16 |
| Ign Map 3 (S2 differs in 251/256 cells) | 0x731C | 16×16 |
| Ign Map 4 | 0x7440 | 16×16 |
| Ign Map 5 | 0x7667 | 16×16 |
| Ign Map 6 | 0x77CF | 16×16 |
| Ign Map 7 | 0x7937 | 16×16 |

All eleven confirmed from the firmware's own descriptor tables (2026-09); axes
RPM 600–7200, load 14–190. `python -m urrom.cli maps <chip>` lists all 126 tables.

## Map Addresses — V8 557 / 404V8 (preliminary, unconfirmed)

V8 map RE is in progress. The 3A/3F header format is confirmed identical to 5-cyl.
Ign advance decode formula confirmed (raw × 0.6491 − 8.22 = °BTDC, values 10–44°).

- 557 calibration begins at upper-half WH 0x2D00 (same offset as 5-cyl 551x)
- Multiple ign map blocks confirmed at upper 0x33D0–0x3670 range
- PT 404V8 ign maps at approximately 0x6A4E, 0x6E92, 0x6ED4

---

## Generation Reference

```
Bosch Motronic M2.3     — Audi 5-cyl 20vT 3B/RR (1988–1991), V8 PT/ABH (1988–1994)
  CPU: Siemens/Bosch 8051 derivative (Intel MCS-51 compatible)
  ECU: 447907404 / 857907404 / 441907404 (5-cyl & V8 3.6)
       441907557 / 4A0907557 (V8 4.2)

Bosch Motronic M2.3.2   — Audi 5-cyl 20vT AAN/ABY/ADU (1991–1995)
  CPU: Siemens SAB80C535 (Intel 8051 derivative, same instruction set)
  ECU: 4A0907551 / 895907551 / 8A0907551

NOT Motronic M3.x       — Uses Intel 80C196 (16-bit CPU), completely different
NOT Motronic M5.x       — Different architecture entirely
```

All M2.3 and M2.3.2 ECUs confirmed to use the **same 8051 instruction set**,
the **same 3A/3F calibration map header format**, and the **same ignition advance
decode formula** (raw × 0.6491 − 8.22 = °BTDC). UrROM tooling is applicable to
all variants in this collection.

---

*UrROM project — https://github.com/dspl1236/UrROM*
