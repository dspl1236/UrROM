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
