# Bosch Motronic M2.3 / M2.3.2 — Complete Hardware Reference

*Compiled from: Official Bosch schematics (Robert Bosch GmbH Stuttgart), PRJ firmware
source and community RE (prj/m232 GitHub), vwnut8392 patcher XDF and S2Forum documentation,
direct chip reads and board photography (2026-03 RE session), RS2 551B XDF (Matt @ S&M Autosport).*

---

## 1. Official Bosch Schematics

Two factory drawings define the M2.3.2 hardware completely:

| Sheet | Drawing number | Title | Date |
|---|---|---|---|
| Page 1 | **Y261 C20 232-5V** | M2.3.2/2.4.1 **Zusatzplatte** (Auxiliary board / boost board) | 25.7.90 |
| Page 2 | **Y261 C27 020**   | M2.3.2 **Grundplatte** (Main board / motor board) | 18.4.91 |

Reference number (Verw): `1 268 311 200-2` (Page 1) / `1 268 313 198-4` (Page 2).
Origin: Robert Bosch GmbH, Stuttgart. Department K3/EEC2.

---

## 2. Two-Board Architecture

The M2.3.2 ECU physically consists of two stacked PCBs connected by a pin bridge:

```
┌─────────────────────────────────────────────────────────┐
│  ZUSATZPLATTE  (Y261 C20 232-5V)  — Boost / Aux board   │
│                                                          │
│  S700  Boost CPU    SAB80C535 (or BD26422 variant)      │
│  S701  Fuel/Ign EPROM   DIP-28  27C256 or 27C512         │
│  S702  Address decoder / glue logic   DIP-40            │
│  S703  Boost EPROM      DIP-28  27C64 (8KB) or 27C256   │
│  N700  Data bus latch   8-bit   (A8–A15 address latch)  │
│  N701  Data bus latch   8-bit                            │
│  S712  Oscillator / crystal circuit                      │
│  MAP sensor (S100 area)   Bosch 200kPa or upgrade        │
└─────────────────────┬───────────────────────────────────┘
                      │  pin bridge / edge connector
┌─────────────────────┴───────────────────────────────────┐
│  GRUNDPLATTE  (Y261 C27 020)  — Main / Motor board      │
│                                                          │
│  S250  Motor CPU    SAB80C535 (DIP-40)                  │
│  S251  External SRAM    32KB                             │
│  S252  Address decode logic                              │
│  S253  Signal conditioning (×4 stages)                  │
│  S254  Comparator stages                                 │
│  S255  Output buffer / latch                             │
│  S256  Lambda (O2S) signal amplifier                    │
│  S300  Power supply regulation / reset                  │
│  S400  Input comparators (A1–A6, injector inputs)       │
│  S450  Input comparators (second bank)                  │
│  S500  Output driver bank 1                              │
│  S501  Output driver bank 2 (injector final stages)     │
│  S200  MAP sensor interface (internal pressure ref)     │
│  S201  Oscillator reference                              │
│  S230  Knock sensor conditioning                        │
│  S310  Lambda amplifier / O2S circuit                   │
│  S460  Idle speed control / stepper driver              │
└─────────────────────────────────────────────────────────┘
```

---

## 3. IC Reference — Zusatzplatte (Page 1)

### S700 — Boost CPU
**Siemens SAB80C535** (or Bosch custom variant BD26422)

- DIP-40 package
- Intel MCS-51 compatible, ROM-less (EA tied low — all code from external EPROM)
- 256 bytes internal RAM, external 64KB program/data space
- 8-bit ADC, 8-channel (Port 6 / AIN0–AIN7)
- Crystal: XTAL1/XTAL2 pins to S712 oscillator circuit
- Address latch: ALE → N700 / N701 (separates A0–A7 from multiplexed P0 bus)
- Chip select: CS→S701 (fuel EPROM), CS2/CE2→S703 (boost EPROM)

