# Bosch Motronic M2.3.2 — Master Hardware & Firmware Reference

*Compiled from: factory schematics (Y261 C20/C27), PRJ m232 GitHub source,*  
*vwnut8392 S2Forum patcher thread, SAB80C535 datasheet, direct chip reads,*  
*PRJ MapFinder analysis, and binary RE sessions (March 2026)*

---

## 1. ECU BOARD ARCHITECTURE

The M2.3.2 ECU consists of **two separate PCBs** in a single enclosure:

### Grundplatte (Motor Board) — Y261 C27 020
The main engine management board. Contains:
- **S760** — Siemens SAB80C535 CPU (motor chip processor)
- **S700/S701** — EPROM sockets (fuel/ignition chip, DIP-28)
- **S252/S251** — Memory decode / address latch
- **S255** — Analog input conditioning, AIN0–AIN7
- **S230** — Signal conditioning for crank/cam trigger
- **S500/S501** — Injector driver outputs
- **S460** — Knock sensor demodulation (S460)
- **R660** — Resistor isolating MAP sensor analog path (removed for SD mode)
- **R201** — Calibration resistor (5.6 kΩ 1% 1206 for RS2; ~6.15 kΩ stock AAN)

### Zusatzplatte (Boost Board) — Y261 C20 232-5V
The auxiliary boost control board (piggyback sub-PCB). Contains:
- **S760** — Second 8051-family processor (boost chip processor)
- **S702** — EPROM socket (boost chip, DIP-28)
- **S703** — Boost-side MAP sensor / pressure transducer
- **S100** — MAP sensor signal conditioning op-amps
- **S500** — Boost board output buffer
- **N701/N700** — Address/data latches for inter-board communication
- **MAP sensor port** — Vacuum nipple on ECU case for manifold pressure

The two boards communicate via a shared external memory bus. The fuel chip CPU
reads boost sensor data by performing MOVX (external memory read) operations
targeting the inter-board registers.

**Inter-board IPC registers (fuel CPU ↔ boost CPU):**
- `0xA040` — primary IPC register (MOVX target, 8+ references in fuel chip code)
- `0xA080` — secondary IPC register (MOVX target, 8+ references)

---

## 2. CPU — Siemens SAB80C535

### Identification
The Bosch M2.3.2 uses the **Siemens SAB80C535** — the ROM-less (mask ROM = 0)
variant of the SAB80C515C. On the 3B/RR board, the Bosch internal PN is
**BD26422** (DIP-40, ©INTEL 90, 9006 date code) — same Intel 8051 architecture,
Siemens fabrication, custom Bosch label.

### Key specifications
| Feature | Value |
|---|---|
| Architecture | Intel MCS-51 (8051) compatible |
| Internal ROM | 0 bytes (EA pin tied low — all code from external EPROM) |
| Internal RAM | 256 bytes |
| External memory | Up to 64KB program + 64KB data |
| ADC | 8-channel, 8-bit, on-chip (Port 6 / AIN0–AIN7) |
| Timers | 3 × 16-bit (T0, T1, T2) + compare/capture unit |
| Interrupt vectors | 12 (including 4 compare/capture INT2–INT5) |
| Serial port | Full-duplex UART |
| Clock | 12 MHz (some variants 16 MHz) |
| Package | DIP-40 or QFP-44 |

### ADC SFRs
| SFR | Hex address | Function |
|---|---|---|
| ADCON | `D8h` | ADC Control (bit-addressable): channel select + conversion trigger |
| ADDAT | `D9h` | ADC Data Register: 8-bit result (0–255) |
| DAPR | `DAh` | DAC/Reference Program Register |
| P6 | `DBh` | Port 6: AIN0–AIN7 analog input port |

**ADCON bit map:**

| Bit | 7 | 6 | 5–4 | 3 | 2 | 1 | 0 |
|---|---|---|---|---|---|---|---|
| Name | ADEX | ADCI | — | ADCS | MX2 | MX1 | MX0 |
| Function | Extended ref | Conv. done flag | Reserved | Start conv. | Channel bit 2 | Channel bit 1 | Channel bit 0 |

