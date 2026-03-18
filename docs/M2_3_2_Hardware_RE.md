# Bosch Motronic M2.3 / M2.3.2 — Complete Hardware & Firmware Reference

*Compiled from: factory schematics (Y261 C27 020 / Y261 C20 232-5), direct chip reads,
PRJ GitHub source (github.com/prj/m232), S2Forum thread documentation, vwnut8392 patcher XDF,
binary analysis of all variants, and community reverse-engineering (2013–2026).*

---

## 1. OFFICIAL BOSCH SCHEMATICS

Two official Bosch schematic sheets cover the M2.3/M2.3.2 ECU:

| Drawing | Bosch Number | Title | Board |
|---------|-------------|-------|-------|
| Sheet 1 | Y261 C20 232-5 | M2.32/241 Zusatzplatte | Boost / MAP sub-board |
| Sheet 2 | Y261 C27 020   | M2.3.2 Grundplatte     | Main (motor) board    |

**Sheet 1 (Zusatzplatte / boost board)** contains:
- S700, S701, S702, S703 — the boost controller MCU and its support ICs
- MAP sensor interface circuitry
- The via routing for the R660/board-wire SD mod

**Sheet 2 (Grundplatte / main board)** contains:
- S251 (SAB80C535 CPU), S252 (address latch), S253 (EPROM socket ×2)
- S254, S255 — output driver stages
- S500/S501 — MAF signal conditioning
- S230, S460, S310 — ignition and injection output stages
- Complete connector pinout (55-pin edge connector, left and right columns)
- R660 location, R201 location, R201 value network

---

## 2. CPU — Siemens SAB80C535

The M2.3.2 motor chip CPU is the **Siemens SAB80C535** (schematic ref: S251, Grundplatte).

### Core Specs
- 8-bit Intel 8051-compatible architecture
- ROM-less variant (SAB80C515 with no internal ROM); EA pin tied LOW — all code from external EPROM
- 256 bytes internal RAM
- External program/data memory: up to 64KB each
- 8-bit ADC with 8 multiplexed inputs (Port 6 / AIN0–AIN7)
- Three 16-bit timer/counters (T0, T1, T2)
- Full-duplex UART
- 12 interrupt vectors, 4 priority levels
- Runs at 12 MHz (some late variants 16 MHz)
- Package: DIP-40 (confirmed from 3B board photos: SIEMENS BD26422, ©INTEL 90)

**Datasheet reference:** Infineon/Siemens D80515 (SAB80C515/80C535), v02.96

### Key SFRs

| SFR | Address | Function |
|-----|---------|----------|
| ADCON | `$D8h` | ADC Control (bit-addressable): channel select + start |
| ADDAT | `$D9h` | ADC Data Register: 8-bit result |
| DAPR  | `$DAh` | DAC/Reference Program Register |
| P6    | `$DBh` | Port 6: AIN0–AIN7 analog input |
| IRCON | `$C0h` | Interrupt Request Control |
| T2CON | `$C8h` | Timer 2 Control |
| XICON | `$C4h` | Extended Interrupt Control |
| P5    | `$A8h` | Port 5 / IE (interrupt enable) |

### ADCON Register Bits

| Bit | Name | Function |
|-----|------|----------|
| 7 | ADEX | Extended reference voltage range |
| 6 | ADCI | ADC conversion complete flag |
| 5–4 | — | Reserved |
| 3 | ADCS | Start conversion (write 1) |
| 2 | MX2 | Channel select bit 2 |
| 1 | MX1 | Channel select bit 1 |
| 0 | MX0 | Channel select bit 0 |

Init: `MOV ADCON, #C0h` (ADEX=1, no channel, no start)

### ADC Polling Subroutine (AAN 551AA — at `$1726`)