### S701 — Fuel/Ignition EPROM
- DIP-28, 27C256 (32KB) — address lines A0–A14 from CPU and N701 latch
- Bosch part numbers confirmed from chip reads:
  - `B57741L / U0076081` (Intel 27C256, ©1984) — 3B/RR variant
  - Labels vary: B57741L, various Intel/AMD/ST silicon, Bosch overlay

### S703 — Boost EPROM
- DIP-28, **27C64 (8KB)** on early boards (3B, 551A) — address lines A0–A12
- DIP-28, **27C256 (32KB)** on later boards (551AA, 551B, 551C) — A0–A14
- Executes independently on S700 CPU — **not memory-mapped data**, executable 8051 code
- Spring wire retainer clip + factory RTV silicone (vibration retention)

### N700 / N701 — Address Latches
- 8-bit latches, triggered by ALE from S700 CPU
- Separate the lower address byte (A0–A7) from the multiplexed P0 data bus
- Drive A8–A15 to both EPROMs (16-bit address space for 64KB program memory)

### S702 — Address Decoder / Glue Logic
- DIP-40, handles chip-select generation for S701/S703
- Decodes upper address bits to /CE (chip enable) signals
- S701 selected for addresses 0x0000–0x7FFF, S703 for 0x8000–0x9FFF (8KB) or higher

### S712 — Crystal Oscillator
- 12 MHz crystal — standard SAB80C535 clock
- Machine cycle = 12 clock cycles → 1 MIPS instruction throughput

### S100 area — MAP Sensor
- On-board pressure transducer, accessed via vacuum port on ECU housing
- Stock: **Bosch 0 273 003 204, 200 kPa** — "Made in Germany"
- Analog output → boost board via board traces → CPU ADC

---

## 4. IC Reference — Grundplatte (Page 2)

### S250 — Motor CPU
**Siemens SAB80C535** (confirmed from schematic and chip reads)

Pin assignments from schematic (Y261 C27 020):

| Pin | SAB80C535 signal | Connected to | Function |
|-----|-----------------|--------------|----------|
| 10  | RESET | S300 | Power-on reset circuit |
| 11  | REF+ | C274/C283 | ADC positive reference |
| 12  | REF- | GND | ADC negative reference |
| 14  | AN6 | — | ADC ch6 (grounded/unused in stock) |
| 15  | AN5 | — | ADC ch5 → **MAP signal when SD modded** |
| 16  | AN4 | — | ADC ch4 → ECT (coolant temp NTC) |
| 17  | AN3 | — | ADC ch3 → IAT (intake air temp) |
| 18  | AN2 | — | ADC ch2 |
| 19  | AN7 | — | ADC ch7 |
| 21  | (INT0)/P3.2 | S253 | External interrupt 0 (crank trigger) |
| 22  | (TXO)/P3.1 | B254 → 7,TXO | KWP1281 serial TX → ECU pin 7 |
| 24  | P5.4 | 135 → 34,SFEZ | — |
| 25  | P3.5 | 137 → 15,SSO | — |
| 26  | P3.5 | 136 → 35,SLD | — |
| 29  | P1.7 | S501/6 | — |
| 30  | P1.6 | S501/3 | — |
| 31  | P1.5 | S501/2 | — |
| 32  | (INT2)/P1.4 | B255 → 46,ZDG1 | INT2 / zero-degree gap |
| 33  | P1.3 | C280/C279/C278 | — |
| 39  | XTAL2 | R246, XTAL2 osc | Crystal oscillator |
| 40  | XTAL1 | Q250, R286, C259 | Crystal oscillator |
| 41  | P2.0/AN1 | C249 → 19,PHA | Phase angle / hall sensor |
| 42  | P2.2 | — | ZDG1 path |
| 43  | P2.3/(INT0)/P3.2 | 43 | Interrupt |
| 45  | P2.4 | 45,PSEN | Program store enable |
| 46  | P2.5 | — | — |
| 49  | PSEN | 49 | EPROM /OE drive |
| 50  | ALE | R247 → 197 | Address latch enable → N latch on boost board |
| 51  | EA | C281,C250 | Tied low = external code only |
| 52  | P0.0 | 53 | Data bus |
| 67  | P5.0 | 67 → 19,PHA | Hall sensor / phase |
| 68  | VCC | C274,C283 | +5V supply |

