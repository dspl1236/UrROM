# The RS2-turbo 3B chipset: as if the 200 20V had shipped with the RS2's turbo

Built 2026-09-16 with `urrom.rs2_builder` (`python -m urrom.cli rs2-chipset`).
Hardware this assumes, per the owner: **K24-7200 RS2 turbo, RS2 green injectors
(0 280 150 984) on the RS2 3.8 bar regulator, the factory RS2 boost profile,
the AAN/ADU 250 kPa MPX4250A sensor on the boost board**, stock MAF (same part
on 3B and AAN). Target: RS2-like power on the 3B ECU.

Base chips: the 3B's own 200 kPa boost chip (`3b_boost_404aa.bin`, the
owner's choice over the RR) and the 3B 447907404AA fuel/ign chip. Reference:
the RS2 D02 boost chip and the ADU fuel/ign chip (byte-identical to the RS2
D02, docs/AAN_3B_port_notes.md).

## Why the 250 kPa sensor is not optional

The RS2's factory boost target peaks at raw 192 on its MPX4250A = **198 kPa
absolute, 14.2 psi**, with the limit table at 258 kPa. On the 3B's 200 kPa
sensor that peak is raw 253 of 255: the controller would have no room above
its own target and the overboost release could never sit above it. On the
250 kPa sensor the same 14.2 psi is raw 179 with a third of the scale spare.
A 300 kPa sensor would also work but makes every count 1.2 times coarser for
no benefit at this pressure.

## Boost chip — `roms/tunes/404/3b_rs2turbo_boost_mpx4250.bin`

1. `convert_sensor(bosch200 -> mpx4250, limits_psi=19)`: every absolute and
   delta table keeps its stock kPa meaning on the new sensor; target ceiling
   133 counts above ambient (18.9 psi) and overboost duty-release at raw 247
   (22.1 psi), both above the profile with margin.
2. **Targets A/B/C**: the RS2's full-throttle curve by rpm (its per-rpm table
   maximum, decoded on the 250 kPa sensor) is written into the TPS-219 row;
   every other TPS row is scaled by the same per-rpm gauge ratio, so the 3B's
   part-throttle shape is kept underneath the RS2 profile.

   | rpm | 3B WOT | RS2 WOT (now in the chip) |
   |---|---|---|
   | 2250 | 12.5 psi | 7.3 psi |
   | 2850 | 12.5 | 8.6 |
   | 3300 | 12.5 | 9.8 |
   | 4100 | 12.5 | 12.4 |
   | 4900 | 12.3 | 14.0 |
   | 5300–7500 | 12.2 → 10.3 | 14.2 |
   | >7500 (top columns) | 9.6 → 4.6 | 14.2 |

   That is the K24-7200's character against the 3B K24's: later, and holding
   on top instead of tapering.
3. **N75 duty D/E/F**: the RS2's per-rpm duty curve applied the same way — 27 %
   at 2250 rpm rising to 74 % from 6000 up, where the 3B tables fall to 16 %.
   The duty ceiling (0x1C47) goes from 68 % to 78 % so the top can be reached.
   The RS2's 551 boost chip is not decoded to the level the 404 is; its column
   axis is not throttle (targets fall in the top columns), so only the per-rpm
   envelope of its tables is used. The adaptive offset and the P/I loop cover
   what the envelope does not.

## Fuel/ign chip — `roms/tunes/404/3b_rs2turbo_fuel-ign_greens38.bin`

1. **Injectors**: fuel maps 1–4 and the cranking table × 305/405 = 0.753 (3B
   stock 0 280 150 737 = 305 cc at 3 bar; greens = 405 cc at 3.8 bar). The
   post-start, IAT and warm-up tables are relative and stay.
2. **Ignition — literally RS2**: the ADU main map (coding set B, 0x3598)
   resampled onto the 3B's rpm × load axes and written into main maps 2/5/6/7
   so the coding plug cannot pick a non-RS2 map. Against the 3B's own map 2 it
   is +0.8° on average and within ±1.5° in the top-load column; the largest
   differences (up to 9°) are light-load cells. Fault map 1 and correction maps
   3/4 stay 3B. Decode is Bosch's 0.75°/count on both chips — the "8–9° RS2
   offset" people remember is the RS2.xdf's 0.6491×raw−8.2 formula, not the
   cam wheel; on a 3B ECU nothing about the trigger changes anyway.
3. **Load**: air-per-rev cap 255, load limiter 210 / 168 (the K24 stage-1+
   numbers). RS2 boost on this turbo is ~1.1–1.2 × the 3B's air, inside the
   8-bit load without a rescale.
4. Checksum recomputed.

**The one thing that is not RS2: the fuel map.** The ADU fuel map equals the
3B's within a few counts below load 100 and runs ~15 % lower above it; if it
were the 3B map scaled for its injectors the whole thing would be 25 % lower.
So the 551 keeps an injector scale elsewhere and its map is relative to it, and
copying its high-load lean-out onto the 3B would be a guess in the wrong
direction. Fuel is therefore 3B × injector ratio, to be trimmed from the
wideband; the RS2's factory AFR under boost is the target.

## Order on the car

1. Greens + regulator in, `3b_inj550`-style check first: burn this fuel/ign
   chip with the **old** boost chip and sensor, confirm idle and cruise
   lambda. (This chip's ignition is RS2 already; that is fine on the K24.)
2. 250 kPa sensor on the boost board, this boost chip. Bench: 5 V supply, ~1.5 V
   at atmosphere for an MPX4250A (the identifier in the boost tab confirms).
3. First pulls with a wideband and the live trace: the RS2 profile comes in
   later than the 3B's, so the first thing to check is that the loop reaches
   14 psi by 5000 rpm without the overboost release (22 psi) tripping.

## Files

| file | CRC32 | copies in 27C512 |
|---|---|---|
| `roms/tunes/404/3b_rs2turbo_boost_mpx4250.bin` | see roms/README.md | 8 |
| `roms/tunes/404/3b_rs2turbo_fuel-ign_greens38.bin` | see roms/README.md | 2 |