```asm
; Call: set DPTR = 0xBE00 + channel_number
; Returns: A = 8-bit ADC result (0–255)
$1726  SETB  $05h.2          ; mark conversion in progress
$1728  MOV   A, #F8h         ; preserve upper bits, clear channel
$172A  ANL   A, ADCON
$172C  ORL   A, DPL($82h)    ; DPL = channel select
$172E  MOV   ADCON, A        ; select channel + start (ADEX=1 triggers)
$1730  MOV   DAPR, #00h      ; full reference range
$1733  JB    ADCON.4, $1733  ; POLL: wait while ADCI=1
$1736  MOV   A, ADDAT        ; read result
$1738  JNB   $05h.2, $1726   ; loop if another conversion queued
$173B  RET
```

**ADC uses polling, not interrupt.** The IADC ISR vector at `$2080` is simply `RETI`.

---

## 3. ADC CHANNEL MAP

### Motor Chip (SAB80C535 / S251)

| Channel | Port Pin | ECU Pin | Function | Notes |
|---------|----------|---------|----------|-------|
| AIN0 | P6.0 | — | TPS | Throttle Position Sensor |
| AIN1 | P6.1 | — | UBAT | Battery voltage |
| AIN2 | P6.2 | 44 | IAT | Intake Air Temperature |
| AIN3 | P6.3 | 45 | ECT | Engine Coolant Temperature |
| AIN4 | P6.4 | 39 | Coding Plug Pin 2 | FREE ADC — used for ethanol/WB logging |
| **AIN5** | **P6.5** | — | **MAP input (SD mode)** | Stock: automatic transmission signal. After R660 removal + board wire: MAP sensor signal arrives here |
| AIN6 | P6.6 | 46 | Map switching (early) | Repurposed for wideband logging in prjmod |
| AIN7 | P6.7 | — | Lambda (S600 variant?) | Uncertain |

*Source: vwnut8392, S2Forum "Added feature Patcher for PRJmod" (2018–2022); confirmed via ROM disassembly.*

### Boost Chip MCU ADCs

| Channel | Function | Notes |
|---------|----------|-------|
| AIN2 | Tied to GND by CPU | **Cannot be used without lifting boost MCU** |
| AIN3 | TPS → RAM_6C | |
| AIN4 | IAT → RAM_69 | |
| AIN5 | ECT → RAM_6A | |
| AIN6 | Tied to GND by CPU | **Cannot be used without lifting boost MCU** |
| AIN7 | Altitude sensor → RAM_67 | |

**Critical:** AIN2 and AIN6 on the boost chip are hardware-grounded by the boost MCU itself.
All external sensor work in SD mode is done on the motor chip side — this is why the
R660 + board wire routes the MAP signal to AIN5 on the motor chip, not the boost chip.

---

## 4. INTERRUPT VECTOR TABLE (AAN 551AA)

All 12 SAB80C535 interrupt vectors confirmed. ISR dispatch table at `$2000` (16-byte slots):

| Vector | Address | Flag | Dispatch | ISR | Function |
|--------|---------|------|----------|-----|----------|
| 0 | `$0003` | IE0 | `$2000` → LJMP `$0438` | Background loop | Map lookups |
| 1 | `$000B` | TF0 | `$2010` → `D2 EB 32` | SETB+RETI | Timer 0 flag |
| 2 | `$0013` | IE1 | `$2020` → LJMP `$0202` | | |
| 3 | `$001B` | TF1 | `$2030` → LJMP `$03CA` | Crank sync ISR | Injection timing |
| 4 | `$0023` | RI+TI | `$2060` → LJMP `$5A39` | KWP1281 serial | Diagnostics |
| 5 | `$002B` | TF2+EXF2 | `$2070` inline | `INC $3Eh; CLR IRCON.6; RETI` | MAF integration |
| 6 | `$0043` | IADC | `$2080` → RETI | (ADC uses polling) | ADC not interrupt-driven |
| 7 | `$004B` | IEX2 | `$2090` → RETI | Disabled | |
| 8 | `$0053` | IEX3/CC0 | `$20A0` inline | | Compare 0 |
| 9 | `$005B` | IEX4/CC1 | `$20B0` → LJMP `$287F` | | Compare 1 |
| 10 | `$0063` | IEX5/CC2 | `$20C0` → LJMP `$006E` | | Compare 2 |
| 11 | `$006B` | IEX6/CC3 | `$20D0` → RETI | Disabled | |