**ECU connector pin routing (from schematic cross-reference table):**

| Ins.MF (schematic) | Verbinder (ECU pin) | Signal |
|---|---|---|
| 260 | 2 | — |
| 261 | 22 | — |
| 262 | 36 | — |
| 263 | 39 | — |
| 264 | 40 | — |
| 265 | 45 | — |
| 266 | 50 | — |
| 267 | 52 | — |
| 268 | 53 | — |

**Key CPU signals at ECU connector:**
- AN3 → 180 → 18 `ADK` (air flow / MAF signal)
- AN4 → 181 → 17 `TAN` (intake air temperature)
- AN5 → 182 → 16 `TMOT` (coolant temperature) ← **also SD MAP input**
- RXD/P3.0 → B253 → 4 `RXO` (KWP1281 RX)
- TXD/P3.1 → B254 → 7 `TXO` (KWP1281 TX)
- P3.2/(INT0) → 43 (crank trigger / INT0)
- (INT2)/P1.4 → B255 → 46 `ZDG1`
- P5.0 → 19 `PHA` (phase / cam signal)
- AN6 → 14 (unused in stock — pin 46 area)

### S251 — External SRAM
- DIP-28, connected to S250 data bus + address bus
- Provides expanded RAM beyond the 256 bytes internal to SAB80C535
- Address decoded by S252

### S252 — Address Decoder
- Generates /CS for S251 RAM and other peripherals

### S253 — Signal Conditioning (×4)
- Multiple comparator/amplifier stages
- Processes crank/cam trigger signals before CPU interrupt pins

### S256 — Lambda (O2S) Amplifier
- Wideband lambda signal conditioning
- Output → CPU ADC

### S300 — Power Supply / Reset
- Voltage regulation, reset threshold detection
- ON/OFF control from ignition line

### S400 / S450 — Input Comparators
- Banks of comparators for injector trigger inputs (A1–A6)
- Condition sensor signals to logic levels for CPU ports P0–P3
- S400: comparators E1–E6, GND/AGND reference, R400–R404
- S450: second bank with D452–D454 clamping diodes

### S500 / S501 — Output Drivers
- Power transistor banks for injector and ignition outputs
- S501 pins labeled S501/2, S501/3, S501/6 — tied to P1.5–P1.7 on CPU
- S500 provides ZL/ZH logic levels from E0–E2

### S200 — MAP Sensor Interface
- On-board absolute pressure transducer circuit
- S201 comparator, R200–R219 resistor network, trimmer MP207/MP209
- COMP output → CPU
- VREF generated internally for ADC reference
- T200 NTC temperature compensation for MAP accuracy
- This is the stock 200 kPa circuit on the Grundplatte

### S230 — Knock Sensor Conditioning
- Piezo knock sensor amplifier and band-pass filter
- Output used for ignition retard calculation

---

## 5. ADC Channel Map — Confirmed from Schematic + Community RE

### Motor CPU (S250 / Grundplatte)

| ADC Ch | SAB80C535 pin | ECU pin | Signal | Source |
|--------|--------------|---------|--------|--------|
| AN0 | P6.0 | — | TPS (Throttle Position Sensor) | vwnut8392 / S2Forum |
| AN1 | P6.1 | 18 `ADK` | MAF (hot-wire mass airflow) | Schematic trace + RE |
| AN2 | P6.2 | — | Battery voltage (UBAT) | vwnut8392 |
| AN3 | P6.3 | 17 `TAN` | IAT (Intake Air Temp, ECU pin 44) | Schematic |
| AN4 | P6.4 | 16 `TMOT` | ECT (Coolant Temp NTC, ECU pin 45) | Schematic + NTC table |
| **AN5** | **P6.5** | **—** | **Automatic-trans (stock) → MAP SD input** | **vwnut8392 confirmed** |
| AN6 | P6.6 | 46 | Map-switch (early) → WB logging (prjmod) | vwnut8392 |
| AN7 | P6.7 | — | Lambda / O2S (possibly S600 variant) | Preliminary |

