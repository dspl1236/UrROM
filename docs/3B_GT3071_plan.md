# GT3071 / K26 hybrid on the 3B (404 ECU): groundwork

Started 2026-09-14. Goal: a 3B fuel/ign + boost chipset for a GT3071-based
hybrid turbo with a 3-bar MAP, done on the car's own 404 ECU rather than an
AAN conversion.

## What 034's GT3071 tunes actually change (AAN, calibration half only)

Diffed with `urrom.descramble` against 034's own stock rip (same 034 firmware in
all of them, so every difference is calibration). Addresses are AAN 551AA.

| table (034 name) | AAN addr | stock rip | GT3071 R9 440cc | R9.1 550cc | R8 42 lb green tops | K24 Stage 1+ |
|---|---|---|---|---|---|---|
| Load Limiter 1 (fuel cut) | 0x8CBF | 192 192 192 176 170 | **254 ×5 (cut disabled)** | same | same | 210 ×5 |
| Load Limiter 2 (limp) | 0x8CCB | 140 130 130 120 110 | 210 200 200 190 180 | same | same | 200 ×5 |
| byte 0x8CE3 | 0x8CE3 | 196 | 210 | 210 | 210 | 210 |
| 5-byte table 0x8CEB (function TBD) | 0x8CEB | aa 76 5f 4a 32 | 65 3b 29 18 05 | 82 4b 31 1f 12 | 88 5e 4c 3b 28 | — |
| scalar 0x8380 | 0x8380 | 151 | 151 | **97** | 151 | 151 |
| Primary fuel map | 0x8DEA | 110…174 | **whole map replaced, 69…109** | **whole map replaced, 126…152, much flatter** | partly different from R9 | whole map, 65…129 |
| Warm-up enrichment | 0x8D71 | — | = stock | 24 bytes lower | = R9 | — |
| IAT fuel compensation | 0x8EF8 | 1.03…1.09 | slightly lower | = R9 | **all 1.00** | — |
| Decel cutoff | 0x8FB3 | 10 13 13 13 | same | 9 11 11 11 | same | — |
| Primary timing map | 0x9224 | — | ~20 cells, −3…+1 in the boost band | 16 cells +1 | = R9 | whole map, mostly +2 |
| Idle setpoint | 0x9B7A | 130 100 90 | same | 135 105 95 | same | — |
| Closed-loop lambda limit | 0x9CE7 | 57 63 62 62 | same | 54 54 54 54 | 60 50 in two cells | — |
| Closed-loop limit (limp) | 0x9CF5 | 0 ×6 | same | 54 ×5, 40 | same | — |

Reading of it:

- **The fuel cut is what stops a stock chip at big boost.** 034 sets load
  limiter 1 to 254 at every rpm on the GT3071 chips and to 210 on the K24
  stage 1+. The 3B's equivalent is now editable (`Load limiter 1 (fuel cut,
  0x6951)`, stock 174…156).
- **Injector size is a whole-map rewrite, not a scalar**, on the 034 firmware.
  440 → 550 cc also flips one calibration byte (0x8380, 151 → 97) and the map
  values go *up* and flatten, so that byte is an injector or scaling constant
  and the map is renormalised around it. Whether the 3B firmware has the same
  constant is the first RE question below.
- 42 lb green tops vs 440 cc Siemens: same nominal flow, different map in the
  low-load half and IAT compensation zeroed — the injector's low-pulse
  behaviour matters more than its rating.
- Timing barely moves for the GT3071: a handful of cells retarded 1–3 raw
  (0.75–2.25°) in the boost band. The power is fuel and boost, not spark.

## Injector choice

034 sold the GT3071 chip for 42 lb Bosch green tops, 440 cc Siemens and 550 cc
Siemens EV14, and quoted 85–90 % duty at 7200 rpm on the 440s for 346 whp.
That is no headroom. The 550 cc EV14 (Siemens Deka 60 lb) is the one to build
for: ~70 % duty at the same power, linear enough at small pulses to idle on a
batch-fired M2.3, and 034 added the R9.1 revision for exactly that reason.
Check the 3B's regulator pressure before ordering: flow ratings are quoted at
3 bar and the 3B is believed to run 3.0 bar where the AAN runs 4.0.

## Order of work on the 404

1. ~~Find the 3B's injection-time path~~ **Done 2026-09-14**, see
   `docs/3B_injection_path_RE.md`. Pulse = MAF air-per-rev × fuel-map factor
   (Q7) × warm-up / IAT / lambda terms, Timer 0 one-shot on P1.3. No injector
   constant: a 550 cc swap is a rescale of the four fuel maps plus the cranking
   and post-start tables (×stock/new), or of the MAF scale bytes at 0x6328.
   Load is MAF-derived; the MAP sensor only feeds a neutral 3-point table, so
   the 3-bar sensor matters on the boost board only. Fuel Map 4 is the
   coding-2/7 map under the boost-board flag, not a WOT map.
2. ~~Load ceiling~~ **Traced 2026-09-14**, see `docs/3B_load_headroom_RE.md`.
   The MAF is a pulse counter on Timer 1; air-per-rev is clamped by the
   `Air-per-rev cap` table (0x6970, = load 200 on every stock chip) 5 % above
   the 190 axis top, then the maps hold their last column. A GT3071 at 23 psi
   needs ~LOAD 280 in stock units, so the scale is compressed, not uncapped:
   `python -m urrom.cli rescale-load 3b.bin out.bin --factor 0.75` scales the
   MAF gain, all 22 load axes, the limiters and the closed-loop limits by k and
   raises the cap to 255 (stock-load 340 of headroom). Still to do on the car:
   log LOAD (group 000) on a full pull with the present chip to see the margin.
3. ~~Boost board~~ **Tooled 2026-09-15** (docs/3B_boost_chip_RE.md, last
   section). Targets and overboost thresholds are absolute counts, ceilings /
   corrections / adaptive steps are deltas, duty tables are sensor-independent;
   `python -m urrom.cli boost-sensor rr_boost_404b.bin out.bin --to vmap300_034`
   re-encodes them for the 3-bar sensor, `--limits-psi 26 --scale-gains` opens
   the ceiling and keeps duty-per-kPa. Overboost on this board is a duty
   release, not a fuel cut. First on-car step: `rr_boost_404b_3bar034.bin`
   with the 3-bar sensor fitted must drive like the RR chip.
4. **Fuel and spark.** Fuel map 1/4 for the injectors, IAT compensation,
   closed-loop limit, and the GT3071 timing retards ported by cell through
   Compare with the 034 R9 chip as ROM B. Wideband on the car for every step.

## Hardware assumed

GT3071 / K26 hybrid, 3.0 bar MAP, Bosch 550 cc injectors (owner's choice), uprated pump,
stock MAF (same part as the AAN),
RS2-style manifold optional. Fuel pressure regulator to be confirmed.
