# Bosch Motronic M2.3 / M2.3.2 — Complete Hardware & Firmware Reference

*Sources: Official Bosch schematics (Y261 C20 232-5 Zusatzplatte, Y261 C27 020 Grundplatte),
PRJ GitHub (github.com/prj/m232), vwnut8392 S2Forum patcher threads, community RE sessions
March 2026, direct chip reads from hardware.*

---

## 1. ECU Physical Architecture

The M2.3.2 ECU contains **two separate boards** in a single housing:

```
┌─────────────────────────────────────────────────────────────┐
│  ZUSATZPLATTE  (Boost/Supplement Board — Y261 C20 232-5)    │
│                                                             │
│  S700  Boost CPU (SAB80C535 variant — DIP-40)               │
│  S701  Fuel/Ignition EPROM  (DIP-28, 27C256 or 27C512)      │
│  S702  Address latch / memory controller                    │
│  S703  Boost EPROM  (DIP-28, 27C64 or 27C256)               │
│  N700  SRAM bank 0  (address register)                      │
│  N701  SRAM bank 1  (address register)                      │
│  S712  Inter-board interface / signal routing               │
│  S760  Additional logic (comparators/timers for boost)      │
│  MAP sensor port  (vacuum port on ECU case → boost board)   │
│  Inter-board connector  (carries signals to Grundplatte)    │
└─────────────────────────────────────────────────────────────┘
               ↕  flat cable / board connector
┌─────────────────────────────────────────────────────────────┐
│  GRUNDPLATTE  (Main Motor Board — Y261 C27 020)             │
│                                                             │
│  S250  Motor CPU (SAB80C535 — DIP-40)                       │
│  S251  External data SRAM  (DIP-28)                         │
│  S252  Address decode / bus logic                           │
│  S253  (×multiple) Op-amp / comparator array                │
│  S254  Output driver stage                                  │
│  S255  Signal conditioning                                  │
│  S300  Power supply regulation                              │
│  S310  Voltage regulator                                    │
│  S400  Input buffer bank A (injectors/sensors)              │
│  S450  Input buffer bank B                                  │
│  S500  Output power stage controller                        │
│  S501  Output driver                                        │
│  S220  MAF signal conditioning                              │
│  S230  Crank/cam trigger signal conditioning                │
│  S200  Reference voltage generation                         │
│  S201  Oscillator / clock                                   │
│  55-pin edge connector  (to engine harness)                 │
└─────────────────────────────────────────────────────────────┘
```

**Board drawing numbers:**
- Boost board: `Y261 C20 232-5 Zusatzplatte` (M2.32/2.41)
- Motor board: `Y261 C27 020 Grundplatte` (M2.3.2)

**The two CPUs communicate via shared external memory** mapped at addresses
`0xA040` and `0xA080` — confirmed by MOVX analysis of fuel/ign chip firmware.

---

## 2. CPU: Siemens SAB80C535

Both boards use the same CPU family. The motor chip has been confirmed as
`BD26422` (Bosch internal designation) which is a custom-labelled
**Siemens SAB80C535**, Intel 8051 core, ©INTEL 90, DIP-40 package.

### SAB80C535 Key Specifications

| Property | Value |
|---|---|
| Architecture | Intel MCS-51 / 8051 compatible |
| Data bus | 8-bit |
| Address space | 64KB program, 64KB data (external) |
| Internal ROM | None — EA pin tied low, all code from EPROM |
| Internal RAM | 256 bytes |
| External RAM | Up to 64KB via MOVX |
| ADC | 8-bit, 8 multiplexed inputs (Port 6 / AIN0–AIN7) |
| Timers | Three 16-bit (T0, T1, T2) + capture/compare unit |
| Serial port | Full-duplex UART |
| Interrupt vectors | 12 (extended 8051) |
| Clock | 12 MHz (production ECUs) |
| Package | DIP-40 |
| Intel license | ©INTEL 90 (confirmed from chip markings) |

### Key ADC SFRs (SAB80C535)

| SFR | Address | Function |
|---|---|---|
| ADCON | `$D8h` | ADC Control — channel select + start + done flag |
| ADDAT | `$D9h` | ADC Data Register — 8-bit result |
| DAPR  | `$DAh` | DAC/Reference Program Register |
| P6    | `$DBh` | Port 6 — AIN0–AIN7 analog inputs |

**ADCON bit map:**

| Bit 7 | Bit 6 | Bits 5-4 | Bit 3 | Bits 2-0 |
|---|---|---|---|---|
| ADEX (extended ref) | ADCI (conv done) | Reserved | ADCS (start conv) | MX2:MX0 (channel) |

### ADC Polling Subroutine (AAN 551AA firmware at `$1726`)

All ADC reads route through a single subroutine. Channel is encoded in DPL:

```asm
$1726  SETB  $05h.2          ; Set "conversion in progress" flag
$1728  MOV   A, #F8h         ; Preserve ADEX+ADCI, clear channel bits
$172A  ANL   A, ADCON
$172C  ORL   A, DPL          ; DPL = channel select (caller sets DPTR = 0xBExx)
$172E  MOV   ADCON, A        ; Write: select channel + ADEX=1 → triggers conversion
$1730  MOV   DAPR, #00h      ; Full reference range
$1733  JB    ADCON.4, $1733  ; POLL: spin while ADCI=1 (conversion in progress)
$1736  MOV   A, ADDAT        ; Read 8-bit result
$1738  JNB   $05h.2, $1726   ; If another channel queued, loop
$173B  RET
```

The ADC interrupt (`IADC`) ISR at `$2080` is simply `RETI` — ADC uses **polling only**.

---

## 3. ADC Channel Map

### Motor Chip (Grundplatte — S250)

| Channel | AIN | ECU Pin | Confirmed Function | Source |
|---|---|---|---|---|
| AN0 | AIN0 | — | TPS (Throttle Position Sensor) | vwnut8392 |
| AN1 | AIN1 | — | UBAT (Battery Voltage) | vwnut8392 |
| AN2 | AIN2 | Pin 44 | IAT (Intake Air Temperature) | vwnut8392 |
| AN3 | AIN3 | Pin 45 | ECT (Engine Coolant Temperature) | vwnut8392 |
| AN4 | AIN4 | Pin 39 | Coding Plug Pin 2 — **free ADC** | vwnut8392 |
| AN5 | AIN5 | — | Stock: auto trans. / **SD MAP sensor input** | vwnut8392 |
| AN6 | AIN6 | Pin 46 | Early: map switch / **WB logging input** | vwnut8392 |
| AN7 | AIN7 | — | Lambda (S600 variant) | Preliminary |

