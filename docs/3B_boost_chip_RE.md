# 3B / RR / S2 Boost Chip (8 KB 27C64) — Firmware RE

*Session 2026-09-08. Tool: `tools/dis8051.py` (linear-sweep 8051 disassembler
written for this job). Chips: `roms/3b_boost_404aa.bin` (447907404AA),
`roms/rr_boost_404b.bin` (857907404B), `roms/s2_boost_404.bin` (895907404 /
0261200484).*

## Summary

*Cross-checked 2026-09-08 against prj's `aduboost.idb` symbol names (see Open items).*

The M2.3 boost board runs its own 8051-family MCU from this 8 KB EPROM. All
three chips carry **identical code**; only calibration differs. The boost
control is a feed-forward + PI loop:

```
duty = clamp( BaseDuty[load][rpm]  +  Kp·error  +  I-term ,  ceiling[rpm band] )
error = Target[load][rpm] − measured pressure
```

Both 2D maps exist in three copies (A/B/C targets, D/E/F base duties) selected
by an intake-air-temperature band.

## Memory layout

| Region | Contents |
|---|---|
| `0x0000–0x15FF` | 8051 code (reset at 0x0000 `CLR IEN0.7`, vectors, ISRs, main loop) |
| `0x1600–0x163F` | table descriptor list: `(X-axis ptr, Y-axis ptr, table ptr)` 16-bit BE triplets |
| `0x1640–0x16B0` | small axes + two 5×8 tables (identical on all chips) |
| `0x17ED–0x17F1` | IAT-band thresholds (differ 3B vs S2/RR) |
| `0x1890–0x18B3` | axes: `0x189A` X (load, 8 pts), `0x18A3` Y (period, 16 pts) |
| `0x18B4–0x1A33` | **Boost Target A / B / C** (3 × 8×16) |
| `0x1A34–0x1BB3` | **N75 Base Duty D / E / F** (3 × 8×16) |
| `0x1BB4–0x1EFF` | 1D correction / limit tables, RPM-band axes |
| `0x1FFA–0x1FFF` | 6-byte tail: sum / id / cal tag (3B 0x0254, RR 0x0255, S2 0xA027) |

## Axis format

`[count][first][delta]…` — absolute breakpoints are the running sum. The
lookup routine at `0x1259` subtracts successive deltas from the input (8-bit
for X, 16-bit RAM 32h:33h for Y) until it borrows, then interpolates.

| Axis | Input | Decoded breakpoints |
|---|---|---|
| `0x189A` X | RAM 64h = linearised **TPS** (ADC ch3 → 71h → 38h) − 64 + correction | 43 61 80 99 118 136 155 219 |
| `0x18A3` Y | RAM 32h:33h = Timer2 period ≫ 4 | 142 153 166 181 200 222 250 285 307 333 363 400 454 526 588 666 |
| `0x1EF0` X | RAM 72h = ADC ch1 raw = **boost pressure** (prj: READ_BOOST_AN1) | 65 91 117 143 169 |
| `0x1640` Y | period | 154 172 200 238 294 385 556 811 |
| `0x16A2` Y | period → RPM-band index 5Eh (0–7) | 152 172 200 238 294 385 556 811 |
| `0x1BB8` Y | period | 167 200 250 333 500 |

**Period → RPM.** Timer 2 captures the tach period at 1 MHz (12 MHz / 12).
With 2.5 pulses per rev (5-cyl ignition events): `RPM = 1 500 000 / value`.
Check: axis `0x1BB8` → 9000 / 7500 / 6000 / 4500 / 3000 rpm exactly.
Main Y axis → 10.5k rpm (col 0) … 2250 rpm (col 15). **Columns run high-rpm
to low-rpm.**

## Lookup engine

