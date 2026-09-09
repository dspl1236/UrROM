# Porting AAN/ABY (551) calibration into the 3B (404) — working notes

*Started 2026-09-09. Tool: `python -m urrom.cli xcompare A B [--role fuel|ign] [--raw]`
(`urrom/xcompare.py`): decodes each map with its own formula, bilinear-resamples B
onto A's axes, prints the delta table.*

## Why this is possible at all

Both families use the same Bosch descriptor format, the same map-reader
routine, the **same 600…7200 rpm axis** (identical delta bytes) and load axes on
the same scale (3B 14…190, 551 12…180/206). PRJmod cannot run on a 404 ECU, so
the port is value-by-value, but the values line up.

## Baselines in `roms/`

| Chip | Family | Note |
|---|---|---|
| `3b_fuel-ign_404aa.bin` | 404 | the user's 200 20v (target) |
| `aby_fuel-ign_551aa.bin` | 551 0x2E17 | real S2 Coupé ABY read |
| `adu_fuel-ign_551c.bin` | 551 0x2E17 | real RS2 ADU read |
| `prj_stock_aan-aby_551aa_0202.bin` | 551 0x0E13 (prjmod layout) | prj's "stock AAN/ABY" calibration (github.com/prj/m232, MIT) |

## Role matching by raw similarity (3B ↔ 551, resampled, raw bytes)

| 3B map | Closest 551 map | rms (raw) | Reading |
|---|---|---|---|
| Ign Map 1 (fault fallback) | ABY map 1 (0x30A8), ADU map 1 (0x30AC) | 3.7 / 4.3 | the 551 "PT primary" is the **fallback** map, mislabelled |
| Ign Map 2 (main) | ADU maps 5 / 7 (0x36BC / 0x3931) | 3.0, mean −0.1 | same bytes → **same degrees**: 551 decode is 0.75°/count, not RS2.xdf's 0.6491 |
| Ign Map 2 (main) | ABY maps 3 / 5 / 7 | 4.4, mean +2.4 raw (+1.8°) | ABY runs ~2° more advance than the 200 20v |
| Fuel Map 1 | ABY / ADU fuel 0x2E17 | 12.4 / 12.7, mean −8 / −5 raw | 551 fuel maps sit lower, −15…−17 raw at high load |
| Fuel Map 1 | prj AAN/ABY 0x0E13 | (see resolution below; earlier row used bad axes) | — |

**Resolved 2026-09-09.** The RS2 D02 chip (real RS2 read, 0x0E13 layout) is
byte-identical to the ADU chip (0x2E17 layout) map for map — fuel 0x0E13 ==
0x2E17, ignition 0x10A8 == 0x30AC … 0x192D == 0x3931 (0.0 rms, blanks
excluded). So the 0x0E13 layout encodes exactly like the 0x2E17 one, and the
PRJ XDF's "overrun / no knock / knock level 1" names are PRJMOD's own use of
those slots, not the stock roles. The earlier "+16…+27 raw"
offset was an artefact: the 0202 profile read its axes from the PRJ XDF's
axis addresses, which do not hold axes in this file (rows came out 9…16, load
400…2840), so the resample was garbage. The file keeps the stock Bosch
descriptors in front of every map, and `get_axes()` now reads them.

Raw, same addresses, prj `stock_AANABY` vs the RS2 D02 chip (ABY and ADU
carry the identical bytes on maps 1/3/5/6/7):

| Map | prj − real chip | Reading |
|---|---|---|
| Fuel 0x0E13 | mean +5.7 raw, 234 cells | fuel raised |
| Ign 1 (0x10A8), 3, 5, 6, 7 | 0 cells differ | untouched stock |
| Ign 2 (0x125F, set A main) | mean −0.3 raw, 242 cells | reshaped, same average |
| Ign 4 (0x1594, set B main) | mean −5.6 raw (≈ −4°) | retarded |

So prj's file = RS2 D02 firmware + the ABY/ADU/RS2 D02 calibration with mild
edits to fuel and to the two coding-set main maps; it does **not** carry the
AAN 551AA chip's maps (those differ on every map). Labelled in `KNOWN_CRCS`
and `roms/README.md`; the ABY / ADU / RS2 D02 direct reads remain the stock
baselines.

## Consequences applied to the app (2026-09-09)

- 551B/551C ignition decode switched to raw × 0.75 − 22.5 (`ign_decode`);
  the RS2.xdf formula is kept as `ign_decode_rs2xdf()`.
- 551 selector traced (`551_calibration_descriptors_RE.md` §3e): three
  coding-plug-selected sets; main map = 2 / 4 / 6, alternates 3 / 5 / 7 only
  under a tester flag, map 1 = fault fallback. Map names updated accordingly.
- `xcompare` ROLE_MAPS: 551 main ignition = map 4 (set B, the low-resistance
  coding class); use `--b-map` for a known coding.

## Sensors (S2Forum thread 65435, vwnut8392 / prj)

AAN stock MAP sensor = **250 kPa**, RS2 = 300 kPa, R201 5.6 kΩ 1 % 1206.
The 3B boost board is still assumed 200 kPa (a 250 kPa scale would put the
stock 3B target at ~1.3 bar, which a K24 200 20v never ran). A logged boost
reading on the car settles it.

## What a port would actually mean

- **Ignition**: the 3B main map is already within ~2° of the ABY's and within
  0.1 raw of the ADU's at the same load/rpm — there is no free timing to
  "port". Differences that matter are in the corrections (3B map 3, 551 maps
  2/3/4/6) whose roles on the 551 are not traced.
- **Fuel**: the 551 chips run 5–17 raw lower. That reflects injectors, MAF
  scaling and sensor range as much as mixture; raw values must not be copied
  across without correcting for hardware.
- **Boost**: the two boost boards run different firmware; targets must be
  re-expressed in the 3B's 8×16 TPS×rpm tables (`docs/3B_boost_chip_RE.md`).

## Next steps

1. ~~Trace the 551 ignition selector~~ — done (§3e): coding plug picks
   maps 2 / 4 / 6.
2. ~~Resolve the prj 0x0E13 timing offset~~ — done: it was an axis-read bug; the
   layouts encode identically (RS2 D02 == ADU byte for byte) and prj's file is
   the ABY/RS2 cal with mild fuel / map 2 / map 4 edits.
3. Log boost on the car → sensor scale → boost-target port.
