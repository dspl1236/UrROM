# GT3071 on the 3B, step 4: fuel and spark

Started 2026-09-15. Steps 1–3 gave the mechanism (injection path, load
scale, boost board). This step is the calibration itself, which is data-driven:
everything below is either a scaffold that can be built now, or a procedure
that needs the car.

## What can be built now: the scaffold

`python -m urrom.cli gt-scaffold roms/3b_fuel-ign_404aa.bin out.bin --factor 0.75 --top 225`

| stage | what it does | why |
|---|---|---|
| load rescale ×0.75 | MAF gain 185→139, all 22 load axes ×0.75, limiters and closed-loop limits ×0.75, cap→255 | the stock scale tops out at load 200; 23 psi needs ~280 stock-load |
| re-grid | fuel maps 1–4 and ignition maps 1–7 get a 16-point LOAD axis that keeps the first 13 scaled breakpoints (11…105) and adds 145 / 185 / 225; data bilinearly resampled | every cell in the stock range keeps its value; three new columns reach stock-load 300 |
| starter fill | new columns start as the old top column, fuel ×(1 + 0.08·frac), ignition −1.5°·frac | a conservative first guess; the wideband replaces it |
| limiter release | load limiter 1 → 250, limiter 2 → 200 | 034 set 254 on every GT3071 chip; the stock 174 would cut fuel at 23 psi |
| injectors | fuel maps 1–4 and the cranking table × stock/new (`--injector-ratio`; post-start and IAT/warm-up are relative and stay) | the 3B has no injector constant (docs/3B_injection_path_RE.md §6) |
| checksum | 0x7F00 recomputed | the ECU checks it at boot |

`roms/tunes/404/3b_gt3071_scaffold_k075.bin` is that build with the 305/550 = 0.555
injector ratio (stock 3B injector: Bosch 0 280 150 737, 305 cc at 3 bar).
**It is still not drivable** — the three new columns are guesses and the
turbo is not on the car — its purpose is to be opened in UrROM next to the
034 GT3071 R9 chip.

`roms/tunes/404/3b_inj550_404aa.bin` (`python -m urrom.cli injectors 3b.bin out.bin
--new-cc 550`) is the other thing the ratio makes possible: the stock 3B
calibration re-fuelled for the Bosch 550s and nothing else. That is the chip
for step 2 below.

## What the car has to supply, in order

1. ~~Injector ratio~~ 305 / 550 = 0.555 (both rated at 3 bar; the regulator
   pressure cancels as long as both injectors are quoted at the same
   reference). Applied to the scaffold and to `3b_inj550_404aa.bin`.
2. **Idle and cruise on the new injectors, stock turbo.** Burn the scaffold
   with the injector ratio and the stock RR boost chip, log lambda at idle,
   1500–3000 rpm part load. Closed loop will hide small errors; the
   long-term correction (XRAM 0x0112 / group lambda cells) shows the real
   one. Adjust the cranking / post-start tables if cold start is off.
3. **Boost board on the 3-bar sensor at stock kPa** (step 3 chip). Same drive.
   Nothing about fuel should change; if it does, the sensor scale is wrong.
4. **Raise boost in steps** with `boost-sensor --limits-psi` and the target
   tables in the editor: 14 → 17 → 20 → 23 psi. At each step a full pull
   with wideband and the UrROM live trace on Fuel Map 1: the trace shows
   which of the new columns the engine used, the wideband says what to do to
   them. Target 11.5–11.8 AFR (λ 0.78–0.80) under boost, the same numbers
   034 quotes. Ignition: knock recognised (P5.5 / KW1281 knock flag) is the
   only signal that matters; the 034 R9 map retards 1–3 raw in the boost
   band relative to stock, which is where the starter fill lands.
5. **IAT compensation and the closed-loop limit** last: the IAT table is
   1.00→1.14 stock; a GT3071 with an intercooler may want the S2's flatter
   version. The closed-loop limit (now ×0.75) decides where open loop starts;
   034 lowered theirs to load 54 on the AAN.

## Reference shapes

The 034 GT3071 R9 (440 cc) AAN calibration is the reference, opened as ROM B
in Compare (cross-family pairing by role). Its differences from 034's stock
rip are small and specific (docs/3B_GT3071_plan.md): fuel map renormalised
for the injectors, load limiter disabled, ~20 ignition cells retarded 1–3 raw
in the boost band, IAT compensation slightly lower. That is the whole tune.
The 3B scaffold puts the same knobs in the same places.

## What is deliberately not automated

Fuel values in the new columns and the spark in them. Any number written
there without a wideband log is a guess, and the guess is already in the
scaffold as a conservative ramp. The build tool makes the next scaffold from
the next set of logs a one-line operation; it does not choose the numbers.