**AN4 free uses:** ethanol content sensor, wideband secondary, additional logging.

**AN5 SD MAP path:** R660 removal + boost→motor inter-board wire routes the external
MAP sensor analog signal to AN5 on the motor chip. Stock firmware reads AN5 as
automatic transmission signal — prjmod SD firmware repurposes it for MAP.

**AN6 WB logging:** The vwnut8392 patcher replaces MAF log bytes with AN6 (ADC4)
raw value. Works with any 0–5V wideband controller.

### Boost Chip (Zusatzplatte — S700)

| Channel | AIN | Status | Function |
|---|---|---|---|
| AN2 | AIN2 | **Grounded by CPU** | Cannot use without lifting boost processor |
| AN3 | AIN3 | Active | TPS → RAM_6C |
| AN4 | AIN4 | Active | IAT → RAM_69 |
| AN5 | AIN5 | Active | ECT → RAM_6A |
| AN6 | AIN6 | **Grounded by CPU** | Cannot use without lifting boost processor |
| AN7 | AIN7 | Active | Altitude sensor → RAM_67 |

AN2 and AN6 on the boost chip are tied low by the CPU itself — they cannot be
activated without physically lifting or removing the boost processor IC.

---

## 4. Interrupt Vector Table (AAN 551AA)

All 12 SAB80C535 interrupt vectors confirmed from ROM analysis:

| Vector | Addr | ISR | Handler | Notes |
|---|---|---|---|---|
| Reset | `$0000` | — | LJMP `$1297` | Startup / init |
| INT0 | `$0003` | IE0 | `$2000` → LJMP `$0438` | Background loop |
| Timer0 | `$000B` | TF0 | `$2010` → SETB + RETI | Software flag |
| INT1 | `$0013` | IE1 | `$2020` → LJMP `$0202` | |
| Timer1 | `$001B` | TF1 | `$2030` → LJMP `$03CA` | Crank sync |
| Serial | `$0023` | RI+TI | `$2060` → LJMP `$5A39` | KWP1281 |
| Timer2 | `$002B` | TF2 | `$2070` inline | `INC $3Eh; CLR; RETI` |
| **IADC** | **`$0043`** | **IADC** | **`$2080` → RETI** | **ADC polling only** |
| IEX2 | `$004B` | — | `$2090` → RETI | Disabled |
| IEX3/CC0 | `$0053` | — | `$20A0` inline | |
| IEX4/CC1 | `$005B` | — | `$20B0` → LJMP `$287F` | |
| IEX5/CC2 | `$0063` | — | `$20C0` → LJMP `$006E` | |
| IEX6/CC3 | `$006B` | — | `$20D0` → RETI | Disabled |

### ISR Loop Model

The firmware runs three concurrent loops:

1. **Background loop** (INT0/IE0 at `$0438`) — map lookups, stores results in RAM
2. **Crank-synchronous loop** (Timer1 ISR at `$03CA`) — injection pulse width, ign timing
3. **Per-tooth loop** (Timer2 ISR inline at `$2070`) — MAF integration, ign event firing
4. **Serial ISR** (`$5A39`) — KWP1281 diagnostic communication

---

## 5. Firmware Identification Strings

Every M2.3.2 chip embeds an ASCII identification string at working half offset `0x7F00`:

```
Format: [checksum_byte] [ECU_PN] [engine_desc] [descriptor] [trigger_code] [Bosch_ECU_PN] [ROM_PN]

Example (ADU/RS2 551C):
  To8A0907551C  2,2l R5 MOTR.RHV RS2D01PMC 0261203543 1267358668
  ^^                              ^^^  ^^^
  ||                              |    trigger code (D01 = cam hall)
  ||                              RS2 = RS2 application flag
  |ECU PN (8A = Type 85 body, RS2)
  checksum prefix bytes (not computed checksum — Bosch identification)
```

### Trigger Codes

| Code | Description | Variants |
|---|---|---|
| D02 | Distributor hall sensor | 551A (early AAN), 551B_D02 (early RS2), 3B/RR (no explicit code) |
| D03 | Cam pulley hall sensor (Type 44 body) | 551AA (late AAN) |
| D01 | Cam pulley hall sensor (Type 85 body) | 551B (ABY S2), 551C (ADU RS2) |
| RS2 | RS2 application flag | 551C, 551B_D02 |
| HS  | Hall Sensor — explicit flag | 551AA, 551B, 551C |

### Complete Variant ID Table

| Variant | ECU PN | Full ID String | Build | Cal | Boost |
|---|---|---|---|---|---|
| 551A | 4A0907551A | `MOTOR D02PMC 0261200465 1267356703` | 0x0202 | 0xA04A | 8KB |
| 551AA | 4A0907551AA | `2,2l R5 MOTR.RHV HS D03PMC 0261200465 1267357391` | 0x0812 | 0x0202 | 32KB |
| 551B | 895907551B | `2,2l R5 MOTR.RHV HS D01PMC 0261203643 1267358375` | 0x0274 | 0x7F02 | 32KB |
| 551B_D02 | 8A0907551B | `2,2l R5 MOTR.RHV RS2D02PMC 0261203478 1267358289` | 0x0202 | 0xA1E9 | 32KB |
| 551C | 8A0907551C | `2,2l R5 MOTR.RHV RS2D01PMC 0261203543 1267358668` | 0x0274 | 0x7F02 | 32KB |
| 3B_AA | 447907404AA | `MOTOR PMC 0261200451 1267356462` | 0xF004 | 0x029B | 8KB |
| RR_B | 857907404B | `MOTOR PMC 0261200453 1267356261` | 0xF004 | 0x0253 | 8KB |

---

## 6. Boost Chip Architecture

### Size and Structure by Variant