**AN5 confirmation:** vwnut8392 states explicitly: *"AN5 = not used, automatic related. (Used for MAP sensor input.)"* — When R660 is removed and the boost→motor inter-board wire is installed, the MAP sensor analog output arrives at AN5 on the motor chip.

### Boost CPU (S700 / Zusatzplatte)

| ADC Ch | Function | Status |
|--------|----------|--------|
| AN0–AN1 | — | Not documented |
| AN2 | Tied to GND by CPU hardware | Cannot use without lifting boost CPU |
| AN3 | TPS → RAM_6C | Confirmed |
| AN4 | IAT → RAM_69 | Confirmed |
| AN5 | ECT → RAM_6A | Confirmed |
| AN6 | Tied to GND by CPU hardware | Cannot use without lifting boost CPU |
| AN7 | Altitude sensor → RAM_67 | Confirmed |

**Important:** AN2 and AN6 on the boost CPU are grounded by the CPU itself. They cannot be repurposed without physically lifting or removing the boost processor.

---

## 6. M2.3.2 Checksum Algorithm

### Stock Bosch chips (no computed checksum)
Stock EPROMs use Bosch ASCII identification strings at the end of the working half.
There is **no computed checksum** in unmodified stock M2.3 chips.

The string at `WH[0x7F00–0x7F40]` takes this format:
```
8A0907551C  2,2l R5 MOTR.RHV RS2D01PMC 0261203543 1267358668
```

Fields: `ECU_PN  displacement R-cylinders type descriptor trigger Bosch_ECU_PN ROM_PN`

The two bytes before the ASCII string (e.g. `DA 6D` for 3B) are a Bosch internal
verification prefix — not a software checksum.

### PRJmod chips (M232csum.dll algorithm)
Confirmed from PRJ's GitHub source (`src/M232csum/M232csum/TPM232Plugin.cpp`):

**32KB working half (boost chip or flat fuel chip):**
```
sum = 0
for i in range(0, 0x3FFA, 2):          # bytes 0x0000–0x3FF9
    even_acc += wh[i]
    odd_acc  += wh[i+1]
total = (even_acc + odd_acc) & 0xFFFF
# Include existing build-number bytes in total:
total += wh[0x3FFE] + wh[0x3FFF] + 0x01FE
correction = total & 0xFFFF
wh[0x3FFA] = (correction >> 8) & 0xFF    # stored high byte
wh[0x3FFB] = correction & 0xFF           # stored low byte
```

**64KB doubled file (fuel/ign chip):**
- Sum covers bytes `0x0000–0xFEFF`
- Stored at `0xFF00` (high) and `0xFF01` (low)
- These addresses land inside the Bosch ASCII ID string for stock chips
  (e.g., `To8A0907551C...`) — confirming that the `To` prefix bytes
  are actually the checksum correction bytes for prjmod-flashed fuel chips

---

## 7. Hardware Modification Reference

### 7.1 MPXH6400A MAP Sensor (Speed Density mode)

Required for prjmod speed-density firmware. All four steps must be completed:

1. Remove solder from via under `S900`/`C660` label on **boost board**
2. Remove solder from via to right of D232 (towards D235) on **motor board**
3. Solder wire through both vias — this connects boost board MAP signal to motor board AN5
4. Remove resistor **R660** from motor board

The MAP sensor output signal now routes: `MAP → boost board → inter-board wire → AN5 on motor CPU`.

Boost range with MPXH6400A (400 kPa): up to ~2.9 bar absolute.

