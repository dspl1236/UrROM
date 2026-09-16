# 034EFI Rip Chip files: what they are and what they gave the 3B

Source: the user's archive `Z:\Archive\Google Drive\home flashing\034 Files\034 Files`
(Rip Chip 1.0.0 software, ECU definitions, and the GT2871 / GT28RS / GT3071 / K24
tunes for the AAN). Decoded 2026-09-14.

## The tunes

Every fuel/ign `.034` descrambles with `urrom.descramble` and identifies as a
4A0907551AA (AAN ECU). They are **not** calibration-only tunes:

| | vs stock AAN 551AA |
|---|---|
| firmware half (0x0000–0x7FFF) | ~24 600 bytes differ, reset vector 0x1218 vs 0x1297 — even on 034's "Stock RipChip" |
| calibration half | 64 bytes (stock rip) … 721 bytes (GT2871 R9) |
| boost chips | ~17 300 of 32 768 bytes differ from the stock AAN boost chip |

So 034 shipped its own main-ECU firmware build and its own boost-MCU code
("brand new code" in their blurb). The table layout survives, though: the GT3071 and GT2871
boost chips differ from each other only at 0x2480–0x25CE (the 551's N75 duty
and boost target tables) and a 28-byte label at 0x2F87, so UrROM's 551AA target
and duty addresses do read them. One boost chip per turbo kit serves every
injector variant. None of it runs in a 3B's 404 ECU, and the kits require coil-on-plug
AAN/ABY/ADU, a 3.0 bar VMAP, 440 cc injectors and a 4 bar FPR.

## The definitions (.ecu)

Java-serialised `com.efi.store.EcuV1` objects; `urrom/ecu034.py` reads them
(needs `javaobj-py3`). Four were in the archive:

| file | ECU | maps | checksum |
|---|---|---|---|
| 3B ECU Generic 1.01.ECU | "3B Fueling/Timing", 32 KB | 11 | sum 0x0000–0x7EFF stored at 0x7F00 — matches the 404 checksum UrROM found in firmware |
| AAN ECU Generic 1.01.023.ecu | "AAN Universal 551AA", 64 KB | 12 | 0x0000–0xFEFF → 0xFF00 |
| RS2 ECU Generic 551B 1.01.03.ecu | 551B | 10 | as AAN |
| AAN Boost R2.ecu | AAN/ABY/ADU boost chip | 2 | none |

**034 never sold a 3B chip, but their software carried a 3B definition.** Its
eleven maps, checked against the 3B firmware's descriptor index
(`decode_descriptor_tables`), are all real firmware-referenced tables. 034's own
axis addresses point at the descriptor delta bytes and were never cumulated, so
the axes Rip Chip showed were wrong; the real ones are below.

| 034 name | data | shape | real axis (descriptor) | 3B values | now in UrROM as |
|---|---|---|---|---|---|
| Primary Fueling Table | 0x6A8E | 16×16 | rpm × load | — | Fuel Map 1 (already) |
| Primary Timing Table | 0x71F8 | 16×16 | rpm × load, ×0.75−22.5 | — | Ignition Map 2 (already) |
| Load Limiter 1 | 0x6951 | 5 | rpm 2000 3000 4000 5000 6000 | 174 174 168 160 156 (RR: 180 …) | Load limiter 1 (fuel cut) |
| Load Limiter 2 | 0x695D | 5 | same | 140 130 130 120 110 (RR 144 134 …) | Load limiter 2 (limp) |
| Closed loop O2 limit | 0x7C72 | 6 | rpm 1000 2000 3000 4000 4800 6520 | 54 90 96 80 62 50 (RR +2…+4) | Closed-loop lambda load limit |
| Closed loop O2 limit (limp) | 0x7C80 | 6 | same | 54 90 96 76 56 40 | … limp |
| Idle Timing | 0x717F | 7 | rpm 560 720 920 1240 1400 1680 2800 | 50 43 43 38 38 43 57 (S2 first = 43) | Idle timing by RPM |
| Warm Up Enrichment Factor | 0x6A01 | 6×6 | ECT × IAT | ×/128, 8-bit | Warm-up enrichment |
| IAT Fuel Compensation | 0x6B9C | 6×4 | rpm 1000…6520 × IAT | 1.00 → 1.14 (S2 flatter) | IAT fuel compensation |
| Idle Electrovalve Setpoint | 0x7B83 | 3 | coolant raw 3 82 143 | 130 100 80 → 1300 1000 800 rpm | Idle target RPM by coolant |
| Decel Cutoff | 0x6FE6 | 4 | rpm 2000 3000 4000 5000 | 10 13 13 13 | Decel fuel-cut threshold |

Load-limiter values are in the same counts as the fuel/ign map load axis (top
breakpoint 190). The RR chip raises limiter 1 at 2000 rpm from 174 to 180 and
the closed-loop limit by 2–4 counts, consistent with its higher boost; that is
the cross-check that 034's names are right.

The 034 scale on the load axis (`x 0.5263`) is just 100/190: they showed load as
a percentage of the 3B's top breakpoint.

## The 3-bar sensor scale

`AAN Boost R2.ecu`: "Boost target in PSI with a 300kpa MAP sensor installed in
the ECU", decode `psi = raw × 0.170588 − 11.5`. That is

    kPa abs = raw / 255 × 300 + 21

a 300 kPa span with a 21 kPa offset at 0 V — not `raw/255×300`. Their 26 psi
overboost is raw 220, and 220 is exactly the maximum in the GT2871 and GT3071
boost chips' target tables. UrROM now has this as the **034 3-bar VMAP** sensor
preset. (Earlier note that the 034 files "might be 300 kPa" is confirmed.)

## What this means for a GT3071 on the 404 ECU

The 3B's main ECU has a hard load ceiling: **Load limiter 1** cuts fuel above
156–174 load counts, and the fuel/ign maps only resolve to load 190. With a
3-bar sensor on the boost board the boost side can be rescaled (sensor preset,
target/ceiling tables), but the main chip needs, in order:

1. Load limiter 1/2 raised (now editable), and the closed-loop limit re-checked.
2. Confirmation of how the 3B main ECU derives load and whether 190 can be
   re-gridded (prj did exactly this on the 551: fuel and ign map 2 re-gridded to a
   10…240 load axis for a 300 kPa sensor — see `prj_stock_aan-aby_551aa_0202.bin`).
3. Fuel map 4 / injector scaling for 440 cc, IAT compensation, ignition under
   23 psi — with a wideband on the car.

The GT3071 calibration half of the 034 AAN chip is the reference for step 3:
descramble it and load it as ROM B in Compare (cross-family pairing by role).