| Variant | Size | Code | Tables | Chip |
|---|---|---|---|---|
| 3B / RR | 8KB | 0x0000–0x1FFF | None | 27C64 |
| 551A | 8KB | 0x0000–0x1FFF | None | 27C64 |
| 551AA / 551B / 551C | 32KB | 0x0000–0x1FFF (entropy ~6.1) | 0x2000–0x7FFF (entropy ~4.6) | 27C256 |

### Boost Chip Startup Signature

All variants start with inline 8051 startup code, **not** a LJMP reset vector:

```
0x0000: C2 AF  = CLR EA    (disable all interrupts — valid 8051 init)
0x0002: 00     = NOP
0x0003: 20 AF ..  = JB EA, ...  (inline handler)
```

The LJMP offset at byte 6–7 varies per variant:
- 3B / 551A: `LJMP 0x08EE`
- 551AA: `LJMP 0x0A2F`
- ABY 551B: `LJMP 0x0AA3`

### Boost Chip IPC Registers

The motor CPU communicates with the boost CPU via MOVX (external memory access):

| Address | Direction | Purpose |
|---|---|---|
| `0xA040` | Motor reads/writes | IPC register 1 — RPM/load data to boost |
| `0xA080` | Motor reads/writes | IPC register 2 — boost request / MAP reading |

Confirmed by 8+ MOVX references at each address in the motor chip disassembly.

---

## 7. ECU Firmware Checksum (PRJmod M232csum.dll)

### Stock Bosch ROMs — No Computed Checksum

Stock chips use ASCII part-number text at WH `0x7F00`–`0x7F40` as identification.
The bytes at `0xFF00`/`0xFF01` (64KB doubled file) happen to be the first two bytes
of this ASCII string — they are **not a computed checksum**.

### PRJmod Checksum Algorithm (from M232csum.dll source)

**32KB working half (boost chip or fuel/ign working half):**

```
sum_even = sum of bytes[0], bytes[2], bytes[4], ... bytes[0x3FF8]
sum_odd  = sum of bytes[1], bytes[3], bytes[5], ... bytes[0x3FF9]
total = (sum_even + sum_odd + bytes[0x3FFE] + bytes[0x3FFF] + 0x01FE) & 0xFFFF
store: bytes[0x3FFA] = total >> 8
       bytes[0x3FFB] = total & 0xFF
```

Bytes `0x3FFE`/`0x3FFF` (the build/firmware version word) are included in the
sum as constants. The `+0x01FE` term is also hardcoded. Complement bytes at
`0x3FFC`/`0x3FFD` are stored for the 32KB→64KB doubling case.

**64KB fuel/ign chip (64KB doubled file):**

Sums all bytes `[0x0000:0xFEFF]` and stores result at `[0xFF00]`/`[0xFF01]`.

**TunerPro integration:** M232csum.dll is registered as a plugin. The XDF checksum
block for prjmod ROMs points to `0x3FFA`/`0x3FFB`. Stock ROMs use no plugin —
TunerPro `CalcMethod=0x0` (None).

---

## 8. Hardware Modifications Reference

### 8.1 MPXH6400A MAP Sensor Upgrade (prjmod SD mode)

Required for prjmod speed-density firmware. Installs external 400kPa MAP sensor
signal onto the motor chip's AN5 ADC input.

**Steps:**

1. Remove solder from via labelled `S900`/`C660` on the **boost board** (Zusatzplatte)
2. Remove solder from via to the right of D232 (towards D235) on the **motor board** (Grundplatte)
3. Solder a wire through both vias, connecting boost board to motor board
4. Remove resistor **R660** from the motor board

**Result:** External MAP sensor output (on boost board) is routed to motor chip AN5.
Boost range: stock 200kPa → up to ~2.9 bar absolute with MPXH6400A.

### 8.2 AAN → RS2 ECU Conversion

The AAN and RS2 ECU are **identical hardware**. Only three components differ:

| Item | AAN | RS2 |
|---|---|---|
| Resistor R201 | ~6.15 kΩ | **5.6 kΩ 1% 1206 SMD** |
| MAP sensor | Bosch 200kPa | 300kPa (MPX4300 class) |
| Chip set | 551AA fuel + 551AA boost | 551C fuel + 551C boost |

R201 value confirmed by PRJ (S2Forum, 2013). Standard 1206 SMD footprint.

### 8.3 MAP Sensor Options

| Sensor | Range | Use Case |
|---|---|---|
| Bosch 0 280 142 xxx | 200kPa | Stock — MAF-based only |
| MPX4250AP | 250kPa | QLCC-era chips, ~1.5 bar gauge max |
| MPX4300 / Bosch 300kPa | 300kPa | 034EFI Rip Chip, AAN→RS2 swap |
| MPXH6400A | 400kPa | prjmod SD standard — R660 + wire mod required |
| MPX6300 | 300kPa abs, 0–5V | Some aftermarket SD builds |

### 8.4 EPROM Socket Installation

De-solder original windowed EPROM chips (27C256 DIP-28), solder in machined DIP-28
turned-pin sockets. Install UV-erasable EPROMs (27C256 for 32KB, 27C512 for 64KB
mirrored fuel chip).

**EPROM pinouts confirmed from Bosch schematic (S701/S703):**
- Address lines A0–A14 routed from CPU address bus via S702 latch
- CE (Chip Enable) and OE (Output Enable) driven by CPU port lines
- Data bus D0–D7 shared with CPU data bus

---

## 9. PRJmod Firmware Reference

### 9.1 Overview

PRJmod (by `prj` / github.com/prj/m232) converts the M2.3.2 from MAF-based to
speed-density fuelling and adds motorsport features. Target ROM: **8A0907551B**
(ABY S2 Coupe, D01 cam trigger). The 32KB chip architecture of 551B/551AA provides
space for the additional calibration tables.

### 9.2 Key Map Addresses (build 0x0202, WH offsets)

Source: PRJ m232.xdf, verified against real ROMs.