### 7.2 AAN → RS2 Conversion

The AAN and RS2 ECUs use **identical PCB hardware**. Only three things differ:

| Item | AAN stock | RS2 equivalent |
|---|---|---|
| R201 resistor | ~6.15 kΩ | **5.6 kΩ ±1%, 1206 SMD** |
| MAP sensor | 200 kPa Bosch | 300 kPa (MPX4300 or equivalent) |
| Chip set | 551A/551AA fuel+boost | 551B/551C fuel+boost |

R201 value confirmed by PRJ directly on S2Forum (2013): *"The resistor is 5.6k 1%, 1206"*.

### 7.3 MAP Sensor Options

| Sensor | Pressure range | Boost capability | Notes |
|---|---|---|---|
| Bosch 0 273 003 204 | 200 kPa absolute | ~0.9 bar gauge max | Stock. MAF-based only |
| MPX4250AP | 250 kPa | ~1.5 bar gauge | QLCC-era chips |
| MPX4300 / Bosch 300 kPa | 300 kPa | ~2.0 bar gauge | 034EFI Rip Chip standard; R201 swap needed |
| MPXH6400A | 400 kPa | ~2.9 bar gauge | prjmod SD standard; R660 + board wire required |
| MPX6300 | 300 kPa (0-5V) | ~2.0 bar gauge | Some aftermarket SD builds |

### 7.4 EPROM Socket Installation
- Desolder original windowed EPROMs (S701 and S703)
- Install turned-pin DIP-28 sockets
- Allows chip swapping without soldering
- Both 27C256 (fuel/ign) and 27C64/27C256 (boost) can be socketed

---

## 8. PRJmod Firmware

### Overview
PRJmod is a community firmware modification by **prj** (GitHub: `prj/m232`) that adds:
- Speed density (VE-table MAF replacement)
- Launch control / No-Lift-Shift (LC/NLS)
- MFTS (coolant sensor) boost cut bypass
- Load decap (prevents uint8 overflow at load=255)
- Wideband O2 sensor logging
- Expanded boost pressure control

### WinlogDriver Live Data Decode Formulas
From `src/WinlogM232FastDiag/WinlogM232FastDiag/WinlogDriver.cpp`:

| Channel | Formula | Unit |
|---|---|---|
| N75 | `buf[0] / 194.0 * 100` | % duty cycle |
| MAP | `buf[2] / 255.0 * 5 / 1.03515625 * mapMult + mapOffset` | kPa |
| MAP_REQ | `buf[3]` (same formula) | kPa |
| KNOCK 1–5 | `buf[4–8] * 0.5` | arbitrary |
| RPM | `buf[31] * 40` | RPM |
| LOAD | `(buf[30]*256 + buf[29]) / 25.0` | % |
| UBATT | `buf[28] * 0.068` | V |
| IAT | `(buf[27] - 70) * 0.7` | °C |
| ECT | `(buf[26] - 70) * 0.7` | °C |
| TPS | `buf[25] * 0.416` | % |
| IGN | `0.75 * (buf[24] - buf[23])` | °BTDC |
| IPW_EFF | `0.52 * (buf[22] + buf[21]/256.0)` | ms |
| TVUB | `buf[20] * 0.010667` | ms (injector dead time) |
| VSS | `buf[19] * 2` | km/h |

**MAP sensor calibration constants** (from WinlogDriver):

| Sensor | mapMult | mapOffset |
|---|---|---|
| MPXH6400A | 82.61049 | 6.9558 |
| MPXH6300A | 62.89308 | 0.222013 |
| Bosch 300 kPa | 65.88235 | −6.35764 |
| Bosch 250 kPa | 50.0 | 10.0 |

### LC/NLS Scalars (working half offsets, build 0x0202)
From S2Forum disassembly of `Motorsport_Features_Code` subroutine at `WH 0x0610`:

