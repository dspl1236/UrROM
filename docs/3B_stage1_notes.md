# 3B OEM chip combinations and a conservative stage 1 — 2026-09-09

*Chips: `roms/3b_*_404aa.bin` (the 200 20V), `roms/rr_*_404b.bin` (UrQuattro
RR), `roms/s2_*_404.bin` (S2 3B). Pressures assume the stock Bosch boost
sensor is linear 0…200 kPa (`docs/3B_boost_chip_RE.md`); a logged boost
reading on the car settles that.*

## Boost chips

| chip | Target B peak (raw) | kPa abs | gauge | other differences vs the 3B chip |
|---|---|---|---|---|
| 3B | 237 | 186 | +0.86 bar | — |
| RR | 244 | 191 | +0.91 bar | N75 duty E higher in the 10.5k…6k rpm columns (20–31 % vs 16–27 %); IAT band thresholds `73 6F` vs `67 63` (switches to the hot-IAT derate later); three 5-byte gain tables at `0x1BC1/0x1BC6/0x1BCB` end `56 AC` instead of `3D 4E` (stronger correction at large boost error) |
| S2 | 215 | 169 | +0.69 bar | same IAT thresholds and gains as the RR; ~350 further bytes in tables not yet mapped (`0x1C60…0x1D32`) |

Targets A (cold IAT) and C (hot IAT) follow the same ordering. Duty and
target ceilings (`0x1C47`, `0x1C4F`) and the knock tables are identical on
all three chips. Whole-chip: RR differs from the 3B in 646 bytes, S2 in 1084.

## Fuel / ignition chips

- **Fuel Map 1**: the RR is 6–10 raw leaner than the 3B in the 154 and 174
  load columns from 1760 to 5720 rpm and **identical at load 190** (the top
  column, which is what runs under full boost). The S2 is leaner below
  2000 rpm and 2–3 raw richer above 5700.
- **Ignition main map**: RR within ±3° on 66 cells, S2 within +9° on 90
  cells, both mean ≈ +0.2°.
- No load-vs-constant fuel cut exists in the 3B main ECU (`MOV A,3Fh` is
  never compared against a constant ≥ 0xA0), so a higher target does not
  trip a cut there.

## The S2 fuel/ignition chip on a 3B (cell-level, S2 minus 3B)

- **Ignition main map**: mean +0.2°, but shaped: +1.5…+3° through 4000–7200
  rpm at part and mid load, up to +3° at 4000–4600 rpm in the top load
  columns; −1.5…−3° at idle and at low load below 1240 rpm. One overrun
  cell (1760 rpm, load 14) is +9°. Maps 5/6/7 carry the same 90-cell trim;
  correction map 3 is almost entirely different (251/256 cells).
- **Fuel**: 3–16 raw leaner everywhere below 1500 rpm and on the low-load
  columns up to 4000 rpm; 4–14 raw richer from 5700 rpm up, peaking at
  7200 rpm; within a few raw of the 3B under full boost from 2000 to
  5200 rpm and identical at load 190. All four fuel maps carry the same
  213-cell pattern.
- RPM×IAT table 17/24 cells different; idle ignition tables touched in one
  or two cells each.

So the S2 chip is the one OEM chip that changes how the engine feels at part
throttle (more mid-band advance) and it adds top-end fuel; the price is a
leaner idle and cruise, which is exactly where an ageing 200 20V is already
marginal. The S2 *boost* chip is the one to leave out (0.2 bar lower).

## Recommendation

**OEM combo:** RR boost chip + S2 fuel/ignition chip. The RR moves boost
(+0.05 bar, firmer top end), the S2 moves timing and top-end fuel; every
byte is factory. Keep the 3B chip if the car idles or cruises badly on the
S2. Images: `D:\ECU FLASH\Bins\rr_boost_404b_27C512.bin` and
`s2_fuel-ign_404_27C512.bin` (both fold back byte-exact, S2 checksum at
0x7F00 valid, native CRC 9245FA10). Log the boost peak on the first drive.

**Stage 1 (conservative):** the RR boost chip with all three target tables
lifted +6 raw, capped at 250 → peak 196 kPa, +0.96 bar. Duty, gains, knock
tables and ceilings untouched. `3b_stage1_boost_rrbase_plus5kpa.bin` and its
27C512 image in the same folder. **Untested on a car** — run with a boost
gauge.

## Road test 2 and the hybrid chip (2026-09-14)

After a few days on RR boost + S2 fuel/ign the car stumbles slightly at a
2000 rpm cruise when the throttle is leaned in and boost starts to arrive.
Two things meet in that cell:

- **RR N75 base duty below 3300 rpm is roughly double the 3B's** (TPS 99 /
  2252 rpm: 3B 28.6 %, RR 44.3 %; TPS 118: 32.9 % vs 51.4 %). The targets are
  only +5 kPa, but the wastegate is held shut much harder at low rpm, so boost
  arrives sooner. Above 3300 rpm the two chips are nearly the same.
- **The S2 fuel maps are 3–6 raw leaner at 1760–2000 rpm / load 14–66** (1760
  rpm / load 54: 3B 128, S2 122, about 5 % leaner). S2 ignition at those cells
  equals the 3B or is a degree softer, so knock retard is the less likely cause.

Byte diff of the two fuel/ign chips (1578 bytes): fuel maps 1–4 (255 each),
ignition maps 2/3/5/6/7 (the S2's 0x731C map 3 differs in full), single bytes
in the idle ignition maps, 19 bytes at 0x7BE8 (unlabelled ramp table), 8 at
0x7542, the ID/serial text and checksum, and the shared rpm axis top point:
**S2 = 7000 rpm, 3B = 7200 rpm** (the `11 4C` / `0C 51` pair before every map).
Ignition maps 1 and 4 and all axes below the top row are identical.

**Hybrid chip** `roms/tunes/3b_hybrid_3bfuel_s2ign_404aa.bin` (CRC
`0x8C966737`, 27C512 image `0x0748FFD0`): the 3B chip with the S2's ignition
maps 2/3/5/6/7 copied in (1280 bytes), checksum recomputed. Fuel maps, idle
ignition, the 7200 rpm axis, the unlabelled tables and the ID text stay 3B.
The S2's 7000 rpm row lands on the 3B's 7200 rpm breakpoint; the two top rows
differ by at most one step, and the rev limiter sits below either. Intended
pairing: hybrid fuel/ign + RR (or stage-1) boost. If the stumble survives it,
the next move is a softer-spool RR boost chip: RR targets with the 3B duty in
the 2252–3304 rpm columns.

## The real ceiling

Raw 255 = 200 kPa on the stock sensor, so +1.0 bar gauge is the most any
200 kPa-sensor chip can request, and the controller needs headroom below
that. The boost MCU compares raw target against raw measurement, which is
why the 250 kPa sensor swap is the first step of every serious 3B tune: the
unchanged stock tables then mean 237/255 × 250 + offset ≈ 1.4 bar and have
to be scaled back down. UrROM's Boost editor sensor selector shows the
tables in the right units once the fitted sensor is identified (key-on
output voltage: 2.5 V = linear 200, 1.8 V = MPX4250A, 2.0 V = linear 250).