| Map | WH Offset | Size | Decode Formula |
|---|---|---|---|
| Fuel enrichment P/T | `0x2E17` | 16×16 | `1/(raw/128)*14.7 = AFR` |
| Ign map 1 (PT primary) | `0x30AC` | 16×16 | `raw × 0.6491 − 8.2186 = °BTDC` |
| Ign map 2 | `0x3263` | 16×16 | same |
| Ign map 3 | `0x3387` | 16×16 | same |
| Ign map 4 | `0x3598` | 16×16 | same |
| Ign map 5 | `0x36BC` | 16×16 | same |
| Ign map 6 | `0x380D` | 16×16 | same |
| Ign map 7 | `0x3931` | 16×16 | same |
| LC/NLS code entry | `0x0610` | — | Signature: `C0 82 C0 83` |
| SD VE table (prjmod) | `0x2074` | 16×16 | `blank = 0x02`, tuned = varied |

**Note:** AAN (551AA) uses different addresses per PRJ's XDF — fuel `0x0E13`,
ign `0x125F`. These differ from the ABY/RS2 addresses above. AAN direct chip
read still required to confirm.

### 9.3 LC/NLS Scalars (WH offsets, prjmod only — 0x0202 firmware)

Source: PRJ's ROM, confirmed from S2Forum m232.org thread.

| Scalar | WH Offset | Stock | Decoded |
|---|---|---|---|
| Hard RPM limit (spark cut) | `0x0617` | `0xC8` | 8000 RPM |
| Speed threshold for LC | `0x0620` | `0x02` | 4 km/h |
| LC ign retard RPM | `0x0625` | `0x71` | 4520 RPM |
| LC ign angle (ATDC) | `0x062B` | `0x29` | 41° ATDC |
| NLS min RPM | `0x063D` | `0xC8` | 8000 RPM |
| NLS ign angle (ATDC) | `0x0643` | `0x52` | 82° ATDC |
| Spark cut knock-disable RPM | `0x064C` | `0xB1` | 7080 RPM |
| LC ign cut RPM | `0x066E` | `0xB4` | 7200 RPM |
| AC idle target RPM | `0x03DF` | `0x4A` | ×10 → 740 RPM |

**RPM encoding:** `byte × 40 = RPM` throughout LC/NLS code.
**Ign angles:** ATDC in LC/NLS (not BTDC like the map tables).

### 9.4 WinlogDriver Decode Formulas

Source: PRJ WinlogM232FastDiag/WinlogDriver.cpp (github.com/prj/m232).

| Signal | Formula | Unit |
|---|---|---|
| N75 duty cycle | `buf[0] / 194.0 × 100` | % |
| MAP | `buf[2] / 255.0 × 5 / 1.03515625 × mapMult + mapOffset` | kPa |
| MAP request | `buf[3]` — same formula | kPa |
| Knock 1–5 | `buf[4–8] × 0.5` | V |
| RPM | `buf[31] × 40` | RPM |
| Load (16-bit) | `(buf[30]×256 + buf[29]) / 25.0` | |
| Battery voltage | `buf[28] × 0.068` | V |
| IAT | `(buf[27] − 70) × 0.7` | °C |
| ECT | `(buf[26] − 70) × 0.7` | °C |
| TPS | `buf[25] × 0.416` | % |
| Ignition advance | `0.75 × (buf[24] − buf[23])` | °BTDC |
| Inj. pulse width | `0.52 × (buf[22] + buf[21]/256.0)` | ms |
| Inj. dead time | `buf[20] × 0.010667` | ms |
| Vehicle speed | `buf[19] × 2` | km/h |
| MAF table # | `buf[18]` | |
| MAF cell value | `buf[17]` | |

**MAP sensor calibration constants (WinlogDriver hardcoded):**

| Sensor | mapMult | mapOffset |
|---|---|---|
| MPXH6400A | 82.61049 | 6.9558 |
| MPXH6300A | 62.89308 | 0.222013 |
| Bosch 300kPa | 65.88235 | −6.35764 |
| Bosch 250kPa | 50.0 | 10.0 |

### 9.5 PRJmod Feature Patches

Source: vwnut8392 patcher XDF (S2Forum "Added feature Patcher for PRJmod"). Target: **8A0907551B only**.

**1. Wideband input logging (pin 46 / AN6)**
Replaces MAF log bytes in data stream with AN6 raw ADC value.
8051 patch bytes: `90 BE 04 12 17 CF 22` — inserted at free code space.
Patcher auto-corrects motor chip checksum. Requires unmodified base 551B.
Error "Base data does not match" = ROM already modified; hand-port required.

**2. NLS via coding plug pin 1**
Routes NLS activation to coding plug pin 1 instead of the normal pin 2 + 12V method.
Requires clutch pedal brake-light switch (open = pedal up, ground = pressed).

**3. Race fuel MAP switch via ECU pin 42 (BETA)**
Grounding ECU pin 42 switches motor chip to alternate fuel map.
May require adding a wire to the ECU connector. BETA — untested as of 2018.
Note: pin 42 also used as prjmod SD enable ground in some builds.

**4. CEL as shift light**
CEL output is a ground-sink. RPM threshold set ~300 RPM below target shift point.
European cars may need CEL wire added to harness.

**5. AC idle fix**
prjmod removed AC idle stepper increase from 551B. `RAM_20.6` is the AC-on flag.
Patch restores the "in-drive" automatic idle map when AC is on.

---

## 10. Boost Chip XDF Map Inventory

Source: vwnut8392 / Matt@S&M Autosport, 8D0907551B RS2 Boost.xdf (2013-02-17)

The boost chip (32KB, working half = full file) contains 20 defined tables.
File addresses are direct offsets into the 32KB boost chip binary.

Note: XDF uses `REGION size=0x7FFF`, `baseoffset=0`. Tables at `0x2xxx` and `0x6xxx`
are mirrored pairs — the chip is internally mirrored (A15 state irrelevant).

