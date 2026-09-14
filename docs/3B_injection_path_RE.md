# 3B (404) injection path: from MAF to injector pulse

Traced 2026-09-14 on `roms/3b_fuel-ign_404aa.bin` (flat 32 KB, firmware
0x0000–0x62FF, calibration 0x6300–0x7EFF, checksum 0x7F00). Addresses are file
offsets. Method: `tools/dis8051.py` sweep, READ_MAP (0x0D92) table-set
dispatcher decode, descriptor index (`decode_descriptor_tables`).

## 1. The background tasks and their table sets

The main loop enters each background task through a dispatcher at 0x20EB
(6-byte entries: `LCALL base-stub; LJMP task`). A stub sets DPTR to the task's
scalar table and 75h:76h / 77h:78h to the READ_MAP pointer / index bases.
READ_MAP takes the slot in R2 and auto-increments it, so a task reads its
tables in slot order.

| entry | stub | DPTR scalars | index / pointer | task | what it is |
|---|---|---|---|---|---|
| 0x20EB | 0x3404 | 0x6322 | (inherits) | 0x036D | crank-sync: ignition tooth scheduling (P1.5) |
| 0x20F7 | 0x3417 | 0x6730 | (inherits) | 0x0A3F | injection pulse finalise (P1.3 / Timer 0) |
| 0x210C | 0x3423 | — | 0x6000 / 0x65D0 | 0x1321 | ADC read, IAT/ECT linearisation |
| 0x2112 | 0x3430 | 0x6357 | 0x6002 / 0x65D6 | 0x1376 | boost-board status, start/run state |
| 0x212A | 0x3462 | 0x635D | 0x6005 / 0x65DC | 0x1994 | **load limiters → fuel cut** |
| 0x2130 | 0x3472 | 0x6360 | 0x65E4 + selected index | 0x1AC9 | **fuel factor** |
| 0x2148 | 0x3590 | 0x637C | 0x6090 / 0x6622 | 0x1C42 | rpm, load, speed-density terms |
| 0x2154 | 0x35B0 | 0x63B8 | 0x6282/8A/92 by A0h.3, 20h.2 | 0x21CC | lambda / closed loop, idle |
| 0x2118 | 0x34B7 | 0x636F | 0x6628 + selected | 0x15E8 | ignition (docs 3c) |

## 2. Which fuel map runs (stub 0x3472)

```
20h.2 clear (boost-board flag off):   A0h.4 → index 0x601E  slot 12 = Fuel Map 2
                                      A0h.5 → index 0x6034  slot 12 = Fuel Map 1  (slot 5 → 0x6D2C)
                                      else  → index 0x6008  slot 12 = Fuel Map 1
20h.2 set:                            A0h.4 → index 0x6060  slot 12 = Fuel Map 4
                                      A0h.5 → index 0x6076  slot 12 = Fuel Map 3  (slot 5 → 0x6FA8)
                                      else  → index 0x604A  slot 12 = Fuel Map 3
```

A0h is the coding byte (docs/551 §3f): A0h.4 is set for coding numbers 2 and 7
(codes 0x10 / 0x14), A0h.5 for coding number 5 (0x20). So **Fuel Map 4 is not a
WOT map**: it is the coding-2/7 calibration with the boost-board flag set, and
Fuel Map 2 the same without it. A car on any other coding runs Fuel Map 1
(flag off) or Fuel Map 3 (flag on). On stock chips maps 1, 2 and 3 are
identical, which is why this was invisible before.

## 3. The fuel factor (task 0x1AC9, table set 0x65E4)

All multiplications are 8-bit factors on a 16-bit mantissa + exponent (R4),
with **0x80 = 1.00** (Q7). The accumulator starts at 0x80 × 0x80 = 0x4000.

| slot | table | shape / inputs | role | stock 3B |
|---|---|---|---|---|
| 0 | 0x6967 | 3 × MAP (AN5, RAM 39h) | ± pulse correction → RAM 63h (max ±60), applied ×5 in §5 | 128 128 128 = off |
| 1 | 0x6970 | 4 × RPM | → 8Bh | 200 ×4 |
| 2 | 0x697B | 5 × UBAT (117…235) | → 6Fh, injector voltage / dead-time correction | 208 119 75 56 39 |
| 3 | 0x698E | 6 RPM × 4 LOAD | → 65h, air-per-rev filter gain (§4) | 48…255 |
| 4 | 0x69B1 | 3 RPM × 4 LOAD | → B, then × slot 5 | 255 … |
| 5 | 0x69CD | 6 ECT × 6 IAT | × slot 4 → 60h | 128 / 109 |
| 6 | 0x6A01 | 6 ECT × 6 IAT | **warm-up enrichment** | 230 … 119 (/128) |
| 7 | 0x6A31 | 5 RPM × 3 LOAD | warm-up rpm/load shaping (× slot 6) | 134 180 207 … |
| 8 | 0x6A47 | 5 × ECT | **post-start enrichment**, decays via table 0x6760[XRAM 015B] | 255 123 40 18 6 |
| 12 | 0x6A8E | 16 RPM × 16 LOAD | **the fuel map** (selected as in §2) | 124…199 (/128) |
| 13 | 0x6B9C | 6 RPM × 4 IAT | IAT fuel compensation (skipped if XRAM 29h.2) | 1.00…1.14 |
| 14 | 0x6BBB | 5 × RPM | rpm fuel trim (skipped if 20h.1, needs XRAM CAh.1) | 129…137 |
| 15 | 0x6BC8 | 6 × ECT | **cranking enrichment** (used instead of the map while 28h.1) | 216 100 48 32 11 8 |
| 16, 17 | 0x6BD2, 0x6BD8 | 2-pt | cranking extras (2Eh.6) | |
| 21 | 0x6BF4 | 4 × ECT | cranking decay (XRAM 7Ch.0) | |
| — | XRAM 0x0112 | — | lambda closed-loop correction (if 29h.1) | |