### ISR Loop Architecture

Three concurrent execution loops:
1. **Background loop** (IE0 / `$0438`) — slow map lookups, stores results in RAM
2. **Crank-synchronous ISR** (TF1 / `$03CA`) — injection pulse width calc, ign timing
3. **Per-tooth ISR** (Timer2 / `$2070` inline) — MAF integration, ign event firing

Serial ISR (`$5A39`) handles KWP1281 diagnostic protocol.

---

## 5. MEMORY MAP (64KB working half)

| Region | WH Offset | Description |
|--------|-----------|-------------|
| Reset vector | `$0000` | LJMP to startup handler |
| Interrupt vectors | `$0000–$006F` | 12 vectors × 3 bytes each |
| Startup code | `$006E–$??` | Hardware init, peripheral setup |
| ISR dispatch table | `$2000–$20DF` | 16-byte slots per interrupt |
| Main code | `$0070–$~1FFF` | Firmware, subroutines |
| Calibration tables | `$0E13+` | Fuel, ignition, enrichment |
| ID string | `$7F00–$7F3F` | Bosch part number ASCII |
| Build number | `$3FFE–$3FFF` | Firmware version word |
| Calibration tag | `$7FFE–$7FFF` | Calibration version word |
| Checksum bytes | `$3FFA–$3FFB` | PRJmod checksum (NOT in stock ROM) |

---

## 6. CONFIRMED MAP ADDRESSES

### 551B / 551C (ABY S2 / ADU RS2) — from vwnut8392 XDF, verified on direct reads

All addresses are **working half (WH) offsets** (full-file addr minus 0x8000):

| Map | WH Address | Dimensions | Decode | Notes |
|-----|-----------|-----------|--------|-------|
| Fuel enrichment (main) | `0x0E13` | 16×16 | `1881.6/X` = AFR | Confirmed |
| Ignition map 1 (PT primary) | `0x30AC` | 16×16 | `X*0.75 - 22.5` = °BTDC | Confirmed |
| Ignition map 2 | `0x3263` | 16×16 | same | Confirmed |
| Ignition map 3 | `0x3387` | 16×16 | same | Confirmed |
| Ignition map 4 | `0x3598` | 16×16 | same | Confirmed |
| Ignition map 5 | `0x36BC` | 16×16 | same | Confirmed |
| Ignition map 6 | `0x380D` | 16×16 | same | Confirmed |
| Ignition map 7 | `0x3931` | 16×16 | same | Confirmed |
| Driving ignition (RS2 D02) | `0x1383` | 16×16 | `X*0.75 - 22.5` | From vwnut8392 XDF |
| Emergency ignition | `0x10A8` | 16×16 | raw | Knock fallback |
| Cold start enrichment | `0x0F91` | 6×6 | raw | Head temp × IAT |
| Lambda correction 1 | `0x1D1C` | 6×1 | raw | |
| Lambda correction 2 | `0x1D2A` | 6×1 | raw | |
| Idle RPM target | `0x1BAF` | 3×1 | raw | 730–880 RPM range |

### AAN 551AA — PRJ XDF (provisional, different codebase from ABY/ADU)

| Map | WH Address | Dimensions | Decode |
|-----|-----------|-----------|--------|
| Fuel enrichment | `0x0E13` | 16×16 | `1/(X/128)*14.7` = AFR |
| Ignition no-knock | `0x125F` | 16×16 | `X*0.6491 - 8.2186` = °BTDC |
| MAP target | `0x1600` | varies | |
| WGDC | `0x1800` | varies | |

**Note:** AAN 551AA uses a different decode formula for ignition (`×0.6491−8.2186`)
vs ABY/ADU (`×0.75−22.5`). Map addresses for AAN require direct chip read to confirm
independently — ABY addresses at 0x30AC etc are NOT confirmed for AAN.

