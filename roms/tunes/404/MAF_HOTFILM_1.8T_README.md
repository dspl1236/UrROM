# Bosch 1.8T hot-film MAF on the 3B — experimental, untested

**No chip is published for this yet, on purpose.** The one number that
matters — how many pulses the 3B's voltage-to-frequency front end makes per
gram of air with the hot-film in place of the hot-wire — cannot be known from
the bench. It comes from one drive's log, and the command below turns that log
into the chip. Everything else (what to buy, how to wire it, what the ECU does
with the signal, the maths) is built and tested on synthetic data.

## Hardware

- **Sensor:** Bosch 1.8T hot-film element in the **69.85 mm VR6 / TT225
  housing** — 0280218042 or 0280218116. That bore is about the stock 3B
  housing's (the OE S2/3B meter is 72 mm ID), so the intake plumbing stays and
  the ECU's three pulse-rate ranges stay closest to where the stock curve
  lives. Not the 60 mm 1.8T housing. The step above it is the B5 RS4 element
  (0 280 218 067) in the 72 mm Alfa HFM5 housing 0 280 217 531, good for
  ~400 hp per the S2Forum thread (docs/3B_MAF_hotfilm_notes.md); same wiring,
  same procedure.
- **Wiring:** three wires — +12 V, signal ground, signal. AEB 4-pin: ground 1,
  signal ground 2, +12 V 3, signal 4. ATW/AUG/AWM 5-pin: +12 V 2, signal ground
  3, signal 5; pins 1 and 4 are the integrated IAT — **leave open**, the 3B has
  its own IAT.
- **Burn-off:** the hot-film has no burn-off element. The ECU's one-second
  1000 °C pulse at key-off has nothing to heat; leave that output unconnected.
  Do not route it into the hot-film's supply.
- **The gate check before any log:** scope the ECU's T1 pin (Timer 1 input) at
  idle, 3000 rpm cruise and on a pull. The stock hot-wire's pulse rate spans
  the three auto-ranged classes the firmware switches between (0x1CE8–0x1D4D:
  rate < 0x10 ×16, < 0x40 ×4, else ×1). If the hot-film's swing pushes the
  rate outside what the V/F stage handles, no calibration fixes that and the
  frequency-output hot-film fed straight to Timer 1 is the answer instead.

## What the chip changes, and only that

The 3B linearises its MAF with **one curve of offset versus pulse rate**, split
into three auto-ranged tables (0x7000 ten points, 0x7019 thirteen, 0x7032
ten, indexed by RAM 4Bh) plus a gain byte (0x6351):

    air per rev = (244 + offset(rate)) x GAIN x pulses >> exp

Fuel, ignition, load axes, limiters, boost: untouched. LOAD is defined in air
per rev, so once the new sensor produces the same air per rev as the hot-wire
did, every map means what it meant. That is the whole point of doing it this
way instead of re-tuning.

## The log

Two drives, same route, same steady states, one with each sensor:

1. **Stock hot-wire first, while it works.** This is the reference and it
   cannot be taken afterwards.
2. Hot-film fitted, same drive.

KWPBridge, 3B, **RAM group 101** (added for this): it reads pulses per crank
segment (42h:43h), the pulse rate (4Bh), the linearisation offset (46h),
air per rev (40h:41h), LOAD (3Fh) and rpm. Record with UrROM's Tools →
Start recording live data, then add a `point` column labelling each steady
state: `idle`, `cruise2k`, `cruise3k`, `cruise4k`, `wot`. A wideband lambda
column is optional and improves the fit (it corrects for the closed-loop
trim hiding part of the error). Required columns: `point, rpm, rate, pulses`.

Hold each steady state for a few seconds; the fit uses medians per point and
250-rpm bin, so a handful of clean seconds beats minutes of noise.

## The command

    python -m urrom.cli maf-swap roms/3b_fuel-ign_404aa.bin out.bin --stock-log stock.csv --new-log hotfilm.csv

It solves `M'(rate') = M(rate) x pulses / pulses'` at every matched operating
point, bins the answers onto the three tables, spills any overflow into the
gain byte, prints the before/after tables, and writes the chip with the
checksum. Points the drive never reached keep the stock shape scaled by the
nearest fitted ratio — the report says which.

For a bench-less first pass on a sensor believed to be a plain scale of the
hot-wire there is `--ratio R` (M' = M × R everywhere). It is a guess with a
knob, and it is the reason this folder holds no chip: the knob's value is
what the log measures.

## After burning

Closed loop will pull idle and cruise straight regardless; the number to
watch is the **long-term lambda correction** (group 000, or XRAM 0x0112 in
group 101's neighbour), which should sit where it sat on the hot-wire. If it
does not, the fit is off in that range and a second log fixes it. Then a
wideband pull: LOAD at redline should match the hot-wire log at the same
boost.

## Status

| item | state |
|---|---|
| firmware path, tables, auto-ranging | traced, tested (docs/3B_load_headroom_RE.md, docs/3B_MAF_hotfilm_notes.md) |
| fitting tool `urrom.maf_swap` / `maf-swap` | built, tested on a synthetic sensor |
| KWPBridge RAM group 101 | built, mock-tested |
| the chip | **waits for the two logs** |
