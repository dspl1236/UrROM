# vwnut8392's 3B spark-cut launch control (S&M Msport Patch V1.01) — diffed 2026-09-09

*Source: TunerPro-patched `447907404A` chip (user-supplied) vs
`roms/3b_fuel-ign_404a.bin`. Thread: S2Forum m232.org 2040975. The XDF
itself is a protected TunerPro file and cannot be read outside TunerPro; the
diff is the whole patch.*

## What changes (86 bytes)

| where | stock | patched | meaning |
|---|---|---|---|
| `0x06C9` | `E5 53 95 58` (`MOV A,53h ; SUBB A,58h`) | `12 62 A0 00` (`LCALL 62A0h ; NOP`) | hook in the ignition path, right after `53h` is computed |
| `0x16B1` | `30 08 xx` (`JNB 21h.0`) | `00 00` | stock use of bit `21h.0` removed |
| `0x3408` | `20 08 04` (`JB 21h.0`) | `00 00 00` | protocol descriptor always `0x6407` |
| `0x3657` | `30 08 02` (`JNB 21h.0`) | `00 00 00` | coding number always takes the +9 branch |
| `0x62A0` | `FF` ×49 | routine below | free area at the end of the firmware |
| `0x7F00` | checksum | checksum | recomputed (see below) |
| `0x7F1A…` | `PMC 0261200451 1267356462` | `S&M Msport Patch V1.01` | K-line ID blocks 3–5 |

## The routine

```
62A0  PUSH DPL ; PUSH DPH
62A4  MOV A,3Ah ; CJNE A,#B8h ; JNC exit      ; rpm/40 < 184  (7360 rpm ceiling)
62AB  MOV DPTR,#BE00h ; LCALL 135Dh           ; ADC channel 0 = throttle pot
62B1  CLR C ; CJNE A,#D8h ; JC exit           ; TPS >= 216/255 (85 %)
62B7  JB 21h.0,exit                           ; bit 21h.0 set = clutch UP -> no launch
62BA  MOV A,3Ah ; CJNE A,#71h ; JC exit       ; rpm/40 >= 113 (4520 rpm launch)
62C1  MOV 54h,#65h                            ; spark: base-timing byte forced
62C4  MOV 58h,#0Dh                            ; dwell byte forced
62C7  exit: CLR C ; MOV A,53h ; SUBB A,58h    ; the two replaced instructions
62CC  POP DPH ; POP DPL ; RET
```

So: clutch down, throttle over 85 %, rpm between the launch value and the
ceiling → `54h` and `58h` are forced every ignition calculation, which is
the hard spark cut. Everything else runs stock.

**Bit `21h.0` is ECU pin 38.** The patch wires pin 38 to the clutch switch
(clutch up = grounded), and the three NOPs remove the ECU's own readers of
that bit so the coding logic no longer reacts to it. Pin 38 is one of the
three coding-plug pins (docs 3f), which means the 3B's coding plug is one
analogue line (ADC ch4) plus at least one digital line that reaches the
firmware as `21h.0` through the boost board's status byte.

## The checksum the patch "corrected"

Every 404 chip we hold (3B A/AA, RR, S2) stores a **16-bit big-endian sum of
bytes `0x0000…0x7EFF` at `0x7F00`**, directly in front of the ID string. The
firmware verifies it at boot (`0x497A`…`0x49AC`: sums pages 00…7E, compares
with `0x7F00`/`0x7F01`, sets `2Eh.0` and logs a fault on mismatch). The V8
404H uses a different scheme. UrROM now verifies it on load, flags it in the
health scan, and recomputes it on save for 404/RR chips.

## UrROM

Hardware tab → "3B Spark-Cut Launch Control": Apply installs the routine
into the free area, hooks it and NOPs the three tests; Revert restores the
stock bytes exactly. UrROM keeps the factory ID text so the K-line
identification stays stock, and the checksum is applied on save. Once
applied, the five bytes vwnut8392 exposes as XDF scalars are editable in the
card: launch rpm (×40), throttle threshold, rpm ceiling, spark and dwell raw.
Applies to 3B (A and AA), RR and S2 3B chips; the 404H V8 is excluded.

Hardware, per the thread: cut the pin-38 wire and route it to a stock
cruise-control clutch switch. Off-road use; hard spark cut can damage the
engine, turbo and exhaust if abused.