### 551B/551C Boost Chip (32KB, from vwnut8392 XDF)

| Map | Address | Dimensions | Notes |
|-----|---------|-----------|-------|
| Map lookup table 1 | `0x2000` | 200×2 | |
| Boost target 1 | `0x2520` | 16×10 | "Traces constantly" |
| N75 duty cycle 1 | `0x2480` | 16×10 | |
| Characteristic map | `0x264B` | 10×8 | |
| Max boost pressure | `0x2A96` | 8×1 | |
| Map lookup table 2 | `0x6000` | 200×2 | Mirrored region |
| Boost target 2 | `0x6520` | 16×10 | "Same as 551AA S4/S6" |
| N75 duty cycle 2 | `0x6480` | 16×10 | "Same as 551AA S4/S6" |

---

## 7. ECU HARDWARE MODIFICATIONS

### 7a. SD MAP Sensor Upgrade (prjmod requirement)

Required for speed-density mode (MPXH6400A sensor):

1. Remove solder from via under `S900`/`C660` label on **BOOST board**
2. Remove solder from via to right of D232 (towards D235) on **MOTOR board**
3. Solder wire through both vias connecting boost board → motor chip processor
4. Remove resistor **R660** from motor board

The wire routes MAP sensor analog signal to **AIN5** (P6.5) on the motor chip SAB80C535.
Boost range: stock 200kPa → up to ~2.9 bar absolute with 400kPa sensor.

### 7b. AAN → RS2 ECU Conversion

Same hardware. Only differences:
- **R201**: AAN stock ≈ 6.15kΩ → RS2 requires **5.6kΩ 1%, 1206 SMD** (per PRJ, S2Forum 2013)
- MAP sensor: 200kPa stock → 300kPa (MPX4300 / 034EFI Rip Chip standard)
- Chip set: ADU 551C fuel/ign + RS2 boost chip

No other PCB changes exist between AAN and RS2. The ECU is identical hardware.

### 7c. MAP Sensor Options

| Sensor | Range | Application | Hardware mod |
|--------|-------|-------------|-------------|
| Bosch 0 280 142 xxx | 200kPa | Stock — MAF builds only | None |
| MPX4250AP | 250kPa | QLCC-era chips, ~1.5 bar gauge max | None (R201 if needed) |
| MPX4300 / Bosch 300kPa | 300kPa | 034EFI Rip Chip, AAN→RS2 | R201 swap to 5.6kΩ |
| MPXH6400A | 400kPa | prjmod SD standard | R660 removal + board wire |
| MPX6300 | 300kPa abs | Some aftermarket SD builds | Board wire only |

### 7d. Chip Socket Installation

De-solder original windowed EPROM chips, install DIP-28 turned-pin sockets.
Fuel/ign chip: 27C256 (32KB) or 27C512 (64KB doubled). Boost chip: 27C64 (8KB) or 27C256 (32KB).

---

## 8. PRJMOD FIRMWARE

### Overview

PRJmod (by "prj", github.com/prj/m232) is the primary open-source M2.3.2 firmware modification.
It converts the ECU from MAF-based fuelling to Speed Density (MAP sensor) and adds:
- LC (Launch Control) + NLS (No-Lift Shift)
- Wideband logging
- Expanded boost control

Target: AAN/ABY chip sets running build `0x0202`. Source is on GitHub.

### Checksum Algorithm (from M232csum.dll source — `TPM232Plugin.cpp`)

**For 32KB working half (prjmod ROMs):**
```
sum = 0
for i in range(0, 0x3FFA, 2):
    sum += wh[i] + wh[i+1]
sum += wh[0x3FFE] + wh[0x3FFF]  # build number included
sum += 0x01FE                     # constant offset
correction = (-sum) & 0xFFFF
wh[0x3FFA] = correction >> 8
wh[0x3FFB] = correction & 0xFF
```

