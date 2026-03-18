# Bosch M2.3 — 3B / RR ECU Reverse Engineering Notes
## 447907404 (AA/B variants) Hardware & Firmware Documentation

Session date: 2026-03-18  
Documented from: board photographs, physical chip reads, binary analysis

---

## 1. HARDWARE — 3B ECU (447907404AA)

### Board Identification
| Field | Value |
|---|---|
| ECU assembly PN | 447907404 AA |
| PCB part number | 1268322153-1 |
| PCB assembly number | 1268329153-1 |
| Board variant code | 3 F0 (silkscreen, top-left corner) |
| Connector | 55-pin edge connector (single) |
| Production date | Week 06–20 / 1990 (chip date codes) |

### IC Inventory
| Reference | Marking | Function | Package |
|---|---|---|---|
| CPU | SIEMENS BD26422 / B57629 / ©INTEL 90 / 9006 | Main 8051-core MCU | DIP-40 |
| Companion QFP | SIEMENS BD26401 / B57828 / ©INTEL 90 / 9020 | Address decode / peripheral | QFP-44 |
| Output ASIC | TA13255A / 100163 / N9002 / B57725 | Injector/ign final stage driver | QFP-52 |
| Injector driver | ©Bosch 30003 | Peak-and-hold injector current | DIP-8 |
| Ign power stage | ©Bosch 30015 / 1090 | Ignition coil driver | DIP-16 |
| EPROM1 (fuel/ign) | Intel 27C256 / B57741L / U0076081 / ©1984 | Fuel & ignition ROM | DIP-28 |
| EPROM2 (boost) | Unknown manufacturer (partially obscured) | Boost controller ROM | DIP-28 |
| Analog IC (×2) | B57794 / H006 | Signal conditioning | SOP-8 |
| Analog IC | B57530 / H001 | Comparator / buffer | SOP-8 |
| MAP sensor | Bosch 0 273 003 204 / 200kPa / Made in Germany | Manifold pressure | Sub-board |

### Notes
- CPU is Siemens (Intel-licensed), same silicon family as AAN SAB80C535
- BD26422 = Bosch internal PN for custom-labeled Siemens 8051 derivative
- ©INTEL 90 confirms Intel architecture license, 1990
- EPROM1 retention: white Bosch overlay label
- EPROM2 retention: factory RTV silicone blob + wire-form spring clip
- Both EPROMs are stock, unmodified, no sockets installed
- No hardware modifications present (stock MAP, R660 intact, no board wire)
- Pink ceramic component at MAP connector = decoupling capacitor (not an IC)

---

## 2. FIRMWARE — FUEL/IGN CHIP COMPARISON

### Identity Strings (embedded in ROM at 0x7F00)
| Field | 3B (447907404 AA) | RR (857907404 B) |
|---|---|---|
| ECU assembly PN | 447907404 AA | 857907404 B |
| Platform prefix | 447 = Type 44 / C3 (Audi 200) | 857 = Type 85 / B2 (UrQuattro) |
| Function | MOTOR PMC | MOTOR PMC |
| Bosch ECU PN | 0261200451 | 0261200453 |
| ROM PN | 1267356462 | 1267356261 |
| Firmware build | 0xF004 (@ 0x3FFE) | 0xF004 (@ 0x3FFE) |
| Calibration tag | 0x029B (@ 0x7FFE) | 0x0253 (@ 0x7FFE) |

### File Information
| Field | 3B fuel/ign | RR fuel/ign |
|---|---|---|
| Filename | 3b_fuel-ign_404a-aa.bin | rr_boost_404b.bin ⚠️ |
| Size | 32768 bytes (32KB) | 32768 bytes (32KB) |
| MD5 | 293d29aab3791d44ba9eb431f103fe1e | 058f9acf64fe0d697d8bd8d4609838e8 |

> ⚠️ **FILENAME WARNING**: The uploaded files `rr_fuel-ign_404b.bin` and
> `rr_boost_404b.bin` have **swapped names**. Sizes reveal the mislabeling:
> - `rr_fuel-ign_404b.bin` = 8KB → this is actually the **boost chip**
> - `rr_boost_404b.bin` = 32KB + LJMP reset vector → this is actually the **fuel/ign chip**

### Reset Vector / Interrupt Table (fuel/ign chip — both variants)
- Reset: `LJMP 0x0F99`
- All 13 interrupt vectors populated: `LJMP 0x20xx` pattern
- SAB80C535 extended vector table confirmed (13 vectors, not just 5)