Running (28h.1 clear):

```
F = 1.0 × warmup(6A01) × shape(6A31) × [poststart(6A47)·decay]
      × FUELMAP(slot 12) × IAT(6B9C) × rpmtrim(6BBB) × lambda(XRAM 112)
```

Result mantissa 0Bh:0Ch, exponent 0Dh → XRAM 0x001E:0x001F, 0x0020 (copied by
0x0D35). The lambda task copies it back (0x2251) and folds in its own terms to
5Ah:5Bh / exponent 5Ch (0x22A2).

## 4. Load is MAF-derived, not MAP

- The per-tooth ISR integrates the hot-wire MAF into 24-bit counters (RAM
  B3–B5 / B6–B8). Their difference over one crank period (0x1200 / 0x04C0) is
  scaled by the calibration bytes at **0x6328–0x632F** (table 0x6322 offsets
  6–13: `90 01 A0 A0 A0 A0 A0 A0`) and ×15, normalised to mantissa 3Ch /
  exponent 3Dh.
- 40h:41h is the filtered **air per revolution**: `40h:41h += (3Ch:3Dh −
  40h:41h) × 65h/256` (0x08D2–0x0931), gain 65h from table 0x698E.
- **LOAD (3Fh) = (40h:41h × 0xA4) >> 12** (0x1D51–0x1D6A). Top of the map load
  axis (190) ≈ 40h:41h = 4746.
- The MAP sensor (ADC ch 5 → RAM 39h) is only consulted by the 3-point table
  0x6967, which is 128/128/128 = no effect on every stock chip. A 3-bar sensor
  on the main ECU therefore changes nothing by itself; the boost board is the
  only consumer of MAP scale.

Load limiters (task 0x1994): `LOAD > LoadLim1 (0x6951)` (or LoadLim2 0x695D
when 2Eh.1) sets 2Ah.7 → fuel cut, and logs through 0x4661 / 0x45DA.

## 5. Pulse width and the injector output (task 0x0A3F, scalars 0x6730)

Entered from the crank-sync path with R7:R6 = 40h:41h (0x0933):

```
p  = air-per-rev (40h:41h)  [+ 66h transient term when 24h.6 / A040.1 allow]
p *= (5Ah:5Bh, 2^5Ch)                        fuel factor incl. lambda
p *= 0x6730[5Dh (+16 if 2Eh.0)] / 64  ×4     start-event multiplier (0x4A for 5 events, then 0x40 = 1.0)
p += 61h (signed, 2Ah.1)                      acceleration term (0x2790)
p += 63h × 5                                  MAP-table correction (0x0A71, zero on stock)
p  = min(p, 0x6730[0x2A] × 25)                ceiling: 179 × 25 = 4475 (0x0A94)
5Eh:5Fh = p                                   injection time; also sent to the boost board at XRAM A040
Timer0 = ~(p × 0x6730[0x2C] (=205) >> n); CLR P1.3; TF0 ISR (0x2010: SETB P1.3; RETI) ends the pulse
```

**P1.3 with the Timer 0 one-shot is the injector drive** (all injectors,
simultaneous). Evidence: the ABY's own KW1281 group 2/2 "injection ms" is this
same RAM pair 5Eh:5Fh; the value is clamped by a calibration ceiling; and it
is the only Timer 0 load in the firmware. `docs/M232_ECU_Hardware_Reference.md`
line 814 calls P1.3 the coil — that is superseded: the coil is **P1.5**, set at
the reference tooth in the crank ISR (0x013A / 0x0179 / 0x0248) and cleared at
the computed dwell-start tooth (0x058C / 0x06F9, from 53h). P1.2 is the idle
valve PWM (compare 2, 5000-tick period, from XRAM 4F/50/51/53).

## 6. Consequences for the GT3071 build

1. **There is no single injector constant.** Pulse = air-per-rev × factor.
   Bigger injectors mean scaling the fuel maps (all four, plus cranking 0x6BC8
   and post-start 0x6A47) by stock_cc / new_cc, or scaling the MAF bytes at
   0x6328. 034 did both on the AAN (their 0x8380 byte plus a renormalised map).
   The clean way on the 3B is the maps: they are Q7 factors around 1.0 with
   headroom to 255, so ×0.8 (440→550-class) lands at 100…160.
2. **The load ceiling is MAF headroom, not the MAP sensor.** The 190-count axis
   top corresponds to a fixed air-per-rev; more air than the stock MAF can meter
   is clipped in hardware before any table sees it. A bigger MAF or a re-scaled
   0x6328 (which moves the whole load axis) is the real limit, then the load
   limiters, then re-gridding the map axes.
3. **The pulse ceiling** at 0x6754 (179 × 25 = 4475) must move with the
   injectors and rpm.
4. The 3-bar sensor matters only on the boost board (docs/3B_boost_chip_RE.md).

## Open

- The scale of 5Eh:5Fh in µs (needs the 0x0F44/0x0F65 exponent bookkeeping
  through 0x6730[0x2C]); the ABY KW1281 formula (0F, 13) will pin it once the
  bridge logs a known idle pulse.
- 0x6730 offsets 0–31 (the 0x4A/0x40 arrays) and 32–63 (ramps 0x33…0xE6): only
  the start-event multiplier use is traced.
- Whether 0x6328 alone re-scales load without side effects on the boost-board
  hand-off (5Eh:5Fh goes to the knock MCU).
