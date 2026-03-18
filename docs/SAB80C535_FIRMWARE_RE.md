# M2.3.2 Firmware RE — SAB80C535 Architecture

*Initial RE session — March 2026*  
*ROM: `aan_fuel-ign_551aa.bin` (AAN, 4A0907551AA, 65535 bytes)*

---

## CPU: Siemens SAB80C535

The Bosch Motronic M2.3.2 uses the **Siemens SAB80C535** — the ROM-less
variant of the SAB80C515. EA pin is tied low (all code from external EPROM).

- 8-bit 8051-compatible architecture
- 256 bytes internal RAM, 0 bytes internal ROM
- External program/data memory up to 64KB each
- 12 interrupt vectors, 4 priority levels
- 8-bit ADC with 8 multiplexed inputs (Port 6 / AIN0–AIN7)
- Three 16-bit timer/counters
- Full-duplex serial port
- 12 MHz / 16 MHz variants used in production ECUs

**Datasheet:** Infineon D80515 (SAB80C515/80C535) v02.96

---

## Memory Map

The 65KB ROM file (`aan_fuel-ign_551aa.bin`) is the full program memory image
loaded at address `$0000–$FFFF`. The ECU has no internal ROM; EA is tied low.

| Region | Address | Contents |
|--------|---------|----------|
| Interrupt vectors | `$0000–$006F` | LJMP instructions to ISR dispatch table |
| ISR dispatch table | `$2000–$20DF` | 16-byte slots for each ISR (LJMP or RETI) |
| Main code | `$0070–$FFFF` | Firmware, calibration tables, map data |
| Calibration tables | Various | Fuel, ignition, enrichment, temp correction |

---

## Interrupt Vectors

All 12 SAB80C535 interrupt vectors confirmed from ROM. Each points into
an ISR dispatch table at `$2000` with 16-byte slots:

| Vector | Address | Flag | Source | Dispatch | Real ISR |
|--------|---------|------|--------|----------|----------|
| 0 | `$0003` | IE0 | External INT0 | `$2000` → LJMP `$0438` | Background loop |
| 1 | `$000B` | TF0 | Timer 0 overflow | `$2010` → `$D2 EB 32` | SETB + RETI |
| 2 | `$0013` | IE1 | External INT1 | `$2020` → LJMP `$0202` | |
| 3 | `$001B` | TF1 | Timer 1 overflow | `$2030` → LJMP `$03CA` | Crank sync |
| 4 | `$0023` | RI+TI | Serial port | `$2060` → LJMP `$5A39` | KWP1281 serial |
| 5 | `$002B` | TF2+EXF2 | Timer 2 | `$2070` → inline | `INC $3Eh; CLR IRCON.6; RETI` |
| **6** | **`$0043`** | **IADC** | **A/D converter** | **`$2080` → RETI** | **ADC uses POLLING** |
| 7 | `$004B` | IEX2 | External INT2 | `$2090` → RETI | Disabled |
| 8 | `$0053` | IEX3/CC0 | INT3/Compare0 | `$20A0` → inline | |
| 9 | `$005B` | IEX4/CC1 | INT4/Compare1 | `$20B0` → LJMP `$287F` | |
| 10 | `$0063` | IEX5/CC2 | INT5/Compare2 | `$20C0` → LJMP `$006E` | |
| 11 | `$006B` | IEX6/CC3 | INT6/Compare3 | `$20D0` → RETI | Disabled |

**Key:** The IADC (ADC complete) ISR at `$2080` is simply `RETI` — the ADC
is driven by **polling**, not interrupts. This matches the 7A ECU approach.

---

## ADC Interface

The SAB80C535 has an internal 8-bit ADC with 8 multiplexed inputs on Port 6.
Unlike the 7A ECU (which uses an external memory-mapped ADC), the M2.3.2 uses
the **on-chip ADC** via dedicated SFRs.

### Key ADC SFRs

| SFR | Address | Function |
|-----|---------|----------|
| ADCON | `$D8h` | ADC Control (bit-addressable): channel select + control flags |
| ADDAT | `$D9h` | ADC Data Register: 8-bit result (0–255) |
| DAPR | `$DAh` | DAC/Reference Program Register: internal reference voltage |
| P6 | `$DBh` | Port 6: AIN0–AIN7 analog input port |

### ADCON Register Bit Map

| Bit | Name | Function |
|-----|------|---------|
| 7 | ADEX | Extended reference voltage range (1 = enabled) |
| 6 | ADCI | ADC interrupt flag / conversion done |
| 5–4 | — | Reserved |
| 3 | ADCS | Start conversion (write 1 to start) |
| 2 | MX2 | Channel select bit 2 |
| 1 | MX1 | Channel select bit 1 |
| 0 | MX0 | Channel select bit 0 |

### ADC Polling Subroutine — `$1726`

All ADC reads go through a shared subroutine. Channel is passed via DPTR:

```asm
; Call convention: set DPTR = 0xBE00 + channel_number before LCALL
; Result returned in A (0–255)
$1726  SETB  $05h.2          ; Set "conversion in progress" software flag
$1728  MOV   A, #F8h         ; Mask: preserve ADEX+ADCI+reserved, clear channel
$172A  ANL   A, ADCON        ; Read ADCON, keep upper bits
$172C  ORL   A, DPL($82h)    ; OR in DPL = channel select bits (from DPTR low byte)
$172E  MOV   ADCON, A        ; Write: select channel + trigger conversion (ADEX=1)
$1730  MOV   DAPR, #00h      ; Full reference range (0V–VAREF)
$1733  JB    ADCON.4, $1733  ; POLL: spin while ADCI=1 (conversion in progress)
$1736  MOV   A, ADDAT        ; READ: 8-bit result into A
$1738  JNB   $05h.2, $1726   ; If another channel queued, loop
$173B  RET
```

**Encoding:** Channel number is the low byte of DPTR (DPL = SFR `$82h`).
Writing to ADCON with ADEX=1 triggers conversion. ADCI is set during
conversion and cleared when done — `JB ADCON.4` spins while busy.

### ADC Initialisation

At reset (`$13F9` and `$3A96`):
```asm
MOV ADCON, #C0h   ; ADEX=1, ADCI=1 (extended ref, interrupt flag clear)
                   ; No channel, no start — init only
```

---

## ADC Channel Map (AAN 551AA — Preliminary)

From analysis of all 12 call sites to the ADC subroutine at `$1726`:

| Channel | Pin | Call Sites | Evidence | Likely Sensor |
|---------|-----|-----------|---------|--------------|
| AIN0 | P6.0 | `$124E`, `$579C` | Plausibility checks (0x06/0xFC) | TPS — confirmed vwnut8392 |
| **AIN1** | P6.1 | `$169A`, `$2436`, `$2509` | Most frequent; table lookup at `$2190` | **UBAT (battery voltage)** — confirmed vwnut8392 |
| AIN2 | P6.2 | `$1264` | Plausibility check | IAT (Intake Air Temp, pin 44) — confirmed |
| AIN3 | P6.3 | `$1259` | Plausibility check | ECT (Coolant Temp, pin 45) — confirmed |
| **AIN4** | P6.4 | `$4E5C` | 8-entry NTC binary search `$4E88` | **Free ADC** — coding plug pin 2, ECU pin 39 |
| **AIN5** | P6.5 | `$16A6` | Stored to xRAM page 4 | **MAP sensor input (SD mode)** — SD inter-board wire target |
| AIN6 | P6.6 | Pin 46 | Early: map switch | WB logging input (vwnut8392 patch) |
| AIN7 | P6.7 | — | Lambda (S600 variant) | Preliminary |

**Note:** The M2.3.2 MAP sensor is **internal to the ECU board** (on-board
pressure transducer, accessed via vacuum port on the ECU case). It may use
a separate ADC path or a dedicated hardware interface — not seen on Port 6.

### ECT Table at `$4E88h` (AIN4 result used as index)

8-entry descending voltage table for binary search — characteristic of NTC
temperature sensor linearisation. Values span 5.00V (sensor open) to 0.61V
(max temp):

```
FF DC CD A9 85 66 3D 32  (5.00V → 4.31V → 4.02V → 3.31V → 2.61V → 2.00V → 1.20V → 0.98V)
```

---

## ISR Architecture — Loop Model

The S2Forum thread confirmed the M2.3.2 runs three concurrent loops:

1. **Background loop** (IE0 ISR at `$0438`) — map lookups, stores results in RAM
2. **Crank-synchronous loop** (TF1 ISR at `$03CA`) — injection pulse width
   calculation, ignition timing conversion
3. **Per-tooth loop** (Timer2 ISR at `$2070` inline) — MAF integration,
   ignition event firing

The serial ISR at `$5A39` handles KWP1281 diagnostic communication.

---

## Status

| Task | Status |
|------|--------|
| CPU identified: Siemens SAB80C535 | ✓ Confirmed |
| Interrupt vector table | ✓ All 12 confirmed |
| ADC SFR interface (ADCON/ADDAT) | ✓ Confirmed |
| ADC polling subroutine at `$1726` | ✓ Fully traced |
| ADC channel encoding via DPL | ✓ Confirmed |
| AIN0 = TPS | ✓ Confirmed (vwnut8392) |
| AIN1 = UBAT (battery voltage) | ✓ Confirmed (vwnut8392) — not MAF |
| AIN2 = IAT (pin 44) | ✓ Confirmed (vwnut8392) |
| AIN3 = ECT (pin 45) | ✓ Confirmed (vwnut8392) |
| AIN4 = Free ADC (coding plug pin 2) | ✓ Confirmed (vwnut8392) |
| AIN5 = SD MAP sensor input | ✓ Confirmed — SD inter-board wire target |
| AIN6 = WB logging / pin 46 | ✓ Confirmed (vwnut8392 patcher) |
| MAP sensor location | ✓ Boost board vacuum port → inter-board wire → AN5 |
| Bosch schematic IDs (S250/S700/S701/S703) | ✓ Confirmed from Y261 C20/C27 schematics |
| Background loop `$0438` — fuel lookup | ❌ Not yet disassembled |
| Crank ISR `$03CA` — injection calc | ❌ Not yet disassembled |
| Rev limit — address confirmed | ❌ Likely in crank ISR code |
| Load variable (8-bit vs 16-bit) | ❌ S2Forum notes 16-bit internal load |