### ADC Polling Subroutine — `$1726` (AAN 551AA)
All ADC reads route through one shared subroutine. Channel number passed in DPL:
```asm
$1726  SETB  $05h.2       ; Set "conversion in progress" flag  
$1728  MOV   A, #F8h      ; Mask: preserve ADEX+ADCI, clear channel bits  
$172A  ANL   A, ADCON     ; Read-modify-write ADCON  
$172C  ORL   A, DPL       ; OR in DPL = channel select (0–7)  
$172E  MOV   ADCON, A     ; Write: select channel, ADEX=1  
$1730  MOV   DAPR, #00h   ; Full reference range  
$1733  JB    ADCON.4,$1733 ; POLL: spin while conversion in progress (ADCI=1)  
$1736  MOV   A, ADDAT     ; READ result into A  
$1738  JNB   $05h.2,$1726 ; Loop if another channel queued  
$173B  RET
```
**The ADC uses polling, not interrupts.** The IADC vector (`$2080`) simply
contains `RETI`.

---

## 3. ADC CHANNEL MAP

### Motor board (fuel chip processor)
Source: vwnut8392, S2Forum "Added feature Patcher for PRJmod" thread + firmware RE

| Channel | ECU Pin | Signal | Notes |
|---|---|---|---|
| AIN0 | — | TPS | Throttle Position Sensor |
| AIN1 | — | UBAT | Battery voltage |
| AIN2 | Pin 44 | IAT | Intake Air Temperature (NTC) |
| AIN3 | Pin 45 | ECT | Engine Coolant Temperature (NTC) |
| AIN4 | Pin 39 | Coding Plug Pin 2 | **FREE ADC** — used for ethanol sensor / extra logging |
| **AIN5** | — | **MAP sensor input** | **Stock: auto-trans signal. SD mode: MAP sensor arrives here after R660 removal + inter-board wire** |
| AIN6 | Pin 46 | Map switch / WB | Early ECUs: race fuel map switch. PRJmod repurposed for wideband 0–5V logging |
| AIN7 | — | Lambda (S600?) | Possibly lambda sensor on certain variants |

### Boost board processor ADC
| Channel | Signal | Notes |
|---|---|---|
| AIN2 | **Tied to GND by CPU** | Cannot be activated without lifting boost processor |
| AIN3 | TPS → RAM_6C | Boost CPU reads TPS |
| AIN4 | IAT → RAM_69 | Boost CPU reads IAT |
| AIN5 | ECT → RAM_6A | Boost CPU reads ECT |
| AIN6 | **Tied to GND by CPU** | Cannot be activated without lifting boost processor |
| AIN7 | Altitude sensor → RAM_67 | Barometric correction |

---

## 4. INTERRUPT VECTOR TABLE (AAN 551AA)

All 12 SAB80C535 vectors confirmed. ISR dispatch table at `$2000`:

| Vector | Address | Source | Dispatch | Notes |
|---|---|---|---|---|
| 0 | `$0003` | INT0 (external) | `$2000` → LJMP `$0438` | Background loop entry |
| 1 | `$000B` | Timer 0 | `$2010` → SETB+RETI | Fast timer tick |
| 2 | `$0013` | INT1 (external) | `$2020` → LJMP `$0202` | |
| 3 | `$001B` | Timer 1 | `$2030` → LJMP `$03CA` | Crank sync / injection |
| 4 | `$0023` | Serial (RI+TI) | `$2060` → LJMP `$5A39` | KWP1281 diagnostics |
| 5 | `$002B` | Timer 2 | `$2070` → inline | MAF integration / ignition fire |
| **6** | **`$0043`** | **ADC complete** | **`$2080` → RETI** | **ADC uses polling, not ISR** |
| 7 | `$004B` | INT2 (CC0) | `$2090` → RETI | Disabled |
| 8 | `$0053` | INT3 (CC1) | `$20A0` → inline | |
| 9 | `$005B` | INT4 (CC2) | `$20B0` → LJMP `$287F` | |
| 10 | `$0063` | INT5 (CC3) | `$20C0` → LJMP `$006E` | |
| 11 | `$006B` | INT6 (CC4) | `$20D0` → RETI | Disabled |

### Three-loop execution model
1. **Background loop** — INT0 ISR at `$0438`: map lookups, sensor reads, stores results in RAM
2. **Crank-sync loop** — Timer1 ISR at `$03CA`: injection pulse width calculation, ignition timing
3. **Per-tooth loop** — Timer2 ISR at `$2070` (inline): MAF integration, ignition event firing
4. **Serial ISR** at `$5A39`: KWP1281 diagnostic protocol handler

---

## 5. MEMORY MAP

| Region | Fuel chip address | Contents |
|---|---|---|
| Interrupt vectors | `$0000–$006F` | LJMP to dispatch table |
| ISR dispatch | `$2000–$20DF` | 16-byte slots |
| Main code | `$0070–$FFFF` | Firmware + calibration |
| ID string | `$FF00–$FF3F` | Bosch part number ASCII |
| Build number | `$BFFE` (WH `$3FFE`) | Firmware build identifier |
| Cal/checksum tag | `$FFFE` (WH `$7FFE`) | Calibration tag |

