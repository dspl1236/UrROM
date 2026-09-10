# UrROM — Credits and Acknowledgements

UrROM is an open-source ROM editor for Bosch Motronic M2.3 / M2.3.2 ECUs.
It stands on a decade of community reverse-engineering work. The following
people and projects made it possible.

---

## ROM Layout and Map Addresses

**prj** (`github.com/prj/m232`)
Map addresses, decode formulas, and ROM layout for the standard AAN/ABY
build-0x0202 firmware were taken directly from prj's `m232.xdf` (TunerPro
XDF format, 2018).  This covers:
- Fuel enrichment P/T: `0x0E13` (working half)
- Ignition P/T no-knock: `0x125F`
- MAP target, WGDC, knock thresholds, and 130+ additional tables
- Confirmed decode equations: `1/(X/128)*14.7` for fuel, `0.75*(X-30)` for ignition

**vwnut8392 / S2 Forum community** (`s2forum.com`, `github.com/vwnut8392/m232`, GPL-3.0)
The 3B spark-cut launch control (S&M Msport V1.01) that the Hardware tab installs is his patch,
carried byte for byte with its ID text; UrROM only ports the apply/revert and exposes the scalars.
Map addresses for the ADU/RS2 aftermarket (build `0x4533`) firmware via `RS2.xdf`:
- Fuel enrichment: `0x2E17`
- Ignition maps 1–7: `0x30AC` through `0x3931`
- Ignition decode: `raw × 0.6491 − 8.2186 = °BTDC` (XDF ZEq formula)
- Axis arrays: 16-point RPM and load axes confirmed against real RS2 ROM

**PRJ MapFinder** (S2 Forum, 0261200484 scan)
3B/RR map addresses confirmed via MapFinder output for build `0xF004`:
- Fuel maps: `0x6A6A`, `0x6BF8`, `0x6D50`, `0x6E74` (Bosch descriptor + 36 bytes)
- Ignition maps: `0x7052`, `0x7643`, `0x77AB`, `0x7913`

---

## .034 Rip Chip File Format

**034 EFI** (`034motorsport.com`)
The `.034` file format and the Rip Chip hardware/software were created by
034 EFI (now 034Motorsport). The descramble algorithm was reverse-engineered
from `ECUGUI.jar` (Rip Chip Tuning Software v1.0.0, 2012) by decompiling
`com.bprog.model.BitShift` and `com.bprog.model.MakeNative` using the
Procyon decompiler.

The algorithm (confirmed self-inverse, verified against real ROMs):
```python
# BitShift.algOne(byte, a=0xAA, b=0x55)
az = ((b & 0xAA) >> 1) | (((b & 0x55) << 1) & 0xFF)  # swap adjacent bit pairs
result = ((az >> 4) & 0x0F) | ((az << 4) & 0xF0)      # swap nibbles
```

The `.ecu` ECU definition format (Java serialised objects, not yet supported)
stores map addresses, checksum parameters, and calibration data for each
ECU variant. This work is attributed to the 034 EFI engineering team.

---

## Factory Documentation

**Bosch / Audi AG**
Factory service documentation (`AAN_ABH_manual.pdf`, `M2_3_2 Schematics.pdf`)
included in prj's repository and referenced for ECU pin assignments and
electrical specifications.

---

## ROM Dumps

Real ROM files used to verify map addresses and CRCs:

| File | Source | Status |
|------|--------|--------|
| `adu_fuel-ign_551c.bin` | Community / S2 Forum | Verified REAL ROM |
| `aby_fuel-ign_551aa.bin` | Community / S2 Forum | Verified REAL ROM |
| `stock fuel.BIN` (3B) | Community | Verified REAL ROM |
| `stock_AANABY_27c512.bin` | prj / github.com/prj/m232 | Verified REAL ROM |
| `bigturbo_WMI_27c512.bin` | prj / github.com/prj/m232 | Verified REAL ROM |
| `stock.BIN` (V8 PT) | Community | Verified REAL ROM |
| `ABT Chip Verified.bin` (V8 PT) | Community | Verified REAL ROM |

---

## Tools

- **TunerPro RT** — XDF format reference
- **PRJ MapFinder** — automated map detection in 8051 ROMs
- **Procyon Decompiler** — Java bytecode decompilation for .034 format RE
- **Ghidra** (NSA / open source) — 8051 disassembly for checksum investigation

---

## Disclaimer

UrROM is provided for educational and research purposes.
Always verify any ROM modification on a wideband O2 sensor before road use.
Map addresses marked PROVISIONAL or UNCONFIRMED must not be trusted for
safety-critical modifications without independent verification.

The authors of UrROM have no affiliation with 034Motorsport, Bosch, or Audi AG.
The `.034` format reverse-engineering is published under fair use for
interoperability purposes.