| Address | Dims | Table Name | Notes |
|---|---|---|---|
| `0x2000` | 200×2 | Map lookup table 01 | |
| `0x6000` | 200×2 | Map lookup table 02 | Mirror of 0x2000 |
| `0x2218` | 25×8 | Unnamed 8×25 | MODIFIED? Traces constantly |
| `0x6218` | 25×8 | Mirror | MODIFIED? |
| `0x2480` | 10×16 | N75 pulsewidth 16×10 | MODIFIED? Same as 551AA S4/S6 |
| `0x6480` | 10×16 | N75 pulsewidth mirror | Same as 551AA S4/S6 |
| `0x2520` | 10×16 | Boost pressure 16×10 | MODIFIED? Same as 551AA S4/S6 |
| `0x6520` | 10×16 | Boost pressure mirror | Same as 551AA S4/S6 |
| `0x264B` | 8×10 | Characteristic map 10×8 | |
| `0x664B` | 8×10 | Characteristic map mirror | |
| `0x2A8C` | 9×2 | 9×2 | Modified? Traces |
| `0x2A96` | 8×1 | Max boost pressure | |
| `0x6A96` | 8×1 | Max boost pressure mirror | |
| `0x2ACE` | 10×16 | Unnamed 16×10 | Traces constantly |
| `0x6ACE` | 10×16 | Mirror | |
| `0x2B6E` | 10×16 | Unnamed 16×10 | |
| `0x6B6E` | 10×16 | Mirror | |
| `0x2C0E` | 10×16 | Unnamed 16×10 | |
| `0x6C0E` | 10×16 | Mirror | |

**Note:** vwnut8392 comment "Same as 551AA S4/S6" indicates boost and N75 tables
are **identical between ABY 551B and AAN 551AA** — different fuel/ign cal, same
boost tuning.

---

## 11. Fuel/Ign XDF Map Inventory (RS2 551B)

Source: vwnut8392, RS2 551B fuel timing.xdf (2013-02-19), 318 total table entries.

**Primary maps (confirmed):**

| Address | Dims | Name | Decode |
|---|---|---|---|
| `0x8E13` | 16×16 | Fuel (raw / A/F Values) | WH = 0x0E13 |
| `0x9383` | 16×16 | Driving Ignition (BTDC) | Modified, traces |
| `0x925F` | 16×16 | Ignition map 2 | |
| `0x9594` | 16×16 | Ignition map 3 | |
| `0x96B8` | 16×16 | Ignition map 4 | |
| `0x9809` | 16×16 | Ignition map 5 | |
| `0x992D` | 16×16 | Ignition map 6 | |
| `0x90A8` | 16×16 | Emergency ignition | |
| `0x8F91` | 6×6 | Cold start −40°C | |
| `0x9BAF` | 3×1 | Idle RPM (730→880 rpm) | |
| `0x9C43` | 3×1 | Idle RPM 2 | |
| `0x9D1C` | 6×1 | Lambda 1 | |
| `0x9D2A` | 6×1 | Lambda 2 | |

**Address note:** XDF `baseoffset=0`, file is 64KB. WH offset = XDF address − 0x8000.
So fuel map at XDF `0x8E13` = WH offset `0x0E13` = matches PRJ's m232.xdf exactly.

**Ign axes (WH offsets):**
- RPM scale: `0x135F` (16 points, 0x3A bytes = address type)
- Load scale: `0x1371` (16 points)

---

## 12. New Variant: RS2 D02 (551B_D02)

The S2Forum zip contains an early RS2 bin with **distributor trigger** — predating
the cam-pulley hall sensor found in production RS2:

```
ECU PN:       8A0907551B
ID:           8A0907551B  2,2l R5 MOTR.RHV RS2D02PMC 0261203478 1267358289
Trigger:      D02  (distributor hall — same as 3B/RR and early AAN)
Bosch ECU PN: 0261203478
ROM PN:       1267358289
Build:        0x0202
Cal tag:      0xA1E9
```

The RS2 therefore followed the same distributor→cam evolution as the AAN:
```
RS2 early: D02 (8A0907551B, 0261203478)  ← this bin
RS2 prod:  D01 (8A0907551C, 0261203543)  ← all production RS2 Avants
```

ECU PN prefix `8A` = Type 85 RS2 Avant specific.

---

## 13. CRC32 Fingerprint Reference

Complete KNOWN_CRCS table for all confirmed direct chip reads:

| CRC32 | Variant | Description |
|---|---|---|
| `0x4378E077` | 551C | ADU/RS2 fuel/ign WH, ROM PN 1267358668 |
| `0x1529520A` | 551C | ADU/RS2 fuel/ign full 64KB |
| `0x4EE87833` | 551c_boost | ADU/RS2 boost 32KB, build 0x0202 |
| `0xA98CB481` | 551B | ABY fuel/ign WH, ROM PN 1267358375 |
| `0x97D26DD1` | 551B | ABY fuel/ign full 64KB |
| `0xF6E33043` | 551b_boost | ABY boost 32KB, build 0x0202 |
| `0xF7432BB5` | 551A | AAN early fuel/ign WH, D02 distributor |
| `0xBBAFE260` | 551A_boost | AAN early boost 8KB, build 0xA04B |
| `0xB9A49F8A` | 551AA | AAN late fuel/ign WH, D03+HS cam |
| `0x16707F66` | 551AA_boost | AAN late boost 32KB, build 0x0202 |
| `0xC5B30158` | 551B_D02 | RS2 early fuel/ign WH, D02, ROM PN 1267358289 |
| `0xC349075E` | 551B_D02 | RS2 early fuel/ign full 64KB |
| `0x288CBFBC` | 551B_D02_boost | RS2 early boost 32KB |
| `0x0AE3CACD` | 404 | 3B fuel/ign, 447907404AA, ROM PN 1267356462 |
| `0xFBE0A74A` | 404 | RR fuel/ign, 857907404B, ROM PN 1267356261 |
| `0xF50660DA` | 404_boost | 3B boost 8KB, build 0x0254 |
| `0xEA8D46DF` | 404_boost | RR boost 8KB, build 0x0255 |

---

## 14. Tools Reference

| Tool | Purpose | Source |
|---|---|---|
| PRJ m232 repo | prjmod source, checksum DLL, XDF | github.com/prj/m232 |
| M232csum.dll | TunerPro checksum plugin | PRJ repo / TunerPro install |
| Mapfinder.jar | Automated map address detection in 8051 ROMs | S2Forum (2012) |
| vwnut8392 patcher | Feature patches for 551B ROMs | S2Forum patcher thread |
| Ghidra | 8051 disassembly (use NSA 8051 module) | ghidra.re |
| TunerPro RT | XDF-based map editor | tunerpro.net |
| T48 programmer | EPROM read/write | |

### Mapfinder Usage
```
java -jar mapfinder.jar <input.bin> > <output.xdf>
```
Input: single-name .bin file. Output: TunerPro XDF with detected map addresses.
Confirmed working on 3B/404 ROMs — produces header-based Bosch descriptor format
with 36-byte offset to map data.