The 64KB physical EPROM file is a **doubled 32KB working half** — the upper
32KB (`$8000–$FFFF`) is the active program space; the lower 32KB is a mirror.
On the 3B/RR ECU, the fuel chip is a **flat 32KB** file with no doubling.

---

## 6. FACTORY SCHEMATIC REFERENCE (Y261 C20 / Y261 C27)

Two-sheet official Bosch schematics archived as `M2_3_2_Schematics.pdf`:

**Sheet 1 — Zusatzplatte (Y261 C20 232-5V):** Boost board  
**Sheet 2 — Grundplatte (Y261 C27 020):** Motor/fuel board

Key reference designators:
| Ref | Board | Function |
|---|---|---|
| S760 | Motor | SAB80C535 / BD26422 — main CPU |
| S700, S701 | Motor | EPROM sockets — fuel/ignition chip |
| S702 | Boost | EPROM socket — boost chip |
| S252 | Motor | Address decoder |
| S251 | Motor | Data latch / bus buffer |
| S255 | Motor | Analog input conditioning (AIN lines) |
| S230 | Motor | Crank/cam trigger signal conditioning |
| S500/S501 | Motor | Injector driver outputs |
| S460 | Motor | Knock sensor demodulation |
| S100 | Boost | MAP sensor op-amp conditioning |
| S703 | Boost | MAP sensor / pressure transducer |
| N701/N700 | Boost | Inter-board address/data latches |
| R660 | Motor | MAP sensor isolation (remove for SD mod) |
| R201 | Motor | Calibration resistor (5.6 kΩ RS2; ~6.15 kΩ AAN) |

---

## 7. CONFIRMED MAP ADDRESSES (MapFinder + direct binary verification)

MapFinder (PRJ, 2012) locates maps by header descriptor. Header address + 36 bytes = data start.
All addresses are **Working Half (WH) offsets** within the 32KB active program space.

### AAN 551AA (4A0907551AA, D03 cam trigger)
MapFinder output: 295 tables found. 8 × 16x16 maps:

| Map | Header WH | Data WH | PRJ XDF | Likely function |
|---|---|---|---|---|
| 1 | `0x0DEA` | `0x0E0E` | `0x0E13` ≈ | Fuel enrichment (primary P/T) |
| 2 | `0x106D` | `0x1091` | — | Fuel map 2 |
| 3 | `0x1224` | `0x1248` | `0x125F` ≈ | Ignition no-knock (primary) |
| 4 | `0x1348` | `0x136C` | `0x1384` ≈ | Ignition map 2 |
| 5 | `0x155F` | `0x1583` | `0x1598` ≈ | Ignition map 3 |
| 6 | `0x1683` | `0x16A7` | — | Ignition map 4 |
| 7 | `0x17D4` | `0x17F8` | `0x1810` ≈ | Ignition map 5 |
| 8 | `0x18F8` | `0x191C` | `0x1934` ≈ | Ignition map 6 |

*PRJ XDF addresses differ by ~20 bytes from MapFinder+36. PRJ addresses are confirmed
against real ROM data; the ~20 byte gap may reflect a different axis encoding.*

### ABY 551B / ADU 551C (895907551B, D01 cam trigger)
MapFinder output: 294 tables found. 8 × 16x16 maps:

| Map | Header WH | PRJ XDF (data) | Confirmed |
|---|---|---|---|
| Fuel 1 | `0x2E17` | `0x2E17` | ✓ exact |
| Ign 1 | `0x30A8` | `0x30AC` | ✓ +4 |
| Ign 2 | `0x325F` | `0x3263` | ✓ +4 |
| Ign 3 | `0x3383` | `0x3387` | ✓ +4 |
| Ign 4 | `0x3594` | `0x3598` | ✓ +4 |
| Ign 5 | `0x36B8` | `0x36BC` | ✓ +4 |
| Ign 6 | `0x3809` | `0x380D` | ✓ +4 |
| Ign 7 | `0x392D` | `0x3931` | ✓ +4 |

*ABY/ADU fuel map header IS the data start. Ign map headers are 4 bytes before data.*
*These addresses confirmed by both MapFinder and direct chip binary reads.*

### 3B / RR 404 (447907404AA, 857907404B, distributor trigger)
MapFinder output: 258 tables found. 11 × 16x16 maps (4 fuel + 7 ignition):

