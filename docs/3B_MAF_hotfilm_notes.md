# The 3B's MAF, and what a hot-film swap would take

> **EXPERIMENTAL — untested.** Nothing in the swap section has been tried on a
> car. It is a build-out for when it is needed (a failing sensor, or well north
> of 500 hp), kept out of the how-to guide on purpose until it has been run.
> The stock-sensor and firmware sections are fact.

Notes gathered 2026-09-16 from the firmware trace (docs/3B_load_headroom_RE.md),
the HachiROM 7A MAF work (github.com/dspl1236/HachiROM, docs/MAF_SENSOR_WIRING.md)
and SJM Autotechnik's 20V MAF page (sjmautotechnik.com/trouble_shooting/20vmassa.html,
citing Audi's 1991 200 20V and S4/V8 Motronic training publications).

## The stock sensor (G70)

- Bosch temperature-regulated **hot-wire**, mounted between the air cleaner
  and the turbo inlet boot, screens at both ends to settle the flow. The
  platinum wire is held **100 °C above the incoming air** by a companion air
  temperature element; the current needed to hold that differential is the
  mass-flow signal. Because it measures mass directly, no air-temperature or
  density correction is needed downstream — that is why the 3B has only a
  small IAT fuel compensation table (0x6B9C) and no baro term in the fuel
  factor.
- **Burn-off**: after the engine is switched off the wire is heated to about
  1000 °C for one second to clean it. The ECU stays awake to do this, so the
  ECU must not be unplugged for at least 20 s after key-off.
- The 3B and AAN use the **same sensor** (owner-confirmed).

## How the 3B ECU reads it (from the trace)

- The MAF output is **not an ADC channel**. The board's S220 block converts it
  to a frequency and **Timer 1 counts pulses** (TMOD 0x51, external counter on
  the T1 pin); the crank ISR captures the count per crank segment into 42h:43h.
- Air per revolution = pulses × (0x00F4 + range offset 46h) × GAIN (0x6351)
  >> exp, capped by the table at 0x6970, then LOAD = air × 0xA4 >> 12.
- **46h is the hot-wire linearisation**: three 6-point rpm tables
  (0x7000 / 0x7019 / 0x7032) selected by pulse-rate class (4Bh < 0x10 /
  < 0x40 / above). This is the piece a different sensor changes.
- Failure behaviour per Audi's training text, which the firmware agrees
  with: at idle the ECU runs the idle timing table and a **precalculated
  amount of air** (the fixed air-per-rev at 0x6343 = 20 × 25 counts is that
  number while 28h.1 is set); at part load with the idle switch open ignition
  goes to a fixed 20° BTDC and the mixture is leaned; idle and lambda
  adaptation stop and the evaporative valve is switched off. Diagnostics
  recognise "signal too low" (open / short to ground) and "signal too high"
  (short to plus). On the S4/S6 the MAF is not used during starting at all
  (baro sensor and temperature-fixed values instead) — the 3B has no baro
  sensor and uses the cranking table 0x6BC8 with the fixed air.

## What a hot-film (HFM) swap needs on this ECU

Two separate questions, and the second is the work:

1. **Does the sensor's output fit the V/F front end?** The S220 stage was
   designed around the hot-wire's voltage swing. A hot-film that swings
   differently produces a pulse rate per gram that may fall outside the three
   range classes the firmware switches on, and then the range-offset tables
   have to be rewritten rather than trimmed. Check with a scope on the T1 pin:
   idle, 3000 rpm cruise, and a pull. The frequency-output hot-film variants
   avoid this entirely — feed Timer 1 directly, bypass the V/F, and
   pulses-per-gram is a datasheet number.
2. **The calibration**: the three range-offset tables, the offset/GAIN/exponent
   bytes at 0x634F–0x6352, and possibly the cap (0x6970). Fuel and ignition
   maps do not change, because LOAD is defined in air-per-rev, not in sensor
   volts. That is the difference from HachiROM's 7A patch, which had to move
   the 16-point MAF axis of an analogue-input ECU; 034's equivalent 7A
   calibration touched the linearisation, fuel, timing and enrichment tables
   (682 bytes) — the honest size of a MAF change when the sensor's curve is
   not matched first.
3. **Burn-off**: a hot-film has no burn-off element; the ECU's one-second
   1000 °C pulse at key-off simply has nothing to heat. Leave the output
   unconnected or on a dummy load; do not feed it into the hot-film's supply.
4. **No CO pot to patch** (the 7A's pin-4 problem): the 3B's idle trim is the
   lambda loop plus the idle tables; the coding plug is a resistance ladder on
   its own ADC channel.

## Calibrating on the road, no flow bench

The raw pulse count per segment (42h:43h) and LOAD (3Fh) are readable over
KW1281 with the 3B's read-RAM command, which KWPBridge speaks. Procedure:

1. **Log the stock sensor first**, while it still works: rpm, 42h:43h, LOAD,
   lambda, on idle / cruise / full pull. That log is the reference curve.
2. Fit the hot-film, log the same drive. The ratio of new pulses to old
   pulses at the same rpm and lambda-corrected fuel is the new sensor's
   curve relative to the hot-wire, class by class.
3. Rewrite the range offsets and GAIN in the editor so the new pulse count
   gives the old air-per-rev; confirm with lambda under closed loop (the
   long-term correction should return to where it was on the hot-wire).

## The one candidate worth building out

The **Bosch 1.8T hot-film element in the 69.85 mm VR6 / TT225 housing**
(0280218042 / 0280218116). That bore is about the stock 3B housing's size, so
the intake plumbing stays and the pulse-rate classes stay closest to where
the hot-wire tables expect them; the 60 mm 1.8T housing would move the whole
curve for no reason. Three wires: +12 V, signal ground, signal (AEB 4-pin, or
the ATW/AUG/AWM 5-pin with its integrated IAT left open). The 7A-era AAH
housing option in the HachiROM notes does not apply here.

## When it is worth doing

Not for the K24-7200 or a GT3071 at 23 psi: the stock hot-wire meters that
airflow (034 ran every kit on it), and the firmware ceiling is the cap and
the 8-bit load, which `rescale-load` handles. It is worth doing when the
stock sensor is failing or unobtainable, when the 70 mm housing is the
restriction, or north of ~500 hp, where the sensor and the housing become the
limit at the same time. At that point the frequency-output hot-film with a
computed linearisation is the cleaner route on this ECU; speed density
(prjmod's answer on the 551) trades the mass measurement for a VE model and
is the bigger job.