### Code Identity
- **Firmware build 0xF004 is identical** in both 3B and RR fuel chips
- Same reset vector, same interrupt table layout
- Shared codebase: 81.8% of bytes identical between 3B and RR
- Large diff block at 0x4893–0x5B23 (4753 bytes) — likely distributor-specific
  ignition/spark sequencing code differs between 200 20vT and UrQuattro

### Map Differences (3B AA vs RR B)
| Map | Address | Diffs | Notes |
|---|---|---|---|
| Fuel map 1 | 0x6A6A | 176/256 (69%) | RR leaner across mid-range |
| Fuel map 2 | 0x6BF8 | 176/256 (69%) | Same pattern as map 1 |
| Fuel map 3 | 0x6D50 | 176/256 (69%) | Same pattern |
| Fuel map 4 | 0x6E74 | 176/256 (69%) | Same pattern |
| Ign map 1 | 0x7052 | **0/256 (0%)** | **Identical** |
| Ign map 2 | 0x7643 | 101/256 (39%) | RR slightly different |
| Ign map 3 | 0x77AB | 75/256 (29%) | |
| Ign map 4 | 0x7913 | 56/256 (22%) | |

Key observation: **Ign map 1 is byte-for-byte identical** across both variants.
This is likely the base no-knock timing map — same spark curve used as the
baseline before knock correction is applied.

The fuel maps differ significantly: RR runs leaner values in the mid-load
operating zone (rows 4–11, columns 4–15 = the cruise/moderate load region).
RR raw fuel values are 5–10 counts lower than 3B in this zone.

### Scalar Differences
| Address | 3B value | RR value | Decoded |
|---|---|---|---|
| 0x64A8 | 0xC8 | 0x32 | 3B=200×40=8000 RPM / RR=50×40=2000 RPM ¹ |
| 0x657F | 0xC8 | 0x32 | Same pair |

¹ These scalars may not be RPM limits — context suggests they may be
load/throttle thresholds. The 0x32 vs 0xC8 difference is suspicious
and warrants disassembly of the surrounding code before drawing conclusions.

---

## 3. FIRMWARE — BOOST CHIP COMPARISON

### Architecture (Critical Finding)
The boost chip is **not a data-only ROM**. It contains executable 8051 code
running on a **second independent MCU** on the MAP sub-board.

Evidence:
- Starts with `C2 AF` = `CLR EA` (disable interrupts) — valid 8051 startup code
- No LJMP reset vector; inline code at vector addresses (compact startup style)
- Interrupt handlers for Timer0, Timer1, INT0, INT1, Serial are all populated
- Has its own independent build number separate from fuel chip
- Contains calibration tables (boost targets, WGDC maps) in upper address space

This is **Architecture B** (dual-MCU), not architecture A (banked code memory).

### File Information
| Field | 3B boost | RR boost |
|---|---|---|
| Filename | 3b_boost_404a-aa.bin | rr_fuel-ign_404b.bin ⚠️ (mislabeled) |
| Size | 8192 bytes (8KB) | 8192 bytes (8KB) |
| MD5 | 63290a8403c51a82f2538a1b419943cf | 8955d47da7221d5a886568d81ca0c9c6 |
| Build @ 0x1FFE | 0x0254 | 0x0255 |
| Byte similarity | — | 92.1% identical |

### Boost Chip Diff Summary
| Region | Size | Description |
|---|---|---|
| 0x17ED–0x17EE | 2 bytes | Minor scalar tweak |
| 0x18B4–0x1BCC | 793 bytes | **Boost target / WGDC calibration tables** |
| 0x1FFA–0x1FFF | 6 bytes | End-of-ROM tag / checksum bytes |

The primary calibration difference is in the 0x1A40–0x1B40 region (boost
target tables). RR values are **notably higher** than 3B across the load range:

Sample comparison (0x1A60, partial row):
```
3B:  44  50  56  61   ...  35  40  44  49
RR:  38  61  78  96   ...  37  40  47  52
```

The RR chip shows larger spread and higher peak values in the boost target
region, consistent with the UrQuattro running more aggressive boost mapping
than the 200 20vT application.

### IPC (Inter-Processor Communication)
The fuel/ign CPU communicates with the boost MCU via shared external memory:
- `0xA040` — appears 8+ times as MOVX target in fuel chip
- `0xA080` — appears 8+ times as MOVX target in fuel chip