---

## 15. Open Items / Verification Needed

| Item | Status | Notes |
|---|---|---|
| AAN 551AA map addresses (direct chip read) | ❌ Pending | PRJ XDF (prjmod 0x0202 firmware) says 0x0E13 fuel / 0x125F ign. AAN direct chip read required to confirm stock addresses match ABY pattern. |
| MFTS boost cut bypass — exact byte signature | ❌ Pending | Need Ghidra trace of MFTS check routine |
| Load decap patch — exact offset | ❌ Pending | Near load accumulation routine, 1–2 byte change |
| Lambda delay patch offset | ❌ Pending | Start of lambda routine in fuel chip |
| S500/S501 output stage — injector driver pins | ❌ Pending | Need schematic cross-reference to ECU connector |
| Crank ISR at $03CA — full disassembly | ❌ Pending | Contains injection pulse width calc + ign timing |
| AN7 function (motor chip) | 🔶 Preliminary | Lambda on S600 variant — unconfirmed on AAN/ABY |
| S2Forum 32-page hardware thread | ❌ Pending upload | Zip not yet received |
| Rev limit address — stock 551x | ✓ Resolved | WH 0x3FF0 is NOT rev limit. End-of-cal RPM table at 0x3FE0–0x3FFF confirmed from ABY direct read. Stock rev limit in 8051 code (hardcoded compare). prjmod: WH 0x0617 (raw×40 = RPM). |
| ABY/ADU calibration memory layout | ✓ Confirmed | Code region WH 0x0000–0x2CFF (LJMP-fill). Calibration WH 0x2D00–0x3FFF. PRJ XDF addresses (0x8xxx–0xAxxx) are for prjmod firmware, NOT stock 551B/C chips. |
| Maps J/K (WH 0x3D05 / 0x3E65) | 🔶 PROVISIONAL | 16×16, values 13–22°BTDC at 2320 RPM axis. Possible idle ign maps. Identical mirror pair. |
| XDF attribution | ✓ Confirmed | "RS2 551B fuel timing.xdf" is for prjmod 551AA_0202 firmware (not stock ABY 551B). "8D0907551B RS2 Boost.xdf" is for stock boost chip. |

---

## 034EFI Rip Chip Package — Boost Chip Pairing (2026-03 fingerprinting session)

All 034EFI `.034` files from the released package were fingerprinted. 18 of 20 files are valid standard 034EFI scramble format. 2 files (`893906266D` 7A late ECU, 54903 bytes each) are a different encoding — not standard 034 scramble and not 65536-byte standard size. These belong to the Hitachi/7A project.

### Confirmed CRC32 fingerprints

| CRC32 | File | Variant | Notes |
|-------|------|---------|-------|
| `0x956BFC9C` | Stock RipChip | 551AA_0202 | Reconstructed stock. No HW mods. |
| `0xA47011AB` | Stage 1+ K24 | 551AA_0202 | 3.0 BAR, RS2 injectors, 7200rpm, +40whp |
| `0x16FD8953` | Stage 1 K24 | 551AA_0202 | Variant of Stage 1 |
| `0x9A8A6B4E` | GT28RS Stage 1 R2 | 551AA_0202 | 3.0 BAR, 550cc, 7000rpm, 285whp |
| `0x6F3AE675` | GT3071 R8 42lb | 551AA_0202 | 3.0 BAR, 440cc, 7200rpm, 346whp |
| `0x07DA1752` | GT3071 R9.1 550cc | 551AA_0202 | 3.0 BAR, 550cc, 7200rpm |
| `0x2EB58546` | GT2871 R9.1 550cc EV14 | 551AA_0202 | 3.0 BAR, 550cc EV14, 7200rpm, 330whp |
| `0xA77BB88E` | GT2871 R9 440cc Siemens | 551AA_0202 | 3.0 BAR, 440cc Siemens, 7200rpm, 330whp |
| `0x28C04D7B` | RS2 91Oct | 551AA_0202 | ABY/ADU application, 3.0 BAR, 440cc |
| `0xB9F0FD51` | GT3071 R9 440cc Siemens | 551AA_0202 | 3.0 BAR, 440cc Siemens, 7200rpm, 346whp |
| `0x69156B3A` | GT2871 Boost chip | 551AA_0202_boost | Build 0x0054. 3.0 BAR MAP. 26psi OB/22psi. |
| `0x39DC67DA` | GT3071 Boost chip 26-23psi | 551AA_0202_boost | Build 0x0054. 3.0 BAR MAP. |
| `0x84B0504E` | 7A NA Big MAF R2 | 7A_NA | ECU 893906266B (early). 034 billet MAF. |
| `0xC075767F` | 7A Stage 1 R1 | 7A_Stage1 | ECU 893906266B (early). Stock mods. |
| `0xA01C4EDA` | 7A Turbo Stage 2 550cc | 7A_Turbo | ECU 893906266D (late). 034 turbo kit. |
| `0x55177DDB` | 7A Turbo Kit Stage 1 R2 | 7A_Turbo | ECU 893906266B (early). 034 T3/T4 kit. |
| `0x4818FA0B` | AAH Stage 1+ R1 | AAH | MMS-200 ECU (8A0 906 266A). Big bore MAF. |
| `0x28C04D7B` | RS2 91Oct (RS2 Tuned) | 551AA_0202 | ADU/ABY specific. 3.0 BAR, 440cc. |

### Boost chip pairing rules

- GT2871 fuel chips (`0x2EB58546`, `0xA77BB88E`) → **GT2871 boost** (`0x69156B3A`)  
- GT3071 fuel chips (`0x6F3AE675`, `0x07DA1752`, `0xB9F0FD51`) → **GT3071 boost** (`0x39DC67DA`)  
- GT28RS fuel chip (`0x9A8A6B4E`) → **GT2871 or GT3071 boost** (either)
- RS2 91Oct (`0x28C04D7B`) → **ABY boost** (`0xF6E33043`) or **ADU/RS2 boost** (`0x4EE87833`)
- Stock Rip Chip → **stock AAN boost** (`0x16707F66`)

### 893906266D 7A late ECU files (54903 bytes)