| Scalar | WH offset | Stock value | Decoded |
|---|---|---|---|
| Hard RPM limit (spark cut) | `0x0617` | `0xC8` | 8000 RPM (×40) |
| Speed threshold for LC | `0x0620` | `0x02` | 4 km/h |
| LC ign retard RPM | `0x0625` | `0x71` | 4520 RPM |
| LC ign angle (ATDC) | `0x062B` | `0x29` | 41° ATDC |
| NLS min RPM | `0x063D` | `0xC8` | 8000 RPM |
| NLS ign angle (ATDC) | `0x0643` | `0x52` | 82° ATDC |
| Spark cut knock-disable RPM | `0x064C` | `0xB1` | 7080 RPM |
| LC ign cut RPM | `0x066E` | `0xB4` | 7200 RPM |
| AC idle target RPM | `0x03DF` | `0x4A` | ×10 → 740 RPM |

**RPM encoding throughout LC/NLS code:** `byte × 40 = RPM`  
**Ignition angles in LC/NLS:** ATDC (not BTDC) — different from the main map convention.

### Firmware Patches (vwnut8392 patcher XDF — target: 8A0907551B only)

1. **Wideband input via pin 46 / AN6**  
   8051 patch code: `90 BE 04 12 17 CF 22`  
   Replaces MAF bytes in logging stream with ADC4 raw value.

2. **NLS via coding plug pin 1** (clutch pedal brake switch)

3. **Race fuel MAP switch via ECU pin 42** (beta/untested)

4. **CEL as shift light** (European cars may need CEL wire added)

5. **AC idle fix** (RAM_20.6 flag, restores in-drive idle map when AC on)

**Note:** Patcher targets unmodified 551B only. "Base data does not match" error = ROM was already modified.

---

## 9. RS2 XDF Tables — vwnut8392 (Matt @ S&M Autosport, 2013)

Two XDF files in `XDF format v1.50`:

### 8D0907551B RS2 Boost.xdf (boost chip)
Author: Matt@S&M Autosport (VWnut8392). Covers boost chip address space 0x0000–0x7FFF.

| Address | Size | Title | Notes |
|---|---|---|---|
| 0x2520 | 16×10 | Boost pressure | MODIFIED? Same as 551AA S4/S6 |
| 0x6520 | 16×10 | Boost pressure (mirror) | Same as 551AA S4/S6 |
| 0x2A96 | 8×1 | Max boost pressure | — |
| 0x6A96 | 8×1 | Max boost pressure (mirror) | — |
| 0x2480 | 16×10 | N75 pulsewidth | MODIFIED? Same as 551AA S4/S6 |
| 0x6480 | 16×10 | N75 pulsewidth (mirror) | Same as 551AA S4/S6 |
| 0x264B | 10×8 | Characteristic map | — |
| 0x2000 | 200×2 | Map lookup table 01 | — |
| 0x6000 | 200×2 | Map lookup table 02 | — |

The 0x2xxx and 0x6xxx mirroring is the 32KB boost chip structure — 8KB code region (0x0000–0x1FFF) followed by calibration tables, with tables mirrored in the upper region.

### RS2 551B fuel timing.xdf (fuel/ign chip, 64KB)
318 tables total. Key confirmed addresses:

| Address | Size | Title | Notes |
|---|---|---|---|
| **0x8E13** | 16×16 | Fuel (A/F values) | Primary fuel map. Note: addresses in 64KB space = WH 0x0E13 |
| **0x9383** | 16×16 | Driving Ignition (BTDC) | Primary ign map, MODIFIED |
| **0x925F** | 16×16 | Ignition | — |
| **0x9594** | 16×16 | Ignition | — |
| **0x96B8** | 16×16 | Ignition | — |
| **0x9809** | 16×16 | Ignition | — |
| **0x992D** | 16×16 | Ignition | — |
| **0x90A8** | 16×16 | Emergency ignition | — |
| **0x9BAF** | 3×1  | Idle RPM | 730→880 RPM range |
| **0x9C43** | 3×1  | Idle RPM 2 | — |
| **0x8F91** | 6×6  | Cold start −40°C | — |
| **0x935F** | 16×1 | RPM scale | X axis |
| **0x9371** | 16×1 | Load scale | Y axis |