These are the inter-chip registers. Almost certainly:
- Fuel CPU reads computed boost request / current MAP from boost MCU
- Fuel CPU writes current RPM / load to boost MCU for scheduling

---

## 4. ARCHITECTURE DIAGRAM

```
┌─────────────────────────────────┐   ┌──────────────────────────────────┐
│  FUEL/IGN CPU (main board)      │   │  BOOST MCU (MAP sub-board)       │
│  Siemens BD26422 (DIP-40)       │   │  Unknown MCU (8KB ROM)           │
│  Intel 8051 core, ©INTEL 90     │   │  8051 architecture               │
│                                 │   │                                  │
│  EPROM1: 32KB Intel 27C256      │   │  EPROM2: 8KB (mfr unknown)       │
│  Build: 0xF004                  │   │  Build: 0x0254 (3B) / 0x0255 (RR)│
│                                 │   │                                  │
│  Fuel enrichment tables         │   │  MAP sensor ADC                  │
│  Ignition advance tables        │   │  Boost target tables             │
│  Distributor sequencing         │   │  WGDC calibration                │
│                                 │   │                                  │
│  MOVX → 0xA040 ◄────────────────┼───┼──► Shared IPC register          │
│  MOVX → 0xA080 ◄────────────────┼───┼──► Shared IPC register          │
└─────────────────────────────────┘   └──────────────────────────────────┘
         │                                          │
         ▼                                          ▼
  Injector drivers                         Wastegate solenoid
  Ignition coil (distributor)              MAP sensor (200kPa)
  TA13255A output ASIC
  Bosch 30003 (injector)
  Bosch 30015 (ignition)
```

---

## 5. URROM IMPLICATIONS

### VARIANT_404 Entry Updates Required
```python
VARIANT_404 = ROMVariant(
    name         = "3B — 200 20vT / UrQ RR",
    software_id  = "404",
    engine_codes = ["3B", "RR"],
    ecu_pns      = ["447907404AA", "857907404B"],   # C3 and B2 platform PNs
    bosch_pns    = ["0261200451", "0261200453"],
    rom_pns      = ["1267356462", "1267356261"],
    firmware_build = 0xF004,                         # shared build
    cal_tags     = {
        "3B_AA": 0x029B,
        "RR_B":  0x0253,
    },
    dual_eprom   = True,
    boost_chip_executable = True,                    # NOT data-only
    ipc_registers = [0xA040, 0xA080],
    ...
)
```

### Map Addresses (MapFinder-confirmed, verified against real ROMs)
```
Fuel maps:  0x6A6A, 0x6BF8, 0x6D50, 0x6E74  (16×16 each)
Ign maps:   0x7052, 0x7643, 0x77AB, 0x7913  (16×16 each)
ID string:  0x7F01
Build word: 0x3FFE
Cal tag:    0x7FFE
```

### Boost Chip RE — Next Steps
1. Load `3b_boost_404a-aa.bin` into Ghidra as flat 8051, 8KB, base 0x0000
2. Entry point: 0x0000 (inline startup, no redirect)
3. Focus on 0x0000–0x00FF (interrupt vectors / init)
4. Focus on 0x18B4–0x1BCC (calibration tables, confirmed diff vs RR)
5. Identify what is written to the IPC registers and map to fuel chip reads
6. Confirm: does boost chip write MAP sensor reading directly, or computed target?

### AAN vs 3B Architecture Difference
The AAN boost chip was described in prior sessions as having MAP data on the
boost chip. The 3B boost chip confirmed here is **executable code** — a full
MCU firmware, not a data table. These may be genuinely different architectures
(AAN data-only vs 3B dual-MCU), or prior AAN documentation may be incorrect.
**Recommend reading AAN boost chip directly to confirm** before drawing
architectural conclusions for UrROM's AAN boost chip handling.

---

## 6. COMMUNITY CROSS-REFERENCE

| PN | Description |
|---|---|
| 447907404 AA | 3B ECU, Audi 200 20vT, Type 44 body |
| 857907404 B | RR ECU, UrQuattro, Type 85 body |
| 0261200451 | Bosch ECU assembly (3B) |
| 0261200453 | Bosch ECU assembly (RR) |
| 1267356462 | Bosch ROM (3B fuel/ign chip) = B57741L |
| 1267356261 | Bosch ROM (RR fuel/ign chip) |
| 0 273 003 204 | Bosch MAP sensor, 200kPa, stock |