These two files use a different encoding (not standard 034 scramble, non-standard size). They are 7A Hitachi-ECU specific. Investigation needed: may be a different scramble version or a raw binary dump. The valid 65536-byte `893906266D` Turbo Stage 2 file (`0xA01C4EDA`) descrambles correctly.


## 034EFI Chipset Reference (2026-03)

### File Summary (from zip, 18 valid chips)

| Fuel CRC | Variant | Boost Chip Required | Specs |
|---|---|---|---|
| 0x956BFC9C | 551AA_0202 | 0x16707F66 (stock AAN) | Stock Rip Chip — no mods |
| 0xA47011AB | 551AA_0202 | 0x69156B3A or 0x39DC67DA | Stage 1+ K24: 3.0 BAR MAP, RS2 injectors, 20psi/14psi/7200rpm |
| 0x9A8A6B4E | 551AA_0202 | 0x69156B3A or 0x39DC67DA | GT28RS R2: 3.0 BAR MAP, 550cc, 27psi/16psi/7000rpm/285whp |
| 0x2EB58546 | 551AA_0202 | 0x69156B3A | GT2871 R9.1 550cc EV14: 26psi/22psi/7200rpm/330whp |
| 0xA77BB88E | 551AA_0202 | 0x69156B3A | GT2871 R9 440cc Siemens: 26psi/22psi/7200rpm/330whp |
| 0x6F3AE675 | 551AA_0202 | 0x39DC67DA | GT3071 R8 42lb: 26psi/23psi/7200rpm/346whp |
| 0x07DA1752 | 551AA_0202 | 0x39DC67DA | GT3071 R9.1 550cc 91Oct: 26psi/23psi/7200rpm |
| 0xB9F0FD51 | 551AA_0202 | 0x39DC67DA | GT3071 R9 440cc Siemens: 346whp |
| 0x28C04D7B | 551AA_0202 | 0xF6E33043 or 0x4EE87833 | RS2 91Oct: ABY/ADU application, 3.0 BAR MAP, 440cc |
| 0x69156B3A | 551AA_0202_boost | — | GT2871 Stage 1 boost, build 0x0054, 300kPa MAP |
| 0x39DC67DA | 551AA_0202_boost | — | GT3071 Stage 1 boost 26-23psi, build 0x0054, 300kPa MAP |
| 0x84B0504E | 7A_NA | — | 7A Big MAF 91Oct R2, ECU 893906266B (early 2-conn) |
| 0xC075767F | 7A_Stage1 | — | 7A Stage 1 91Oct R1, ECU 893906266B |
| 0xA01C4EDA | 7A_Turbo | — | 7A Turbo Stage 2 550cc, ECU 893906266D (late 4-conn) |
| 0x55177DDB | 7A_Turbo | — | 7A Turbo Kit Stage 1 R2, ECU 893906266B |
| 0x4818FA0B | AAH | — | AAH/AKH 12v V6 Stage 1+, MMS-200 ECU only, big bore MAF |

### Invalid Files (non-standard format)
- `893906266D - CQ Big MAF 91Oct R1` (2× copies): 54903 bytes, not standard 034 scramble

---

## 551A / 551AA / 551B / 551C — Trigger System RE (2026-03)

### Confirmed trigger system differences

| Variant | Part Number | Trigger | Build | Calibration |
|---------|-------------|---------|-------|-------------|
| 551A    | 4A0907551A  | Distributor (D02) | 0x0202 | Factory-blank (0x02) |
| 551AA   | 4A0907551AA | Cam + Hall sensor (D03+HS) | 0x0812 | Factory-blank (0x02) |
| 551B    | 895907551B  | Cam + Hall sensor (D01+HS) | varies | REAL calibration confirmed |
| 551C    | 8A0907551C  | Cam + Hall sensor (RS2 mods) | varies | REAL calibration confirmed |

### Firmware code island analysis (working half, 0x0000-0x2CFF)

| Variant | Code bytes | Reset vector | INT0 ISR | Notes |
|---------|-----------|--------------|----------|-------|
| 551A    | 6,376     | 0x0000→0x0400 | - | Distributor trigger |
| 551AA   | 10,292    | 0x0000→0x1297 | 0x0003→0x0438 | Cam trigger, reference tooth handling |
| 551B    | 2,594     | - | - | Cam trigger similar to 551AA |

551AA has ~60% more firmware code than 551A — the cam+Hall sensor trigger requires
significantly more code for reference tooth detection and phase synchronisation.

### Map layout: IDENTICAL across all stock variants

All four variants (551A, 551AA, 551B, 551C) share the **same calibration map addresses**:
- Fuel map: WH 0x2E17 (16×16)
- Ign maps 1-7: WH 0x30AC, 0x3263, 0x3387, 0x3598, 0x36BC, 0x380D, 0x3931

The trigger system change is purely in firmware code (0x0000-0x2CFF).
This confirms PRJmod/034EFI can use one XDF for all stock variants.

### PRJmod 551AA_0202 uses COMPLETELY DIFFERENT firmware and map addresses

**Stock chips (551A/AA/B/C):**
- Fuel map: WH 0x2E17  
- Code: LJMP-fill with islands throughout 0x0000-0x2CFF

**PRJmod chips (551AA_0202, 034EFI tunes):**
- Fuel map: WH 0x0E13 (completely different location)
- Code: Different firmware, maps occupy 0x0000-0x2CFF entirely

**DO NOT cross-load stock and PRJmod ROMs.** UrROM detects this via CRC32 and sets the correct variant automatically.

### Factory-erased calibration (551A and 551AA)

Both the 551A and 551AA chips in this collection have **blank (0x02) calibration areas**.
This is consistent with:
- The chips being used as PRJmod firmware base ROMs (vwnut8392 M232-Firmware project)
- Factory-erased state before tuning was applied
- The "034EFI Stock Rip Chip" (0x956BFC9C) is a **reconstructed** stock tune, not a direct chip read

The **only confirmed direct chip read with real stock calibration** is the ABY 551B chip (0xA98CB481).

---

## Boost Chip N75 Map — Real Data (ABY direct read, 2026-03)

The ABY 551B boost chip (0x3b_boost = `aby_boost_551aa.bin`, 32KB) N75 map at WH 0x2480 shows:

- Rows 0 (600 RPM): all zero — no wastegate duty at lowest RPM
- Row 9 (7200 RPM): 39–74% duty cycle (100→189 raw ÷ 255 × 100)
- Progressive increase: row 5 first shows significant duty (~6-42%)
- High-load cols show higher duty (expected for boost control)

**Axis confirmation:** Axes are NOT stored in a data table. Scan found no ascending
10-byte sequence matching [600,1000,1500...7200] × 40. The values are embedded in
the MCU code as immediate operands — requires Ghidra disassembly to extract.

The community estimate `[600, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 7200]`
is consistent with the N75 map data pattern (zero duty at low RPM, progressive increase).

**Boost pressure map at 0x2520:** Row 0 values 94-144 raw = 110-169 kPa absolute
(at 300kPa sensor: 94/255×300=110kPa, 144/255×300=169kPa). These represent
atmospheric plus a small boost at idle — consistent with stock operation.


---

## Trigger System Compatibility: 3B → AAN ECU Swap (RE analysis 2026-03)

### The question

> If you have a 3B engine with a distributor, then swap in an AAN/S2/RS2 ECU and
> complete coil-pack harness — can you use the 3B trigger/Hall sender signal with
> the AAN ECU firmware?

**Short answer: Not directly — but there are paths that work.**

---

### What the binary RE reveals

#### INT0 ISR structure comparison

Both the 3B (404AA) and late AAN (551AA) firmware have almost identical INT0 handler
prologues — they're clearly from the same Bosch MCU firmware generation:

```
3B   INT0 @ 0x0339:  c0 e0 c0 d0 | c0 83 e8 c0 e0 c0 82 90 a0 80 ...
551AA INT0 @ 0x0438: c0 e0 c0 d0 | 00 00 c0 83 e8 c0 e0 c0 82 90 a0 80 ...
                     ^^^^^^^^^^^    ^^^^^^^^
                     IDENTICAL      only 2 bytes different (2 NOPs in 551AA)
```

The reset handlers are **identically identical for the first 21 bytes**. These ECUs
are from the same firmware lineage — the M2.3 → M2.3.2 evolution.

#### Timer ISR: the coil driver output differs

This is the critical difference for coil pack vs distributor:

| Variant | Timer0 ISR | Output | Meaning |
|---------|-----------|--------|---------|
| 3B 404AA | `d2 93 32` = SETB P1.3, RETI | P1.3 | Distributor coil (single coil) |
| 551AA | `d2 eb 32` = SETB bit 0xEB, RETI | SFR 0xE8.3 | Coil pack driver (multi-coil) |

The 3B fires **one pin** (P1.3) which drives the single ignition coil through the
distributor cap/rotor. The 551AA fires a **different SFR bit** that drives the
multi-channel coil pack output circuit on the 551AA ECU's output stage hardware.

This is a hardware difference — the ECU PCBs have different output transistor
configurations. You cannot change this in firmware alone.

---

### Why the swap is not straightforward

#### Trigger signal incompatibility

| ECU variant | Expects | 3B distributor provides |
|-------------|---------|------------------------|
| 551A (early AAN) | Distributor Hall sender, 5 vanes | ✓ Compatible signal type |
| 551AA (late AAN) | Cam Hall sensor with **reference tooth** | ✗ No reference tooth |
| 551B/C (ABY/RS2) | Cam Hall + Hall sensor position ref | ✗ No cam sensor |

The reference tooth is the key: the cam trigger firmware (551AA, 551B, 551C) uses
a **missing vane** in the cam sensor wheel to identify cylinder 1. Without it, the
ECU cannot determine which cylinder to fire. The 3B distributor has no equivalent —
cylinder position is determined by where you physically position the cap.

#### ECU hardware output differences

Even if you solve the trigger signal, the 3B has one ignition output pin (P1.3)
while the coil-pack ECUs have 5 individual coil driver outputs (one per cylinder)
on completely different PCB circuits. A 3B ECU PCB **cannot drive coil packs**
without hardware modification.

---

### What actually works

#### Path 1: Keep the 3B distributor, use the early AAN ECU (551A)

The early AAN (551A, 4A0907551A) also uses a distributor with a Hall sender.
The **trigger signal concept is the same** as the 3B. However:
- The ECU connectors and pinouts are completely different (different connector)
- The firmware maps (calibration) are in a different part of ROM
- You'd still need full harness adaptation

**Verdict:** Feasible with harness adaptation, but you're still using a distributor —
you don't get coil packs.

#### Path 2: Add a cam sensor wheel to the 3B engine, use 551AA/551B ECU

The AAN and 3B share the same engine block design. You can:
1. Fit an AAN camshaft sprocket with the Hall sensor trigger wheel
2. Mount the Hall sensor from the AAN harness
3. Run the 551AA ECU and coil pack harness

The **calibration maps are identical in structure** (confirmed from our RE). The 3B
and AAN share the same basic fuel map layout (fuel @ WH 0x2E17). However:
- Different engine displacement = different VE curve
- Different turbo = different boost calibration
- You'd start from the 3B calibration values in an AAN-format ROM

**Verdict:** Mechanically feasible. Requires cam sensor wheel fabrication/sourcing and
full harness swap. The ECU firmware will work — the cam trigger code handles the
reference tooth correctly. Start with ABY calibration as baseline, retune for 3B spec.

#### Path 3: Standalone ECU (Megasquirt, Haltech, etc.)

Skip the M2.3.2 ECU entirely. Run a standalone that accepts any trigger pattern,
drives coil packs, and is fully configurable. No firmware RE or harness adaptation
needed. The 034EFI PRJmod system is essentially this for the AAN.

---

### Summary

The 3B Hall sender from the distributor **cannot be directly connected to an
AAN 551AA/551B/551C ECU** — the firmware expects a cam-mounted sensor with a
reference tooth, and the ECU PCB has different coil driver hardware.

The early AAN (551A) uses the same distributor Hall concept as the 3B, but the
ECU is completely different hardware — different connector, different PCB,
different pinout. Not a plug-and-play swap.

The cleanest path to coil packs with the AAN ECU is to add an AAN cam sensor
wheel to the 3B engine and run the full AAN trigger system.

