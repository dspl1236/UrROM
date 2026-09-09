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
| Fuel Map 1 | prj AAN/ABY 0x0E13 | 18.6, mean −7 | similar to ABY; high-load −25 raw |

prj's 0x0E13-layout ignition maps come out **+16…+27 raw** above the 3B — far
more than any real stock difference. Either that file is not stock timing, or
the 0x0E13 family encodes ignition differently. **Do not use it for timing
until resolved**; the ABY/ADU direct reads are the trustworthy 551 baselines.

## Consequences applied to the app (2026-09-09)

- 551B/551C ignition decode switched to raw × 0.75 − 22.5 (`ign_decode`);
  the RS2.xdf formula is kept as `ign_decode_rs2xdf()`.
- 551 map names: map 1 "(fault fallback?)", maps 5 / 7 "(main?)" — the 551
  selector logic has not been traced; roles are by similarity only.
- `xcompare` ROLE_MAPS: 551 main ignition = map 5.

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

1. Trace the 551 ignition selector (same method as the 3B: `0x0FF9` READ_MAP,
   base-pointer stubs, IGN_CALC) to name maps 2–7 properly.
2. Resolve the prj 0x0E13 timing offset (compare against the RS2 D02 chip's
   own maps, which are the same family).
3. Log boost on the car → sensor scale → boost-target port.