| Routine | Function |
|---|---|
| `0x12CA` | 2D lookup: DPTR → descriptor triplet, R1 → X input RAM addr. X search (`0x1259`), Y search on 32h:33h (`0x1256`), then `table[xidx·ncols + yidx]` with bilinear interpolation (`0x128C`, `0x1293`). |
| `0x1259` | delta-axis search, 8-bit input |
| `0x1256` | delta-axis search, 16-bit input (R1 → high byte, R1−1 → low) |

Tables are row-major `[X][Y]`: rows = load, columns = period.

## Control loop (main routine 0x0A55–0x0B90, loop 0x0DB0–0x1040)

1. ADC sweep: ch3 → 71h → linearised via `0x1887/0x1889` → **38h (TPS)**;
   ch1 → 72h (**boost pressure**, filtered into 42h);
   ch4 → 6Eh, ch5 → 6Fh, ch7 → 73h (temperature-type inputs with plausibility
   windows at `0x188A/0x188E`); ch0 → 69h, ch2 → 6Ah.
2. `64h = clamp(38h − 64) + f(RPM band)` — the throttle-derived row input for the big maps.
3. `5Eh` = RPM band (0–7) from axis `0x16A2`.
4. **Mode select** (`0x0B5F–0x0B82`): 6Eh (IAT) vs thresholds `0x17ED`
   `hi, hi−hyst, lo, lo−hyst`. Above `hi` → tables **C / F**; between → keep;
   between `lo−hyst` and `hi−hyst` → **B / E** (default, also set at reset);
   below `lo−hyst` → **A / D**.
5. `4Bh = min( D/E/F[64h][rpm] + corr[0x1D6A][F6h], ceiling[0x1C47][5Eh] )`
   — **N75 base duty**.
6. `3Dh = A/B/C[64h][rpm] − corr[0x1C6A][F6h] − 7Ah` — **boost target**.
7. Error = 42h vs target 3Eh (target limited by `0x1C4F[5Eh]`); P-term
   `7Dh = gain·|err|` added to 4Bh (`0x0EDB`), I-term via 49h/4Ah
   (`0x0EF4–0x0F84`), result clamped by `0x1C47`, stored 43h/3Bh.
8. PWM: compare channel 3 (`CCL3/CCH3`) on-time = `5Dh × period`
   (`0x00D4`, `0x13B9`), period from `CRCL/CRCH`.

## Status nibble to the main ECU (traced 2026-09-09)

The main ECU's flag byte `20h` is the boost board's port P4 low nibble (latched
at `0xA040`, main side XORs 0x0E). On the boost side:

```
0x0349   5Fh = result of 0x08A8            ; every main-loop pass
0x00FA   P4 = (P4 & 0xF0) | 5Fh            ; in the CC3 ISR
0x08A8   R0 = swap((C6h & 0xF0) + BFh)     ; = (67h:66h sample) >> 4
0x088F   band = 5 thresholds @0x183C  28 14 0A 05 03  (40 20 10 5 3)
         5Fh  = code[band] @0x1847         00 01 02 04 08 0F   (one-hot)
```

So the nibble is a **five-band level code of the 16-bit value `67h:66h`**
(bands: ≥640 → 0, 320–639 → 1, 160–319 → 2, 80–159 → 4, 48–79 → 8, <48 → F
in raw units; `BEh–C2h` / `C5h–C9h` are 4-deep histories of 66h / 67h that
also feed a 5-sample moving average at `0x0C57`).

`67h:66h` is the **adaptive knock reference** (background noise level), and
the routine at `0x0700–0x07EA` is the knock evaluator (traced 2026-09-09):

- **Knock window ISR** (`0x006D`, ADC-complete vector, three phases on flags
  `26h.4/.5/.6`): at the window start it samples ADC **ch0** into `6Ah`,
  resets/arms the knock IC integrator via **P4.4 / P4.5**, and extends compare
  register 3 by `5Ah:5Bh` (window length × period, from the 5×8 tables at
  `0x1649/0x1671`); at the window end it samples ch0 again, inverted, into
  **`6Bh`** = integrated knock signal; a third sample lands in **`69h`**.
  `2Bh` → P4.4–P4.7 is the knock-amplifier **gain word** (0x50 / 0xA0 / 0xC0),
  auto-ranged at `0x0110–0x0131` from the `69h` vs `6Ah` comparison (`0x014D`).