| Map | Header WH | PRJ header | Δ | Data WH |
|---|---|---|---|---|
| Fuel 1 | `0x6A8E` | `0x6A6A` | +36 | `0x6AB2` |
| Fuel 2 | `0x6C1C` | `0x6BF8` | +36 | `0x6C40` |
| Fuel 3 | `0x6D74` | `0x6D50` | +36 | `0x6D98` |
| Fuel 4 | `0x6E98` | `0x6E74` | +36 | `0x6EBC` |
| Ign 1 | `0x7076` | — | — | `0x709A` |
| Ign 2 | `0x71F8` | — | — | `0x721C` |
| Ign 3 | `0x731C` | — | — | `0x7340` |
| Ign 4 | `0x7440` | — | — | `0x7464` |
| Ign 5 | `0x7667` | `0x7643` | +36 | `0x768B` |
| Ign 6 | `0x77CF` | `0x77AB` | +36 | `0x77F3` |
| Ign 7 | `0x7937` | `0x7913` | +36 | `0x795B` |

*3B PRJ header addresses differ by exactly +36 bytes — MapFinder points to the
descriptor, PRJ points to descriptor+36 (the actual 16x16 data block). Consistent.*

---

## 8. CHECKSUM ALGORITHM (M232csum.dll — from PRJ GitHub source)

Source: `src/M232csum/M232csum/TPM232Plugin.cpp` (PRJ m232 GitHub repository)

### 32KB working half (fuel/ign chip)
```
Loop bytes[0x0000 .. 0x3FF9] in pairs:
  si += byte[i]      (even addresses: 0, 2, 4 … 0x3FF8)
  di += byte[i+1]    (odd addresses:  1, 3, 5 … 0x3FF9)
cx = (si + di + wh[0x3FFE] + wh[0x3FFF] + 0x01FE) & 0xFFFF
Store: wh[0x3FFA] = cx >> 8    (hi byte)
       wh[0x3FFB] = cx & 0xFF  (lo byte)
```

**Checksum location: WH bytes `0x3FFA` and `0x3FFB`**  
The build number at `0x3FFE/0x3FFF` is included as a constant in the sum.  
`0x01FE` is a fixed bias term.

**Stock Bosch ROMs have NO computed checksum.** The bytes at `0xFF00/0xFF01`
in 64KB files are the start of the ASCII ID string (`To8A0907551C…`) —
not checksum bytes. Stock ROMs rely on EPROM read-back verification only.
PRJmod ROMs use M232csum.dll to compute and embed the checksum.

### 64KB doubled file (551x chips)
```
Sum bytes[eax+2 .. 0xFEFF] in pairs (similar accumulator loop)
Store: [0xFF00] = hi, [0xFF01] = lo
```

---

## 9. PRJmod FIRMWARE (speed-density conversion)

PRJmod by PRJ (prj-tuning.com) converts the stock MAF-based M2.3.2 to
speed-density (MAP sensor based). Requires hardware modifications.

### Required hardware modifications
1. **Remove R660** from motor board
2. **Solder inter-board wire** — from boost board MAP sensor output via through
   motor board via to AIN5 (AN5) of the motor chip processor
3. **Install MPXH6400A** (400 kPa) MAP sensor in place of stock 200 kPa sensor
4. **Burn PRJmod firmware** — 0x0202 build, replaces MAF calculation with VE table lookup

### WinlogDriver decode formulas (from PRJ m232 GitHub source)
```
N75 duty cycle:  buf[0]  / 194.0 * 100  (%)
MAP sensor:      buf[2]  / 255.0 * 5 / 1.03515625 * mapMult + mapOffset  (kPa)
MAP requested:   buf[3]  — same formula
Knock sensors:   buf[4..8] * 0.5  (each channel)
RPM:             buf[31] * 40
Load (16-bit):   (buf[30]*256 + buf[29]) / 25.0
Battery voltage: buf[28] * 0.068
IAT:             (buf[27] - 70) * 0.7  (°C)
ECT:             (buf[26] - 70) * 0.7  (°C)
TPS:             buf[25] * 0.416  (%)
Ignition:        0.75 * (buf[24] - buf[23])  (°BTDC)
Inj pulse width: 0.52 * (buf[22] + buf[21]/256)  (ms)
Injector deadtime: buf[20] * 0.010667  (ms)
VSS:             buf[19] * 2  (km/h)
```

### MAP sensor calibration constants (MPXH6400A — PRJmod default)
| Sensor | mapMult | mapOffset |
|---|---|---|
| MPXH6400A | 82.61049 | 6.9558 |
| MPXH6300A | 62.89308 | 0.222013 |
| Bosch 300 kPa | 65.88235 | −6.35764 |
| Bosch 250 kPa | 50.0 | 10.0 |