**Address mapping note:** The fuel XDF uses 64KB addresses (0x8000 offset applied).  
To get working-half offset: `WH_addr = xdf_addr - 0x8000`  
Example: `0x8E13 - 0x8000 = 0x0E13` = PRJ's confirmed fuel map address ✓

---

## 10. Variant Summary — All Confirmed Variants

| Variant | ECU PN | Trigger | ID string descriptor | Bosch ECU PN | ROM PN | Build | Boost chip |
|---|---|---|---|---|---|---|---|
| **3B/AA** | 447907404AA | Distributor | (none) | 0261200451 | 1267356462 | 0xF004 | 8KB |
| **RR/B** | 857907404B | Distributor | (none) | 0261200453 | 1267356261 | 0xF004 | 8KB |
| **551A** | 4A0907551A | D02 distributor | MOTOR D02PMC | 0261200465 | 1267356703 | 0x0202 | 8KB |
| **551AA** | 4A0907551AA | D03+HS cam | MOTR.RHV HS D03PMC | 0261200465 | 1267357391 | 0x0812 | 32KB |
| **551B** | 895907551B | D01+HS cam | MOTR.RHV HS D01PMC | 0261203643 | 1267358375 | 0x0274 | 32KB |
| **551B D02** | 8A0907551B | D02 distributor | MOTR.RHV RS2D02PMC | 0261203478 | 1267358289 | 0x0202 | 32KB |
| **551C** | 8A0907551C | D01+RS2 cam | MOTR.RHV RS2D01PMC | 0261203543 | 1267358668 | 0x0274 | 32KB |

**Trigger code meanings (from Bosch ID strings):**
- `D01` — cam pulley hall sensor, Type 85 body connector
- `D02` — distributor hall sensor (same architecture as 3B/RR)
- `D03` — cam pulley hall sensor, Type 44 body connector
- `HS` — explicit Hall Sensor flag (only on cam-trigger variants)
- `RS2` — RS2 Avant application (explicit in descriptor)

**Firmware siblings:**
- 551B and 551C: zero code diff — same firmware, calibration only differs
- 551A and 551AA: 80.6% code identical
- 3B and RR: 81.8% code identical; ign map 1 byte-for-byte identical

---

## 11. MapFinder Tool

`mapfinder.jar` (PRJ, 2012–2013) — Java tool for automated map detection in M2.3 ROMs.

Usage: `java -jar mapfinder.jar <input.bin> > <output.xdf>`

Identifies 16×16 map tables by scanning for Bosch descriptor byte patterns. Output is a TunerPro XDF. Confirmed output used to establish 3B/RR map addresses:
- Fuel maps: `0x6A6A`, `0x6BF8`, `0x6D50`, `0x6E74`
- Ign maps: `0x7052`, `0x7643`, `0x77AB`, `0x7913`
- These are file offsets for the 32KB flat 3B ROM (no WH offset needed).

---

## 12. Dual-CPU IPC Architecture

The two CPUs communicate via shared external memory on the Grundplatte.

From disassembly of `aan_fuel-ign_551aa.bin`:
- **0xA040** — inter-processor communication register (8+ MOVX references in fuel chip)
- **0xA080** — inter-processor communication register (8+ MOVX references in fuel chip)

The boost CPU writes computed boost request, current MAP reading, and wastegate duty cycle to these shared registers. The fuel CPU reads them during each calculation cycle.

This architecture means:
- MAP sensor data ultimately reaches the fuel CPU via the boost CPU's processed output
- The R660/board wire SD modification bypasses this path — it feeds MAP directly to AN5 of the fuel CPU, allowing the fuel CPU to read raw MAP directly without boost CPU involvement
