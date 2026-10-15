# What to log, and when — the 3B (404) on the car

Written 2026-10-14. Every tool in this repository that is still waiting on
something is waiting on one of the logs below. This page says, for each
situation, what to record, how, and which decision the numbers settle, so a
session with the car produces usable data the first time.

## Tools

- **KWPBridge** on a K-line cable, `--ecu 3b` (part numbers 447907404AA/A,
  857907404B, 895907404/A). The 3B profile takes **group 100** first: a
  read-RAM window over `36h…3Fh` and `53h…54h`, which carries UBAT, IAT,
  coolant, rpm (`3Ah`, 40 rpm per count, no saturation) and **LOAD (`3Fh`, the
  map axis unit)**. Group 000 is the fallback; its rpm byte saturates at
  2550 rpm, so it is fine for idle and no use on a pull. **Group 101** is the
  MAF window (`42h:43h` pulses, `4Bh` rate, `46h` offset, `40h:41h` air per
  rev, `3Fh`, `3Ah`). docs/3B_KW1281_RE.md has the decodes.
- **UrROM** → Tools → *Start recording live data* writes the stream to CSV
  and paints the trace on the open map. Name the file with the chip and the
  situation (`hybrid_2k-cruise_2026-10-20.csv`); the first line of the
  session log records which chip was loaded.
- A **boost gauge** and a **wideband** (any logging wideband; a `lambda`
  column in the CSV is enough). A multimeter for the sensor check.

## The checklist

| # | situation | record | decides |
|---|---|---|---|
| 1 | hybrid chip (3B fuel + S2 ign + RR boost), first drives | group 100 at a steady 2000 rpm cruise and through a gentle roll-on from 1800 rpm; note whether the stumble is there | whether the hybrid becomes the daily chip, or the 2k region of the fuel map is the next edit |
| 2 | any chip, third gear, 2000 → 6500 rpm at full throttle | group 100 (LOAD and rpm) | where LOAD sits against the 200 cap on the present turbo; this sets the GT3071 load-scale factor, which is a guess (0.75) until then |
| 3 | GM 3-bar MAP sensor, key on, engine off, before any chip is burned | the sensor's output voltage at atmosphere, and the ambient pressure if a weather station is handy | the preset: about 1.3 V matches `vmap300_034` (kPa = raw/255 × 300 + 21); about 1.0 V means a zero-offset part, use `lin300` |
| 4 | the present chip, any drive | the fault memory (KWPBridge reads it), the peak on the boost gauge, the coding number from ID block 3 | the baseline every later change is compared with |
| 5 | Bosch 550 injectors fitted, `3b_inj550_404aa.bin` burned | wideband lambda at idle and 3000 rpm cruise, five minutes warm, plus group 100 | the fuel trim; the chip scales fuel by 305/550 and the long-term correction says how far off that is |
| 6 | 3-bar sensor fitted, `3b_boost_404aa_3bar034.bin` burned (stock kPa) | the boost gauge peak on a pull, against item 4 | that the sensor conversion is right before any target is raised |
| 7 | 3-bar sensor, targets raised in steps | gauge peak, wideband on the pull, group 100 | each step; stop when lambda moves or knock retard appears in the log |
| 8 | hot-film MAF, if ever | group 101 on the stock hot-wire first, then the same route on the new element, with a `point` column (`idle`, `cruise2k`, `cruise3k`, `cruise4k`, `wot`) | the `maf-swap` input (roms/tunes/404/MAF_HOTFILM_1.8T_README.md) |

Items 1 to 4 need nothing bought. Items 5 and 6 are the winter work and
each wants its own session. Item 7 only after 6 reads right.

## How to hand a log over

A log is usable when it carries, in the file name or the first lines:

- the chip (file name or CRC32 from the UrROM overview tab) and the boost
  chip with it;
- the coding number and ambient temperature;
- for a pull: the gear and the rpm range; for a cruise: the speed held;
- the wideband's unit (lambda or AFR) if a column is present.

Several short clean segments beat one long noisy file: the fits use
medians per rpm bin, and ten seconds of a held state is plenty.

## Where each number goes

| log | tool it feeds | doc |
|---|---|---|
| 2k cruise LOAD and lambda | fuel map edit in the editor | docs/3B_stage1_notes.md |
| full-pull LOAD | `urrom.cli rescale-load --factor` | docs/3B_load_headroom_RE.md |
| sensor voltage | `urrom.cli boost-sensor --to` | docs/3B_boost_chip_RE.md (3-bar section) |
| idle and cruise lambda on 550s | `urrom.cli injectors` ratio check | docs/3B_injection_path_RE.md |
| boost steps with wideband | boost tab target tables | docs/3B_GT3071_step4_fuel_spark.md |
| MAF group 101 pair | `urrom.cli maf-swap` | docs/3B_MAF_hotfilm_notes.md |