### PRJmod patches (vwnut8392 patcher, target: 8A0907551B only)
1. **Wideband logging** (pin 46 / AIN6): replaces MAF bytes in logging stream with ADC4 raw
   8051 patch bytes: `90 BE 04 12 17 CF 22`
2. **NLS via coding plug pin 1**: clutch pedal brake switch (open=up, GND=pressed)
3. **Race fuel MAP switch** via ECU pin 42 (BETA)
4. **CEL as shift light**: CEL = grounding output; threshold ~300 RPM below shift point
5. **AC idle fix**: RAM_20.6 flag restores in-drive idle map when AC on

### LC/NLS scalars (WH `0x0610`, prjmod 551AA 0x0202 firmware)
| Scalar | WH offset | Stock value | Decoded |
|---|---|---|---|
| Hard RPM limit (spark cut) | `0x0617` | `0xC8` | 8000 RPM |
| Speed threshold for LC | `0x0620` | `0x02` | 4 km/h |
| LC ignition retard RPM | `0x0625` | `0x71` | 4520 RPM |
| LC ignition angle (ATDC) | `0x062B` | `0x29` | 41° ATDC |
| NLS minimum RPM | `0x063D` | `0xC8` | 8000 RPM |
| NLS ignition angle (ATDC) | `0x0643` | `0x52` | 82° ATDC |
| Spark cut knock-disable RPM | `0x064C` | `0xB1` | 7080 RPM |
| LC ignition cut RPM | `0x066E` | `0xB4` | 7200 RPM |
| AC idle target RPM | `0x03DF` | `0x4A` | ×10 → 740 RPM |

*RPM encoding throughout LC/NLS: byte × 40 = RPM*
*Ignition angles in LC/NLS are ATDC (not BTDC like the map tables)*

---

## 10. ECU VARIANT SUMMARY

| Variant | ECU PN | Trigger | Bosch ECU PN | ROM PN | Build | Boost chip |
|---|---|---|---|---|---|---|
| 551A | 4A0907551A | D02 — distributor hall | 0261200465 | 1267356703 | 0x0202 | 8KB |
| 551AA | 4A0907551AA | D03+HS — cam pulley | 0261200465 | 1267357391 | 0x0812 | 32KB |
| 551B | 895907551B | D01+HS — cam pulley | 0261203643 | 1267358375 | 0x0274 | 32KB |
| 551C | 8A0907551C | D01+RS2 — cam pulley | 0261203543 | 1267358668 | 0x0274 | 32KB |
| 404AA | 447907404AA | Distributor (3B/200) | 0261200451 | 1267356462 | 0xF004 | 8KB |
| 404B | 857907404B | Distributor (RR/UrQ) | 0261200453 | 1267356261 | 0xF004 | 8KB |

**551B and 551C are firmware siblings** — zero code diff, calibration only.  
**551A and 551AA share firmware build 0x0202** but differ in trigger system code.  
**3B/RR share firmware build 0xF004** but differ in calibration.

---

## 11. MAPFINDER TOOL

`mapfinder.jar` by PRJ (2012) — locates map headers in M2.3 ROM files.
Requires Java 8 (JAXB removed in Java 9+):
```bash
/usr/lib/jvm/java-8-openjdk-amd64/jre/bin/java -jar mapfinder.jar input.bin > output.xdf
```

Generated XDF files for all variants are in `docs/`:
- `mapfinder_aby_551b.xdf` — 294 tables (ABY 551B)
- `mapfinder_aan_551aa.xdf` — 295 tables (AAN 551AA)
- `mapfinder_3b_404aa.xdf` — 258 tables (3B 404AA)

---

## 12. SOURCES AND ATTRIBUTION

| Source | Content |
|---|---|
| Robert Bosch GmbH — Y261 C20 / Y261 C27 schematics | Official factory ECU schematics |
| Siemens / Infineon D80515 datasheet | SAB80C535 register map, ADC SFRs |
| PRJ (prj-tuning.com) — m232 GitHub | M232csum.dll source, WinlogDriver.cpp formulas, MapFinder.jar |
| PRJ — m232.xdf | AAN/ABY map addresses (136 tables confirmed) |
| vwnut8392 — S2Forum patcher thread | ADC pin map, R201 value, patch byte sequences |
| S2Forum m232.org subforum | LC/NLS scalars, boost chip interaction, community RE |
| Direct chip reads (2026-03 RE session) | CRC fingerprints, ID strings, firmware diffs |
| MapFinder analysis (2026-03) | Map header addresses for AAN, ABY, 3B variants |