- `6Bh` → `6Ch` (`0x0245`) → **`34h`** (`0x02FC`, in the ignition-event ISR).
  So **34h = integrated knock signal of the last window, 69h = the companion
  ch0 sample** used for gain ranging and as the signal offset.
- `37h = (34h + 69h) × B / (67h:66h)` (`0x0721–0x074A`) is the **knock ratio**
  (signal over reference). It is compared with thresholds from `0x1817`
  (`1F 04 08 08 10 04 08 19`); the result sets `24h.0/24h.2/24h.3` and is
  driven out on **P5.5 = knock recognised**.
- `67h:66h` starts at 0x000F (`0x08C2`) and is adapted each cycle at
  `0x0756–0x07EA` (scaled multiply/divide, gains from `0x1805–0x1816`),
  tracking the background level — classic Bosch adaptive knock reference.
  `BEh–C2h` / `C5h–C9h` are 4-deep histories of it; `0x0C57` takes a
  5-sample mean for the plausibility check that sets `22h.0/22h.1`.

So the P4 low nibble sent to the main ECU is the **band of the knock
reference level** (how noisy the engine currently is), not the knock event.
The knock event itself is the P5.5 line. On the main-ECU side bit 2 of the
nibble (reference in band 80–159) and P4.4 (`20h.4`, the gain word's low
bit) pick the ignition table set; the maps involved differ in at most 62
cells by at most 3° at part load (`551_calibration_descriptors_RE.md` §3c),
i.e. a mild timing trim by noise class. Bits 0, 3, 6 and 7 of `20h` are used
elsewhere in the main firmware (`0x1740`, `0x18D8`, `0x2B99`, `0x37F9`…) —
not yet traced. All tables involved are byte-identical on 3B, RR and S2.

## Chip differences

| Item | 3B 404AA | RR 404B | S2 (0261200484) |
|---|---|---|---|
| Target B max raw | 0xED → 186 kPa | 0xF4 → 191 kPa | 0xD5 → 167 kPa |
| Target A/C max | 0xD9 / 0xCE | 0xDE / 0xD0 | 0xCE / 0xC9 |
| Base duty D/E/F | up to 0xAC | up to 0xAC | up to 0xAC (shape differs) |
| IAT thresholds `0x17ED` | 67 63 38 2D | 73 6F 38 2D | 73 6F 38 2D |
| `0x1C60–0x1DFC`, `0x1E20–0x1EC8` | = RR | = 3B | different (correction tables) |

The kPa decode assumes the stock Bosch 200 kPa sensor. Absolute scale is
therefore **provisional** until confirmed against a logged boost gauge.

## Open items

- ADC channel identities now cross-checked against prj's IDA database of the ADU
  (551) boost chip (`prj/m232/src/idb/aduboost.idb`): boost on AN1, TPS is the
  map axis (`RAM_5E_MAPAXIS_TPS`), IAT/ECT/altitude are the other channels. The
  32KB ADU firmware is a different build but the same code family: prj's
  READ_2D_MAP / READ_AXIS / READ_AXIS_16BIT / INTERPOLATE / DEG_TO_TIME map onto
  the 3B routines at 0x12CA / 0x1259 / 0x1256 / 0x128C / 0x13B9.
- The boost MCU also runs knock detection (prj: KNOCK_ROUTINE, DEG_TO_TIME). The
  period-scaled tables `0x1649` / `0x1671` (5Ch/5Dh × period → CCL3, P4 nibble)
  are therefore the knock-window timing, not wastegate PWM.
- `0x1649` / `0x1671` PWM-fraction tables and the 5Fh → P4 nibble output.
- Exact meaning of `0x1C60–0x1DFC` (S2-only differences; likely altitude /
  IAT corrections indexed by F6h).