Complement stored at `0x3FFC/0x3FFD`. Build number at `0x3FFE/0x3FFF` is INCLUDED in sum.

**Stock Bosch ROMs do NOT have a software checksum.** The bytes at `0x3FFA/0x3FFB` in stock
chips are part of the Bosch ASCII ID string. The `M232csum.dll` only applies to prjmod-modified ROMs.

### WinlogDriver Decode Formulas (from `WinlogDriver.cpp` source)

Live data stream (KWP1281 fast logging, byte offsets in data packet):

| Signal | Formula | Units |
|--------|---------|-------|
| N75 duty cycle | `buf[0] / 194.0 * 100` | % |
| MAP actual | `buf[2] / 255.0 * 5 / 1.03515625 * mapMult + mapOffset` | kPa |
| MAP target | `buf[3]` same | kPa |
| Knock 1–5 | `buf[4..8] * 0.5` | V |
| RPM | `buf[31] * 40` | RPM |
| Load | `(buf[30]*256 + buf[29]) / 25.0` | mg/stroke |
| UBATT | `buf[28] * 0.068` | V |
| IAT | `(buf[27] - 70) * 0.7` | °C |
| ECT | `(buf[26] - 70) * 0.7` | °C |
| TPS | `buf[25] * 0.416` | % |
| Ignition | `0.75 * (buf[24] - buf[23])` | °BTDC |
| IPW effective | `0.52 * (buf[22] + buf[21]/256.0)` | ms |
| Injector dead time | `buf[20] * 0.010667` | ms |
| VSS | `buf[19] * 2` | km/h |

### MAP Sensor Calibration Constants (in WinlogDriver)

| Sensor | mapMult | mapOffset |
|--------|---------|-----------|
| MPXH6400A (400kPa) | 82.61049 | 6.9558 |
| MPXH6300A (300kPa) | 62.89308 | 0.222013 |
| Bosch 300kPa | 65.88235 | -6.35764 |
| Bosch 250kPa | 50.0 | 10.0 |

### LC/NLS Scalars (prjmod `0x0202` firmware, WH offsets)

| Scalar | WH Offset | Stock Value | Decoded |
|--------|----------|-------------|---------|
| Hard RPM limit (spark cut) | `0x0617` | `0xC8` | 8000 RPM |
| LC RPM enable | `0x0620` | `0x02` | 4 km/h |
| LC ignition retard RPM | `0x0625` | `0x71` | 4520 RPM |
| LC ignition angle (ATDC) | `0x062B` | `0x29` | 41° ATDC |
| NLS minimum RPM | `0x063D` | `0xC8` | 8000 RPM |
| NLS ignition angle (ATDC) | `0x0643` | `0x52` | 82° ATDC |
| Spark cut knock-disable RPM | `0x064C` | `0xB1` | 7080 RPM |
| LC ignition cut RPM | `0x066E` | `0xB4` | 7200 RPM |
| AC idle target RPM | `0x03DF` | `0x4A` | 740 RPM |

RPM encoding: `byte × 40 = RPM` throughout LC/NLS code.
Ignition angles in LC/NLS are **ATDC** (not BTDC like map tables) — common confusion source.

### vwnut8392 Patcher Patches (target: 8A0 907 551B only)

1. **Wideband logging** (AIN6 / ECU pin 46):
   8051 patch: `90 BE 04 12 17 CF 22` — streams ADC4 raw value via KWP1281 logger
   Compatible with Zeitronix ZT-2/ZT-3 and any 0–5V wideband.

2. **NLS via coding plug pin 1** (instead of pin 2 + 12V):
   Requires clutch pedal brake-light switch.

3. **Race fuel MAP switching** via ECU pin 42 (BETA as of 2018):
   Grounding pin 42 switches to race fuel map.

4. **CEL as shift light**: ECU sinks CEL output. European cars may need harness wire added.

5. **AC idle fix**: RAM_20.6 flag. Restores in-drive idle map when A/C is on.

