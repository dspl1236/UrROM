# docs/ — what is here and where to start

Everything UrROM states about an ECU was traced in the firmware and written
up here. The app cites these pages on every cell (the provenance line under
each map description). Dates are when the trace was done; addresses are file
offsets in the named ROM unless a page says otherwise.

## Start here

| page | what it is |
|---|---|
| [UrROM_How_To.pdf](UrROM_How_To.pdf) | the illustrated guide: every tab, editing, compare, hardware patches, live data, the 3B recipes, what to log (rebuild with `python tools/build_guide.py`) |
| [UrROM_Master_Reference.md](UrROM_Master_Reference.md) | the app reference: supported ECUs, map inventory, checksums, CLI |
| [3B_what_to_log.md](3B_what_to_log.md) | the logging checklist for the car: what to record, how, and which decision each log settles |
| [ROADMAP.md](ROADMAP.md) | the agreed build order and what landed when |

## The 3B / RR / S2 (404 ECU, Motronic M2.3)

In the order the engine uses them:

| page | covers |
|---|---|
| [3B_RR_ECU_RE_notes.md](3B_RR_ECU_RE_notes.md) | the 447 907 404 board and firmware layout from chip reads and photographs (March 2026) |
| [3B_KW1281_RE.md](3B_KW1281_RE.md) | the K-line dialect: ID blocks, group 000's ten bytes, the read-RAM window KWPBridge uses for groups 100 and 101 |
| [3B_injection_path_RE.md](3B_injection_path_RE.md) | from MAF pulses to injector pulse: the background tasks and their table sets, fuel map selection, the fuel factor chain |
| [3B_load_headroom_RE.md](3B_load_headroom_RE.md) | where the MAF signal stops: Timer 1 counting, the linearisation tables, the air-per-rev cap, the 8-bit load wrap, `rescale-load` |
| [551_calibration_descriptors_RE.md](551_calibration_descriptors_RE.md) §3c, §3f | 3B ignition map selection and the coding plug (shared page with the 551) |
| [3B_boost_chip_RE.md](3B_boost_chip_RE.md) | the 8 KB boost MCU: target and duty tables, axes, knock evaluator, control loop, sensor scale, the 3-bar conversion |
| [3B_launch_control_RE.md](3B_launch_control_RE.md) | vwnut8392's spark-cut launch control diffed byte by byte, and the 404's 16-bit checksum |
| [3B_stage1_notes.md](3B_stage1_notes.md) | 3B / RR / S2 chip differences in numbers, the OEM combination, a conservative stage 1 |
| [3B_RS2_turbo_chipset.md](3B_RS2_turbo_chipset.md) | the K24-7200 chipset: RS2 boost profile on the 3B board, ADU ignition, greens at 3.8 bar |
| [3B_GT3071_plan.md](3B_GT3071_plan.md) | the bigger-turbo build order (injection path, MAF headroom, boost board, fuel and spark) and the 034 kit analysis |
| [3B_GT3071_step4_fuel_spark.md](3B_GT3071_step4_fuel_spark.md) | the scaffold chip for the GT3071: load re-grid, injector scaling, what the logs must fill in |
| [3B_MAF_hotfilm_notes.md](3B_MAF_hotfilm_notes.md) | **experimental**: the G70 hot-wire, the pulse path, what a hot-film swap needs, the RS4 MAF thread |

## The AAN / ABY / ADU / RS2 (551 ECU, Motronic M2.3.2)

| page | covers |
|---|---|
| [AAN_ABY_551_RE_notes.md](AAN_ABY_551_RE_notes.md) | calibration status per chip, map inventory, CRCs (March 2026) |
| [SAB80C535_FIRMWARE_RE.md](SAB80C535_FIRMWARE_RE.md) | the 551 firmware on the SAB80C535: vectors, memory map, first pass at the task structure |
| [551_calibration_descriptors_RE.md](551_calibration_descriptors_RE.md) | the split-bank 64 KB layout, the descriptor tables, ignition map selection by coding set, the coding plug ladder |
| [AAN_3B_port_notes.md](AAN_3B_port_notes.md) | porting 551 calibration into the 3B: `xcompare`, the RS2 D02 verdict, the prj base tune |
| [034_RipChip_definitions_RE.md](034_RipChip_definitions_RE.md) | what the 034EFI Rip Chip files contain, their 3B definition decoded, the 3-bar VMAP scale |

## Hardware references (both families)

Four compilations from the March 2026 sessions that overlap heavily;
[M232_ECU_Hardware_Reference.md](M232_ECU_Hardware_Reference.md) is the
longest and the one to read first. The others are
[M232_Hardware_Reference.md](M232_Hardware_Reference.md),
[M232_Master_Reference.md](M232_Master_Reference.md) and
[M2_3_2_Hardware_RE.md](M2_3_2_Hardware_RE.md). Sources: the Bosch
schematics (Y261 C20 / C27), prj's m232 repository, vwnut8392's S2Forum
threads, chip reads and board photographs.

## Other files

- `mapfinder_3b_404aa.xdf`, `mapfinder_aan_551aa.xdf`, `mapfinder_aby_551b.xdf`:
  TunerPro definitions generated from the descriptor decode, one table per
  firmware-referenced map, titled by address, shape and input. Useful for a
  second opinion in TunerPro; UrROM's own map list is the curated one.
- `img/`: the screenshots the guide is built from.
- The prj XDF for the 551 (`rs2_xdf/m232.xdf`, GPL-3) lives one level up.

## Conventions

- "CONFIRMED" on a map means the firmware's descriptor table references it
  on a real chip; "assumption" means the app says so too.
- RAM addresses are the 8051's internal RAM (`3Fh`), XRAM is external
  (`XRAM 0x5F`); bits are `byte.bit` (`20h.2`).
- The 404 ROM is flat 32 KB; the 551 is firmware low, calibration high, and
  `normalize_rom` hands tools the calibration half.
