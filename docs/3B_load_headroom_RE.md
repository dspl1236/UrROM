# 3B (404) load headroom: where the MAF signal stops

Traced 2026-09-14, continuing `docs/3B_injection_path_RE.md`. Addresses are
file offsets in `roms/3b_fuel-ign_404aa.bin`; every number below is identical
on the 3B, RR and S2 chips.

## 1. How the MAF gets in

- Timer 1 is configured as an **external pulse counter** (TMOD = 0x51: T1 =
  16-bit counter on the T1 pin, T0 = 16-bit timer). The hot-wire MAF's
  conditioned output (schematic block S220) is a frequency, not an ADC channel:
  none of the eight ADC inputs is the MAF.
- At every crank reference event the crank ISR captures TH1:TL1 into
  **42h:43h** and restarts the counter (0x031A): MAF pulses per crank segment.
- Task 0x0700 (crank-sync chain, scalars at 0x6322) turns that into air per
  revolution.

## 2. Air per revolution and LOAD

```
0x0735:  R4 = cal[0x30] (=3)                        exponent seed
         R7:R6 = (cal[0x2D]:cal[0x2E] (=0x00F4) + 46h) x cal[0x2F] (=185)   MAF linearisation
         normalise; x 42h:43h (pulses); >> R4       0x0761 — saturates at 0xFFFF (0x076A)
0x076E:  x 7Bh/128 when the adaptive term is active (7Ah == 0, 7Bh != 0x80)
0x0787:  R7:R6 = min(R7:R6, 8Bh x 25)               8Bh = table 0x6970[rpm]   <-- THE CAP
0x08D2:  40h:41h += (R7:R6 - 40h:41h) x 65h/256     filter, gain from table 0x698E
0x1D51:  LOAD (3Fh) = (40h:41h x 0xA4) >> 12         8-bit; wraps (not clamps) at 40h:41h >= 6394
```

- **46h** is a MAF-range offset: tables 0x7000 / 0x7019 / 0x7032 (6 x RPM,
  values 99…12 / 108…202 / 245…235) selected by flags 28h.6/28h.7 that task
  0x1C42 sets from the pulse-rate class (4Bh < 0x10 / < 0x40 / above). This is
  the piecewise hot-wire linearisation; leave it alone unless the MAF changes.
- **The cap table 0x6970** (`Air-per-rev cap`, 4 x RPM 800…6000) is 200 at every
  point on every stock chip. 200 x 25 = 5000 air counts = LOAD 200. So the stock
  ECU cannot register more than LOAD 200 no matter what the MAF does, and the
  fuel/ignition load axes end at 190. That is the headroom: **5 % above the map
  axis, then flat.** Beyond the top breakpoint READ_MAP holds the last column
  (0x0E16–0x0E23: fraction 0 at the last cell), so an over-range load is not a
  cut, it is a frozen column — the fuel cut comes only from the load limiters.
- The 8-bit LOAD would wrap at air 6394 (LOAD 256 → 0). The cap keeps
  40h:41h ≤ 6375 even at 255, so a raised cap is still wrap-safe: Bosch chose
  the ×25 for that.

## 3. What this means for a GT3071 at 23 psi

Stock 3B at ~11 psi sits near the axis top on a full pull; the map has a 190
column for a reason. Absolute-pressure ratio (23 + 14.7) / (11 + 14.7) ≈ 1.47,
and a hybrid at the same VE moves about that much more air — LOAD ~280 in
stock units. Raising the cap alone gets to 255 (1.34 ×). So the load **scale**
has to be compressed, not just uncapped:

1. `GAIN` at 0x6351 × k (k ≈ 0.7–0.75 for 23 psi; 255/k stock-load headroom).
2. Every LOAD-axis descriptor × k (22 tables: the four fuel maps, seven ignition
   maps, the idle-ignition family, filter gain, warm-up shaping, three small
   RPM × LOAD tables) so the stock calibration still lands in the same cells.
3. Load limiters and closed-loop lambda limits × k (they compare against LOAD).
4. Cap → 255.

`urrom.load_rescale.rescale_load(rom, k)` and `python -m urrom.cli rescale-load
rom.bin out.bin --factor 0.75` do exactly that and recompute the checksum. After
it, the top columns of every map (old 154/174/190 → 116/131/143 at k = 0.75)
cover the stock range and the last cell now extends to stock-load 340; the new
territory above the old top is where the GT3071 fuel and spark go, filled in
the editor with the 034 GT3071 R9 map as the reference shape.

prj did the equivalent on the 551 by re-gridding to a 10…240 axis on a
speed-density conversion. The same cap-and-8-bit-load ceiling exists there,
which is very likely why prjmod went MAF-less: a MAP sensor gives a load
signal that scales with the sensor instead of with a clamped pulse count. The
3B stays MAF-based here, so the scale moves at the gain instead.

## 4. The sensor itself

- 034 shipped every GT2871 / GT28RS / GT3071 kit for the AAN "stock MAF in all
  applications" at 22–23 psi to redline (docs/034_RipChip_definitions_RE.md), so
  the Bosch hot-wire meters that airflow. The 3B and AAN use the **same MAF**
  (confirmed by the owner, 2026-09-15), so that precedent transfers directly.
- The ECU-side pulse counter is 16-bit per crank segment and never gets near
  overflow; the firmware saturation at 0xFFFF (0x076A) is before the cap and
  is unreachable with a stock-range sensor.
- The measurement that settles it: log LOAD (3Fh, group 000 cell 2) over a full
  pull with KWPBridge on the present stage-1 chip. If it pins at 200 under
  boost the cap is already active on the current turbo; if it tops out lower,
  that number over 190 is the present margin.

## 5. Firmware constants summary

| addr | stock | meaning |
|---|---|---|
| 0x6343 | 20 | cranking air-per-rev / 25 (fixed while 28h.1) |
| 0x634F–0x6350 | 0x00F4 | MAF linearisation offset base (+ 46h) |
| 0x6351 | 185 | **GAIN** |
| 0x6352 | 3 | exponent seed |
| 0x6970 (4) | 200 ×4 | **cap**, load counts, by rpm 800/2000/4000/6000 |
| 0x7000/19/32 | see §2 | MAF range offsets A/B/C by rpm |
| 0x66F0 (32) | 0…0xFF ramp | transient enrichment by ΔLOAD index 4Ah (→ 66h) |
| 0x6710 (32) | 0…10 | transient ignition retard by 4Ah (→ 54h/55h) |
| 0x1D58 | 0xA4 | LOAD = air × 164 / 4096 (code constant) |