Patcher note: "Base data does not match" = bin was already modified; hand-port patches instead.

---

## 9. COMPLETE VARIANT REGISTRY

| Software ID | ECU PN | Trigger | Bosch ECU PN | ROM PN | Build | Cal tag | Boost | Reset |
|------------|--------|---------|-------------|--------|-------|---------|-------|-------|
| 551A | 4A0907551A | D02 (distributor) | 0261200465 | 1267356703 | 0x0202 | 0xA04A | 8KB | 0x117A |
| 551AA | 4A0907551AA | D03+HS (cam) | 0261200465 | 1267357391 | 0x0812 | 0x0202 | 32KB | 0x1297 |
| 551B | 895907551B | D01+HS (cam) | 0261203643 | 1267358375 | 0x0274 | 0x7F02 | 32KB | 0x1329 |
| 551B (D02) | 8A0907551B | D02 (distributor) | 0261203478 | 1267358289 | 0x0202 | 0xA1E9 | 32KB | 0x1329 |
| 551C | 8A0907551C | D01+RS2 (cam) | 0261203543 | 1267358668 | 0x0274 | 0x7F02 | 32KB | 0x1329 |
| 3B (404AA) | 447907404AA | distributor | 0261200451 | 1267356462 | 0xF004 | 0x029B | 8KB | 0x0F99 |
| RR (404B) | 857907404B | distributor | 0261200453 | 1267356261 | 0xF004 | 0x0253 | 8KB | 0x0F99 |

**Trigger code legend:**
- D01 = cam pulley hall sensor (Type 85 / B2 body variants)
- D02 = distributor hall sensor (early AAN + early RS2 + 3B/RR)
- D03 = cam pulley hall sensor (late AAN, Type 44 body)
- RS2 = explicit RS2 designation in descriptor field

**Firmware siblings:** 551B and 551C are byte-identical code — only calibration differs.
551B (D02) uses build 0x0202 shared with early 551AA/551A — likely same firmware version.

---

## 10. CRC32 FINGERPRINT TABLE

All CRCs are of the 32KB working half unless noted:

| CRC32 | Variant | Description |
|-------|---------|-------------|
| `0x4378E077` | 551C | ADU RS2 fuel/ign WH, 8A0907551C, ROM 1267358668 |
| `0x1529520A` | 551C | ADU RS2 fuel/ign full 64KB |
| `0x4EE87833` | 551c_boost | ADU RS2 boost 32KB, build 0x0202 |
| `0xA98CB481` | 551B | ABY S2 fuel/ign WH, 895907551B, ROM 1267358375 |
| `0x97D26DD1` | 551B | ABY S2 fuel/ign full 64KB |
| `0xF6E33043` | 551b_boost | ABY S2 boost 32KB, build 0x0202 |
| `0xB9A49F8A` | 551AA | AAN fuel/ign WH, 4A0907551AA, D03 cam trigger |
| `0x16707F66` | 551AA_boost | AAN boost 32KB, build 0x0202 |
| `0xF7432BB5` | 551A | AAN fuel/ign WH, 4A0907551A, D02 distributor |
| `0xBBAFE260` | 551a_boost | AAN boost 8KB, build 0xA04B |
| `0xC349075E` | 551B_D02 | RS2 D02 fuel/ign full 64KB, 8A0907551B, ROM 1267358289 |
| `0xC5B30158` | 551B_D02 | RS2 D02 fuel/ign WH |
| `0x288CBFBC` | 551B_D02_boost | RS2 D02 boost 32KB, build 0x0202 |
| `0x0AE3CACD` | 404 | 3B fuel/ign 32KB, 447907404AA, ROM 1267356462 |
| `0xFBE0A74A` | 404 | RR fuel/ign 32KB, 857907404B, ROM 1267356261 |
| `0xF50660DA` | 404_boost | 3B boost 8KB, build 0x0254 |
| `0xEA8D46DF` | 404_boost | RR boost 8KB, build 0x0255 |
