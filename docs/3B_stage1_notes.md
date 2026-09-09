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

## Recommendation

**OEM combo:** RR boost chip + the car's own 3B fuel/ignition chip. Gains
about +0.05 bar and a firmer top end; every byte is factory.
`D:\ECU FLASH\Bins\rr_boost_404b_27C512.bin`.

**Stage 1 (conservative):** the RR boost chip with all three target tables
lifted +6 raw, capped at 250 → peak 196 kPa, +0.96 bar. Duty, gains, knock
tables and ceilings untouched. `3b_stage1_boost_rrbase_plus5kpa.bin` and its
27C512 image in the same folder. **Untested on a car** — run with a boost
gauge.

## The real ceiling

Raw 255 = 200 kPa on the stock sensor, so +1.0 bar gauge is the most any
200 kPa-sensor chip can request, and the controller needs headroom below
that. The boost MCU compares raw target against raw measurement, which is
why the 250 kPa sensor swap is the first step of every serious 3B tune: the
unchanged stock tables then mean 237/255 × 250 + offset ≈ 1.4 bar and have
to be scaled back down. UrROM's Boost editor sensor selector shows the
tables in the right units once the fitted sensor is identified (key-on
output voltage: 2.5 V = linear 200, 1.8 V = MPX4250A, 2.0 V = linear 250).
