"""
urrom/ecu_profiles.py
=====================
ECU variant definitions, ROM layout, map address tables, and checksum
logic for Bosch Motronic M2.3 / M2.3.2.

Architecture overview
---------------------
5-cylinder 2.2 20vT ECUs are dual-processor:
  Main chip  — stored as 64KB (32KB working half mirrored twice).
               Working half = UPPER half of 64KB file (offset 0x8000-0xBFFF).
               Fuel maps, ignition maps, lambda, idle, temperature corrections.

  Boost chip — 8KB working half (stored as 32KB or 64KB mirrored).
               Boost target table, knock threshold, N75 duty cycle.

V8 ECUs have a single 32KB EPROM (flat file, no mirroring needed).

3B / RR ECUs use a flat 32KB file layout (not doubled).
Maps use Bosch embedded descriptor format: header [descriptor, count, axis...]
precedes the map data. MapFinder and our tools store the HEADER address,
data starts 36 bytes later (2 + 16 axis bytes + 2 + 16 axis bytes).

551A/551AA/551C variants:
Maps XDF addresses are into the 64KB doubled file directly.
Working half offset = XDF address - 0x8000.
The XDF Address points to the MAP DATA (not the header).
Header (with axis data) sits immediately before the data address.

Checksum (all variants):
  checksum   = sum(working_half[0x0000:0x3FFA]) & 0xFFFF
  complement = 0xFFFF - checksum
  Stored at working_half[0x3FFA:0x3FFC] = checksum (big-endian)
             working_half[0x3FFC:0x3FFE] = complement (big-endian)
             working_half[0x3FFE:0x4000] = Bosch build number (read-only)

Confirmed map addresses (working half offsets for 551x, file offsets for 3B/V8):
  Source: RS2.xdf (vwnut8392, S2Forum) + direct verification against bin files
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Callable
import zlib


# ── Axis / value conversion factors ──────────────────────────────────────────

def rpm_decode(raw: int) -> float:
    """Bosch RPM axis: raw × 40 = RPM."""
    return raw * 40.0

def load_decode(raw: int) -> float:
    """Bosch load axis descriptor 0x3F: raw × 0.05 = ms injection."""
    return raw * 0.05

def temp_decode(raw: int) -> float:
    """Coolant / IAT descriptor 0x38/0x37: raw × 0.75 − 48 = °C."""
    return raw * 0.75 - 48.0

def ign_decode(raw: int) -> float:
    """
    Ignition timing decode for 551C/551AA/551A variants (AAN, ABY, ADU).
    Formula from RS2 XDF (vwnut8392): raw × 0.6491 − 8.2186 = °BTDC.
    Input is unsigned byte (0–255).
    Verified: raw=49 → 23.6°BTDC ✓ (RS2 stock part-throttle).
    """
    return raw * 0.6491 - 8.2186

def ign_encode(deg: float) -> int:
    """°BTDC → raw byte for 551C/551AA/551A variants."""
    return max(0, min(255, int(round((deg + 8.2186) / 0.6491))))

def ign_decode_3b(raw: int) -> float:
    """
    Ignition timing decode for 3B/RR/V8 variants (404 codebase).
    Standard Bosch Motronic formula: raw × 0.75 − 22.5 = °BTDC.
    Input is unsigned byte (0–255).
    Verified: raw=0x31=49 → 14.2°BTDC ✓ (3B S2 stock part-throttle).
    """
    return raw * 0.75 - 22.5

def ign_encode_3b(deg: float) -> int:
    """°BTDC → raw byte for 3B/RR/V8 variants."""
    return max(0, min(255, int(round((deg + 22.5) / 0.75))))

def fuel_decode(raw: int) -> float:
    """Fuel map: 128 = stoich reference. Return as relative value."""
    return float(raw)

def fuel_encode(val: float) -> int:
    return max(0, min(255, int(round(val))))


# ── Map definition ────────────────────────────────────────────────────────────

@dataclass
class MapDef:
    name:        str
    description: str
    main_addr:   int              # working-half offset (or file offset for 3B/V8)
    rows:        int              # RPM axis length
    cols:        int              # Load axis length
    map_type:    str = "fuel"     # "fuel" | "ign" | "boost" | "1d" | "raw"
    unit:        str = ""
    decode:      Optional[Callable] = None
    encode:      Optional[Callable] = None
    chip:        str = "main"     # "main" | "boost"
    confidence:  str = "CONFIRMED"  # "CONFIRMED" | "PROVISIONAL" | "UNCONFIRMED"
    notes:       str = ""

    @property
    def size(self) -> int:
        return self.rows * self.cols


# ── ROM variant ───────────────────────────────────────────────────────────────

@dataclass
class ROMVariant:
    name:         str
    software_id:  str
    engine_codes: list[str]
    ecu_pns:      list[str]
    bosch_pns:    list[str]
    dual_eprom:   bool = True
    # working_half_offset: byte offset from start of physical file to start of
    # working half. 0x8000 for 64KB doubled files, 0 for 32KB flat files.
    working_half_offset: int = 0x8000
    main_maps:    list[MapDef] = field(default_factory=list)
    boost_maps:   list[MapDef] = field(default_factory=list)
    notes:        str = ""

    @property
    def all_maps(self) -> list[MapDef]:
        return self.main_maps + self.boost_maps


# ── ROM constants ─────────────────────────────────────────────────────────────

MAIN_CHIP_PHYSICAL  = 0x10000   # 64KB physical (doubled) for 551x
MAIN_CHIP_WORKING   = 0x8000    # 32KB working half
BOOST_CHIP_WORKING  = 0x2000    # 8KB boost chip working half
BOOST_CHIP_PHYSICAL = 0x8000    # 32KB boost chip physical

# Working half layout (offsets within the 32KB working half)
CHECKSUM_RANGE_END  = 0x3FFA
CHECKSUM_ADDR       = 0x3FFA    # checksum high byte
COMPLEMENT_ADDR     = 0x3FFC    # complement high byte
BUILD_NUMBER_ADDR   = 0x3FFE    # Bosch internal build number

WORKING_HALF_OFFSET = 0x8000    # byte offset of working half in 64KB doubled file
MIRROR_OFFSET       = 0x8000    # used in write_map to locate the mirror region

# 3B/RR/V8 are flat 32KB files (no mirror)
FLAT_32K            = 0x8000    # 32KB flat file size


# ── Confirmed map addresses — 551C / ADU (RS2) ───────────────────────────────
#
# Source: RS2.xdf (verified against adu_fuel-ign_551c.bin)
# XDF addresses → working half = XDF address - 0x8000
# Verified by checking decoded values against expected timing/fuel ranges.
#
# RPM axis (16 points): 600,1000,1240,1520,1760,2000,2520,3000,3520,4000,4600,5200,5720,6000,6520,7200
# Load axis (16 points): 2,6,9,13,16,20,24,28,32,35,39,42,46,50,57,70
# Decode: raw × 0.6491 − 8.2186 = °BTDC (XDF ZEq formula)

_RPM_AXIS_551 = [600,1000,1240,1520,1760,2000,2520,3000,3520,4000,4600,5200,5720,6000,6520,7200]
_LOAD_AXIS_551 = [2,6,9,13,16,20,24,28,32,35,39,42,46,50,57,70]

_MAPS_551C_MAIN = [
    # Main fuel map — 16×16, centred at 128 (stoich)
    MapDef("Part Throttle Fuel",
           "Main fuelling map. 128 = stoich reference. Higher = richer.",
           main_addr=0x2E17, rows=16, cols=16,
           map_type="fuel", unit="relative",
           decode=fuel_decode, encode=fuel_encode,
           confidence="CONFIRMED",
           notes="Verified: values 128-145 at light load, RS2 stock"),

    # Seven 16×16 ignition maps — part throttle variants
    # Multiple copies selected by throttle position / conditions
    MapDef("Ign Map 1 (PT primary)",
           "Primary part-throttle ignition map. Decode: raw×0.6491−8.2186=°BTDC",
           main_addr=0x30AC, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Verified: 22-24°BTDC at PT. Most-used ign map."),

    MapDef("Ign Map 2",
           "Part-throttle ignition map variant 2.",
           main_addr=0x3263, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Same axis as Map 1. Slight variation in advance values."),

    MapDef("Ign Map 3",
           "Part-throttle ignition map variant 3.",
           main_addr=0x3387, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map 4",
           "Part-throttle ignition map variant 4.",
           main_addr=0x3598, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map 5",
           "Part-throttle ignition map variant 5.",
           main_addr=0x36BC, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map 6",
           "Part-throttle ignition map variant 6.",
           main_addr=0x380D, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map 7",
           "Part-throttle ignition map variant 7.",
           main_addr=0x3931, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    # Rev limit
    MapDef("Rev Limit",
           "Fuel cut RPM. Address UNCONFIRMED — 0x3FF0 gives idle-speed values "
           "in real ROMs. Rev limit is likely embedded in 8051 code as an "
           "immediate compare; needs Ghidra disassembly to locate.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM",
           confidence="UNCONFIRMED",
           notes="Scan of real ROMs shows 0x3FF0 = ~700 RPM range, not rev limit."),
]

# ── Confirmed map addresses — 551AA / ABY ─────────────────────────────────────
#
# Verified: aby_fuel-ign_551aa.bin has same addresses as 551C.
# ABY and AAN share the same codebase layout.

_MAPS_551AA_MAIN = [
    MapDef("Part Throttle Fuel",
           "Main fuelling map. 128 = stoich reference.",
           main_addr=0x2E17, rows=16, cols=16,
           map_type="fuel", unit="relative",
           decode=fuel_decode, encode=fuel_encode,
           confidence="CONFIRMED",
           notes="Same address as 551C. Verified on ABY bin."),

    MapDef("Ign Map 1 (PT primary)",
           "Primary part-throttle ignition map.",
           main_addr=0x30AC, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Verified on ABY: 20-22°BTDC at PT."),

    MapDef("Ign Map 2",
           "Part-throttle ignition map variant 2.",
           main_addr=0x3263, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map 3",
           "Part-throttle ignition map variant 3.",
           main_addr=0x3387, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map 4",
           "Part-throttle ignition map variant 4.",
           main_addr=0x3598, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Verified on ABY: 20-22°BTDC at PT."),

    MapDef("Ign Map 5",
           "Part-throttle ignition map variant 5.",
           main_addr=0x36BC, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Verified on ABY: 20-21°BTDC at PT."),

    MapDef("Ign Map 6",
           "Part-throttle ignition map variant 6.",
           main_addr=0x380D, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Verified on ABY: 20-22°BTDC at PT."),

    MapDef("Ign Map 7",
           "Part-throttle ignition map variant 7.",
           main_addr=0x3931, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Verified on ABY: 20-21°BTDC at PT."),

    MapDef("Rev Limit",
           "Fuel cut RPM. Address UNCONFIRMED — needs disassembly.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM",
           confidence="UNCONFIRMED"),
]

# ── 3B / RR map addresses ─────────────────────────────────────────────────────
#
# Source: PRJ MapFinder output (0261200484_1267356530_A810_Audi_S2_MapFinder-3b.txt)
# 3B uses FLAT 32KB file. MapFinder addresses are direct file offsets.
# Each entry points to the MAP HEADER (descriptor + axis bytes).
# Actual map data starts 36 bytes after header address (2+16+2+16 = 36).
#
# 8 confirmed 16x16 maps. First 4 are FUEL (high values 117-205 raw = injection).
# Last 4 are IGNITION (values 40-88 raw → 12-30°BTDC decoded).
#
# RPM axis (×40): [400,240,280,240...] — irregular axis stored in header.
# We read axes dynamically from the header bytes.
#
# Confirmed addresses (file offset of HEADER):
#   Fuel maps:  0x6A6A, 0x6BF8, 0x6D50, 0x6E74
#   Ign maps:   0x7052, 0x7643, 0x77AB, 0x7913
# Data offset = header + 36

_MAPS_3B_MAIN = [
    MapDef("Fuel Map 1",
           "Part-throttle fuel map 1. Header @ 0x6A6A, data +36 bytes.",
           main_addr=0x6A6A + 36, rows=16, cols=16,
           map_type="fuel", unit="raw",
           decode=fuel_decode, encode=fuel_encode,
           confidence="CONFIRMED",
           notes="Verified from MapFinder + bin. Values 117-205."),

    MapDef("Fuel Map 2",
           "Part-throttle fuel map 2.",
           main_addr=0x6BF8 + 36, rows=16, cols=16,
           map_type="fuel", unit="raw",
           decode=fuel_decode, encode=fuel_encode,
           confidence="CONFIRMED"),

    MapDef("Fuel Map 3",
           "Part-throttle fuel map 3.",
           main_addr=0x6D50 + 36, rows=16, cols=16,
           map_type="fuel", unit="raw",
           decode=fuel_decode, encode=fuel_encode,
           confidence="CONFIRMED"),

    MapDef("Fuel Map 4",
           "Part-throttle fuel map 4 (high load).",
           main_addr=0x6E74 + 36, rows=16, cols=16,
           map_type="fuel", unit="raw",
           decode=fuel_decode, encode=fuel_encode,
           confidence="CONFIRMED"),

    MapDef("Ignition Map 1 (PT primary)",
           "Primary part-throttle ignition map. Decode: raw×0.75−22.5=°BTDC",
           main_addr=0x7052 + 36, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode_3b, encode=ign_encode_3b,
           confidence="CONFIRMED",
           notes="Verified: 8-14°BTDC at PT idle. Standard Motronic formula."),

    MapDef("Ignition Map 2",
           "Part-throttle ignition map 2.",
           main_addr=0x7643 + 36, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode_3b, encode=ign_encode_3b,
           confidence="CONFIRMED"),

    MapDef("Ignition Map 3",
           "Part-throttle ignition map 3.",
           main_addr=0x77AB + 36, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode_3b, encode=ign_encode_3b,
           confidence="CONFIRMED"),

    MapDef("Ignition Map 4",
           "Part-throttle ignition map 4.",
           main_addr=0x7913 + 36, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode_3b, encode=ign_encode_3b,
           confidence="CONFIRMED"),

    MapDef("Rev Limit",
           "Fuel cut RPM. Address UNCONFIRMED — needs disassembly.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM",
           confidence="UNCONFIRMED"),
]

# ── V8 maps ───────────────────────────────────────────────────────────────────
# Single 32KB flat EPROM. Limited community documentation.
# Addresses UNCONFIRMED — V8 has different codebase from I5 turbo.

_MAPS_V8_MAIN = [
    MapDef("Part Throttle Fuel",
           "Main fuelling map — part throttle.",
           main_addr=0x3000, rows=16, cols=16,
           map_type="fuel", unit="raw",
           confidence="UNCONFIRMED",
           notes="Address not verified. V8 documentation scarce."),

    MapDef("Part Throttle Ignition",
           "Ignition timing — dual distributor V8.",
           main_addr=0x2F00, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="UNCONFIRMED"),

    MapDef("Rev Limit",
           "Fuel cut RPM. Address UNCONFIRMED — needs disassembly.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM",
           confidence="UNCONFIRMED"),
]

# ── Boost chip maps ───────────────────────────────────────────────────────────
# Addresses relative to boost chip working half start.
# Source: community research — still PROVISIONAL.

_MAPS_BOOST_551 = [
    MapDef("Boost Target",
           "Boost pressure target vs RPM/load. "
           "250kPa sensor (AAN/ABY): raw÷255×2.5=bar. "
           "300kPa sensor (ADU/RS2 hardware mod): raw÷255×3.0=bar.",
           main_addr=0x0200, rows=8, cols=8,
           map_type="boost", unit="bar", chip="boost",
           confidence="PROVISIONAL"),

    MapDef("Knock Threshold",
           "Knock sensor threshold vs RPM. Higher = less sensitive.",
           main_addr=0x0400, rows=1, cols=16,
           map_type="raw", unit="raw", chip="boost",
           confidence="PROVISIONAL"),

    MapDef("N75 Duty Cycle",
           "Wastegate frequency valve duty cycle vs RPM. Higher = more boost.",
           main_addr=0x0300, rows=1, cols=16,
           map_type="raw", unit="%", chip="boost",
           confidence="PROVISIONAL"),
]


# ── Variant registry ──────────────────────────────────────────────────────────

VARIANT_551C = ROMVariant(
    name                = "ADU — RS2 Avant / UrS6 (551C)",
    software_id         = "551C",
    engine_codes        = ["ADU"],
    ecu_pns             = ["8A0907551C", "8A0907551B", "8A0907551A"],
    bosch_pns           = ["0261203543", "0261203544"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_551C_MAIN,
    boost_maps          = _MAPS_BOOST_551,
    notes               = "RS2 Avant. 300kPa MAP sensor hardware. "
                          "Best-documented M2.3.2 — XDF verified.",
)

VARIANT_551AA = ROMVariant(
    name                = "AAN / ABY — UrS4 / UrS6 / S2 Coupe (551AA)",
    software_id         = "551AA",
    engine_codes        = ["AAN", "ABY"],
    ecu_pns             = ["4A0907551AA", "4A0907551A", "895907551A"],
    bosch_pns           = ["0261203601", "0261203145"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_551AA_MAIN,
    boost_maps          = _MAPS_BOOST_551,
    notes               = "AAN (UrS4/S6 coil-on-plug) and ABY (S2 coupe). "
                          "Same codebase. Map addresses confirmed on ABY bin.",
)

# ── Confirmed map addresses — 551AA / 0x0202 prjmod + 034EFI firmware ────────
#
# Source: PRJ m232.xdf (136 tables) + .034 Rip Chip tuned file diff analysis
#         (8 tuned files vs stock, byte-level diff).
#
# This firmware layout applies to:
#   - 034EFI "Rip Chip" v1.0 (034 brand tuning, MAF-based SD)
#   - prjmod base ROM for AAN/ABY
#   - All 4A0907551AA / 551A  chips with build number 0x0202
#
# Axis values are dynamic (MAF-based, embedded in map data) and vary per tune.
# Fixed decode formulas per PRJ XDF:
#   Fuel:  1 / (raw/128) * 14.7 = AFR
#   Ign:   raw * 0.75 - 22.5 = °BTDC
#   MAP kPa: raw / 1.035
#   WGDC %:  raw / 192 * 100
#
# Newly confirmed from .034 tune diff analysis:
#   WH[0x0CEB:0x0CF0]  — Injector flow scaling (5×1) — varies with injector cc
#   WH[0x0CBF:0x0CC4]  — MAF high-load voltage clamp upper (5×1)
#   WH[0x0CCB:0x0CD0]  — MAF high-load voltage clamp lower (5×1)
#   WH[0x0D71:0x0D8F]  — High-load injector correction (32 bytes, 550cc+ tunes)
#   WH[0x1450:0x1456]  — Knock level 2 ignition limit (6×1, retarded 5° in 3071 tunes)
#   WH[0x1B7A:0x1B7C]  — Warm idle setpoint RPM (3×1, raw×10 = RPM)
#   WH[0x1CE8:0x1CF5]  — Boost target secondary table (2 rows × 6 cols, kPa)
#   WH[0x7F00:0x7F40]  — ROM version/ID string (034EFI chip ID + tune description)
#
# Injector flow scaling reference (WH[0x0CEB], key byte [0]):
#   0xAA (170) = stock AAN ~440cc equivalent
#   0xA3 (163) = RS2 stock (~440cc RS2 injectors, slightly different)
#   0x97 (151) = 28RS Stage1 unspecified injectors
#   0x88 (136) = 42lb "green top" injectors (~440cc, different flow curve)
#   0x82 (130) = 550cc 91Oct tune
#   0x65 (101) = 440cc Siemens (2871 turbo build, higher flow needed)
#   (Lower byte = larger effective injector flow at the sample MAF point)

def _prj_ign_decode(raw):
    return [round(b * 0.75 - 22.5, 1) for b in raw]

def _prj_ign_encode(val_list):
    return [max(0, min(255, round((v + 22.5) / 0.75))) for v in val_list]

def _prj_fuel_decode(raw):
    return [round(1 / (b / 128) * 14.7, 2) if b > 0 else 0.0 for b in raw]

def _prj_fuel_encode(val_list):
    return [max(1, min(255, round(14.7 / v * 128))) if v > 0 else 128 for v in val_list]

def _prj_map_kpa_decode(raw):
    return [round(b / 1.035, 1) for b in raw]

def _prj_wgdc_decode(raw):
    return [round(b / 192 * 100, 1) for b in raw]

def _prj_rpm_decode(raw):
    return [b * 40 for b in raw]

_MAPS_0202_MAIN = [
    # ── Fuel maps ──────────────────────────────────────────────────────────
    MapDef("Fuel P/T (primary)",
           "Primary part-throttle fuel map. 128 = 14.7:1 stoich. "
           "Higher raw = richer. Axes are MAF-scaled, vary per tune.",
           main_addr=0x0E13, rows=16, cols=16,
           map_type="fuel", unit="AFR",
           decode=_prj_fuel_decode, encode=_prj_fuel_encode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed. All 034EFI tunes use this address."),

    MapDef("Fuel P/T (failsafe)",
           "Fuelling map used on methanol/failsafe mode.",
           main_addr=0x21A4, rows=16, cols=16,
           map_type="fuel", unit="AFR",
           decode=_prj_fuel_decode, encode=_prj_fuel_encode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed."),

    MapDef("Fuel P/T (race fuel)",
           "Fuelling map used on race fuel mode.",
           main_addr=0x2224, rows=16, cols=16,
           map_type="fuel", unit="AFR",
           decode=_prj_fuel_decode, encode=_prj_fuel_encode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed."),

    # ── Ignition maps ───────────────────────────────────────────────────────
    MapDef("Ign P/T (no knock)",
           "Primary ignition map, no knock active. raw×0.75−22.5 = °BTDC.",
           main_addr=0x125F, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED",
           notes="PRJ XDF + .034 diff confirmed. 034EFI tunes advance up to +7° vs stock."),

    MapDef("Ign P/T (knock level 1)",
           "Ignition map with knock level 1 active (mild knock).",
           main_addr=0x1594, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed."),

    MapDef("Ign P/T (overrun)",
           "Ignition map during overrun/deceleration.",
           main_addr=0x10A8, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed."),

    # ── Boost control ───────────────────────────────────────────────────────
    MapDef("MAP Target",
           "Boost target in kPa absolute. raw/1.035 = kPa.",
           main_addr=0x2520, rows=10, cols=16,
           map_type="raw", unit="kPa",
           decode=_prj_map_kpa_decode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed."),

    MapDef("Base WGDC Pilot",
           "Base wastegate duty cycle pilot map. raw/192×100 = %.",
           main_addr=0x2480, rows=10, cols=16,
           map_type="raw", unit="%DC",
           decode=_prj_wgdc_decode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed."),

    MapDef("MAP Limit",
           "Hard boost cut limit in kPa absolute. 8-cell 1D table.",
           main_addr=0x2A96, rows=8, cols=1,
           map_type="raw", unit="kPa",
           decode=_prj_map_kpa_decode,
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed. Stock = ~1.9 kPa (essentially no limit in these tunes)."),

    MapDef("N75 Upper Limit",
           "N75 solenoid upper duty cycle limit. raw/192×100 = %.",
           main_addr=0x2A23, rows=8, cols=1,
           map_type="raw", unit="%DC",
           decode=_prj_wgdc_decode,
           confidence="CONFIRMED"),

    MapDef("N75 Lower Limit",
           "N75 solenoid lower duty cycle limit.",
           main_addr=0x2A1B, rows=8, cols=1,
           map_type="raw", unit="%DC",
           decode=_prj_wgdc_decode,
           confidence="CONFIRMED"),

    # ── Newly confirmed from 034 tune analysis ──────────────────────────────
    MapDef("Boost Target Secondary",
           "Secondary boost target cells (2 rows × 6 cols). "
           "Varies significantly between 3071 and 2871 turbo tunes. "
           "034EFI 3071 R9 tunes set all cells to 0x36 (flat 52 kPa abs). "
           "raw/1.035 = kPa absolute.",
           main_addr=0x1CE8, rows=2, cols=6,
           map_type="raw", unit="kPa",
           decode=_prj_map_kpa_decode,
           confidence="PROVISIONAL",
           notes="Confirmed from .034 diff: changes in all 3071 tunes, stable in Stage1/Stage1+."),

    MapDef("Knock Level 2 Ign Limit",
           "Ignition timing limit when knock level 2 is active. "
           "034EFI 3071 tunes retard this by ~5° vs stock (0x50→0x45). "
           "raw×0.75−22.5 = °BTDC limit.",
           main_addr=0x1450, rows=6, cols=1,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode,
           confidence="PROVISIONAL",
           notes="Confirmed from .034 diff: 0x42,0x42,0x50×4 → 0x39,0x39,0x45×4 in 3071 tunes."),

    MapDef("Injector Flow Scaling",
           "MAF-based injector flow correction (5-cell 1D). "
           "Key byte [0] encodes injector sizing:\n"
           "  0xAA = stock AAN ~440cc | 0xA3 = RS2 stock\n"
           "  0x88 = 42lb green tops  | 0x82 = 550cc 91Oct\n"
           "  0x65 = 440cc Siemens (2871 build)\n"
           "Lower value = larger injector at that MAF point.",
           main_addr=0x0CEB, rows=5, cols=1,
           map_type="raw", unit="corr",
           confidence="PROVISIONAL",
           notes="Confirmed from .034 diff: varies with injector spec per tune name."),

    MapDef("Warm Idle Setpoint RPM",
           "Warm idle RPM target (3-cell coolant-temperature stepped). "
           "raw×10 = RPM. Stock: 1300/1000/800 RPM. "
           "3071 tunes raise to 1350/1050/950.",
           main_addr=0x1B7A, rows=3, cols=1,
           map_type="raw", unit="RPM",
           decode=lambda raw: [b * 10 for b in raw],
           confidence="PROVISIONAL",
           notes="Confirmed from .034 diff: 28RS_R2 and R9.1_550 both raise idle vs stock."),

    # ── Axes (read-only reference) ──────────────────────────────────────────
    MapDef("Fuel P/T RPM axis",
           "RPM axis for fuel P/T map. MAF-scaled, varies per tune/injector.",
           main_addr=0x0DF1, rows=16, cols=1,
           map_type="raw", unit="raw",
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed. Axis varies between injector/MAF builds."),

    MapDef("Fuel P/T Load axis",
           "Load axis for fuel P/T map.",
           main_addr=0x0E03, rows=16, cols=1,
           map_type="raw", unit="raw",
           confidence="CONFIRMED"),

    MapDef("Ign P/T RPM axis",
           "RPM axis for ignition P/T map.",
           main_addr=0x123D, rows=16, cols=1,
           map_type="raw", unit="raw",
           confidence="CONFIRMED"),

    MapDef("Ign P/T Load axis",
           "Load axis for ignition P/T map.",
           main_addr=0x124F, rows=16, cols=1,
           map_type="raw", unit="raw",
           confidence="CONFIRMED"),

    MapDef("Injector Latency",
           "Dead-time compensation per voltage step (5 cells). "
           "0.010667 × raw = ms. IDENTICAL across all 034 tunes (no injector changes here).",
           main_addr=0x0D14, rows=5, cols=1,
           map_type="raw", unit="ms",
           decode=lambda raw: [round(b * 0.010667, 3) for b in raw],
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed. Stock values: 0.64/0.32/0.62/0.03/0.27 ms. "
                 "Injector sizing encoded in WH[0x0CEB] instead."),

    MapDef("Warmup Enrichment (ECT×IAT)",
           "Warmup fuel enrichment, coolant vs air temp. 6×6 table.",
           main_addr=0x0D9A, rows=6, cols=6,
           map_type="raw", unit="raw",
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed."),

    MapDef("VE Table",
           "Volumetric efficiency table (speed-density mode only). "
           "Active only when SD mode is enabled. Blank (0x02) in MAF-based tunes.",
           main_addr=0x2074, rows=16, cols=16,
           map_type="raw", unit="%VE",
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed. 034EFI tunes leave this blank — they use MAF."),

    MapDef("MAF Linearisation (low)",
           "MAF sensor low-range linearisation (9-cell). "
           "IDENTICAL across all 034EFI tunes — MAF sensor hardware not changed.",
           main_addr=0x102D, rows=9, cols=1,
           map_type="raw", unit="raw",
           confidence="CONFIRMED",
           notes="PRJ XDF confirmed. Unchanged across all .034 variants."),

    MapDef("MAF Linearisation (mid)",
           "MAF sensor mid-range linearisation (15-cell). Identical across .034 tunes.",
           main_addr=0x1047, rows=15, cols=1,
           map_type="raw", unit="raw",
           confidence="CONFIRMED"),
]

VARIANT_551AA_0202 = ROMVariant(
    name                = "AAN / ABY — 034EFI Rip Chip / prjmod (551AA 0x0202)",
    software_id         = "551AA_0202",
    engine_codes        = ["AAN", "ABY"],
    ecu_pns             = ["4A0907551AA", "4A0907551A", "895907551A"],
    bosch_pns           = ["0261200465", "0261200451"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_0202_MAIN,
    boost_maps          = _MAPS_BOOST_551,
    notes               = "034EFI Rip Chip v1.0 / prjmod base firmware. Build number 0x0202. "
                          "MAF-based (not MAP/SD). All 034 tunes (Stage1, Stage1+, 28RS, R8, R9.1) "
                          "use this address layout. Map addresses confirmed from PRJ XDF + .034 diff. "
                          "ROM tail WH[0x7F00:0x7F40] contains ASCII version string: "
                          "'4A0907551AA [tune name] 034EFI [flasher info]'. "
                          "Injector sizing encoded at WH[0x0CEB]: lower = larger injectors. "
                          "Boost control uses secondary table at WH[0x1CE8] — disabled (zeroed) "
                          "in some 3071 tunes that run boost from the boost chip only.",
)

VARIANT_404 = ROMVariant(
    name                = "3B / RR — 200 20vT / UrQ RR / S2 early (404)",
    software_id         = "404",
    engine_codes        = ["3B", "RR"],
    ecu_pns             = ["895907404BA", "443907404", "895907404"],
    bosch_pns           = ["0261200451", "0261200484"],
    dual_eprom          = True,
    working_half_offset = 0,       # 32KB flat file, no offset
    main_maps           = _MAPS_3B_MAIN,
    boost_maps          = _MAPS_BOOST_551,
    notes               = "Distributor ignition (not coil packs). "
                          "Map addresses CONFIRMED from PRJ MapFinder output. "
                          "Header-embedded format: addresses include 36-byte offset.",
)

VARIANT_V8_ABH = ROMVariant(
    name                = "ABH — V8 4.2L 32v",
    software_id         = "557",
    engine_codes        = ["ABH"],
    ecu_pns             = ["4A0907557A"],
    bosch_pns           = ["0261203143"],
    dual_eprom          = False,
    working_half_offset = 0,
    main_maps           = _MAPS_V8_MAIN,
    boost_maps          = [],
    notes               = "Single EPROM. Dual distributor. Map addresses UNCONFIRMED.",
)

VARIANT_V8_PT = ROMVariant(
    name                = "PT — V8 3.6L 32v",
    software_id         = "404V8",
    engine_codes        = ["PT"],
    ecu_pns             = ["443907404A", "893907404F"],
    bosch_pns           = ["0261200273", "0261200228"],
    dual_eprom          = False,
    working_half_offset = 0,
    main_maps           = _MAPS_V8_MAIN,
    boost_maps          = [],
    notes               = "Single EPROM. Dual distributor. Map addresses UNCONFIRMED.",
)

ALL_VARIANTS: list[ROMVariant] = [
    VARIANT_551C,
    VARIANT_551AA,
    VARIANT_551AA_0202,
    VARIANT_404,
    VARIANT_V8_ABH,
    VARIANT_V8_PT,
]


# ── Known CRC32 fingerprints ──────────────────────────────────────────────────
# CRC32 of the 32KB working half.
# Working half = upper 32KB of 64KB file (offset 0x8000) for 551x.
# Working half = entire file for 3B/V8.

KNOWN_CRCS: dict[int, tuple[str, str]] = {
    0x4378E077: ("551C",      "Stock — ADU RS2 (adu_fuel-ign_551c.bin)"),
    0xA98CB481: ("551AA",     "Stock — ABY S2 Coupe (aby_fuel-ign_551aa.bin)"),
    0x0AE3CACD: ("404",       "Stock — 3B 200 20vT (stock fuel.BIN)"),
    0x9245FA10: ("404",       "Stock — 3B Audi S2 (0261200484 MapFinder bin)"),
    0x594F97FB: ("404V8",     "Stock — PT V8 3.6L"),
    0x750A9EB0: ("404V8",     "ABT tune — PT V8 3.6L"),
    # 034EFI Rip Chip / prjmod 0x0202 firmware (confirmed from .034 diff analysis)
    0x956BFC9C: ("551AA_0202", "034EFI Stock Rip Chip (4A0907551AA)"),
    0xA47011AB: ("551AA_0202", "034EFI Stage 1"),
    0x16FD8953: ("551AA_0202", "034EFI Stage 1 (variant)"),
    0x9A8A6B4E: ("551AA_0202", "034EFI Stage 1 28RS R2"),
    0x6F3AE675: ("551AA_0202", "034EFI Stage 1 R8 42lb Green Tops"),
    0x07DA1752: ("551AA_0202", "034EFI Stage 1 R9.1 550cc 91Oct"),
    0x2EB58546: ("551AA_0202", "034EFI Stage 1 R9.1 550cc EV14"),
    0xA77BB88E: ("551AA_0202", "034EFI Stage 1 AAN 2871 R9 440cc Siemens"),
    0x28C04D7B: ("551AA_0202", "034EFI Rip Chip RS2 91Oct"),
    0x81D197CF: ("551AA_0202", "PRJ AAN/ABY stock (m232.org)"),
    0xAD9330AC: ("551AA_0202", "PRJ AAN bigturbo WMI"),
    # vwnut8392/M232-Firmware — two further PRJmod revisions
    0x9DD68BD3: ("551AA_0202", "PRJmod AAN D03PMC (vwnut8392/M232-Firmware TMS27C512)"),
    0xF7432BB5: ("551AA_0202", "PRJmod 551A D02PMC (vwnut8392/M232-Firmware, 4A0907551A)"),
}

# Build number ranges for MEDIUM confidence detection (fallback when CRC unknown)
# 551AA_0202 (prjmod/034EFI): build 0x0202 — must come BEFORE the 551AA range
# 551AA covers both AAN (low builds ~0x0000-0x3FFF) and ABY (0x6450 range)
# 551C (ADU/RS2) sits at 0x4533; overlap with ABY is resolved via CRC fingerprint
BUILD_RANGES: dict[str, tuple[int, int]] = {
    "551AA_0202": (0x0202, 0x0202),  # exact build number for prjmod/034EFI
    "551AA":      (0x0000, 0x6FFF),  # AAN + ABY; CRC match takes priority
    "551C":       (0x7000, 0x9FFF),  # ADU known build 0x4533 — CRC match used
    "404":        (0xE000, 0xFFFF),
    "404V8":      (0xA000, 0xCFFF),
}


# ── Normalisation ─────────────────────────────────────────────────────────────

def normalize_rom(raw: bytes, variant: ROMVariant | None = None
                  ) -> tuple[bytes, list[str]]:
    """
    Normalise a raw chip read to the 32KB working half.

    For 551x (64KB doubled files): extracts upper half at offset 0x8000.
    For 3B/V8 (32KB flat): returns as-is.
    Also accepts .034 files (identical bytes to .bin).
    """
    notes = []
    size = len(raw)

    # Already a 32KB working half (either extracted already, or flat 3B/V8 file)
    if size == MAIN_CHIP_WORKING:
        return raw, notes

    # 64KB doubled file — working half is always at offset 0x8000 for 551x
    if size == MAIN_CHIP_PHYSICAL:
        working = raw[WORKING_HALF_OFFSET:WORKING_HALF_OFFSET + MAIN_CHIP_WORKING]
        notes.append("64KB chip read — upper half selected (working half @ 0x8000)")
        return working, notes

    # Sizes in between — might be a partial read or unusual chip
    notes.append(f"Unexpected size: {size:,} bytes. Returning as-is.")
    return raw, notes


# ── Checksum ──────────────────────────────────────────────────────────────────

def compute_checksum(rom: bytes) -> int:
    """16-bit sum of bytes 0x0000–0x3FF9, truncated."""
    return sum(rom[:CHECKSUM_RANGE_END]) & 0xFFFF


def read_stored_checksum(rom: bytes) -> tuple[int, int]:
    cs = (rom[CHECKSUM_ADDR] << 8) | rom[CHECKSUM_ADDR + 1]
    cp = (rom[COMPLEMENT_ADDR] << 8) | rom[COMPLEMENT_ADDR + 1]
    return cs, cp


def verify_checksum(rom: bytes) -> bool:
    if len(rom) < MAIN_CHIP_WORKING:
        return False
    computed = compute_checksum(rom)
    stored_cs, stored_cp = read_stored_checksum(rom)
    return stored_cs == computed and stored_cp == (0xFFFF - computed)


def apply_checksum(rom: bytearray) -> bytearray:
    """Write checksum and complement, mirror if 64KB."""
    cs = compute_checksum(bytes(rom))
    cp = 0xFFFF - cs
    rom[CHECKSUM_ADDR]     = (cs >> 8) & 0xFF
    rom[CHECKSUM_ADDR + 1] = cs & 0xFF
    rom[COMPLEMENT_ADDR]   = (cp >> 8) & 0xFF
    rom[COMPLEMENT_ADDR+1] = cp & 0xFF
    if len(rom) >= MAIN_CHIP_PHYSICAL:
        for i in range(MAIN_CHIP_WORKING):
            rom[MIRROR_OFFSET + i] = rom[i]
    return rom


def read_build_number(rom: bytes) -> int:
    if len(rom) < MAIN_CHIP_WORKING:
        return 0
    return (rom[BUILD_NUMBER_ADDR] << 8) | rom[BUILD_NUMBER_ADDR + 1]


# ── Detection ─────────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    variant:      ROMVariant | None
    confidence:   str
    method:       str
    checksum_ok:  bool
    crc32:        int
    build_number: int
    warnings:     list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.variant:
            return f"{self.variant.name}  [{self.variant.software_id}]"
        return "Unknown M2.3 / M2.3.2 variant"

    @property
    def is_known(self) -> bool:
        return self.variant is not None and self.confidence in ("HIGH", "MEDIUM")

    @property
    def dual_eprom(self) -> bool:
        return self.variant.dual_eprom if self.variant else True


def detect_rom(rom: bytes) -> DetectionResult:
    """
    Detect ROM variant. Input should be the 32KB working half.
    """
    crc    = zlib.crc32(rom[:MAIN_CHIP_WORKING] if len(rom) >= MAIN_CHIP_WORKING
                        else rom) & 0xFFFFFFFF
    cs_ok  = verify_checksum(rom)
    build  = read_build_number(rom)

    # 1. CRC32 exact match
    if crc in KNOWN_CRCS:
        sw_id, cal_label = KNOWN_CRCS[crc]
        variant = next((v for v in ALL_VARIANTS if v.software_id == sw_id), None)
        return DetectionResult(
            variant=variant, confidence="HIGH",
            method=f"CRC32 match — {cal_label}",
            checksum_ok=cs_ok, crc32=crc, build_number=build,
        )

    # 2. Build number heuristic
    for sw_id, (lo, hi) in BUILD_RANGES.items():
        if lo <= build <= hi:
            variant = next((v for v in ALL_VARIANTS if v.software_id == sw_id), None)
            warnings = []
            if not cs_ok:
                warnings.append("Checksum invalid — ROM may be modified")
            return DetectionResult(
                variant=variant, confidence="MEDIUM",
                method=f"Build number 0x{build:04X} → {sw_id}",
                checksum_ok=cs_ok, crc32=crc, build_number=build,
                warnings=warnings,
            )

    return DetectionResult(
        variant=None, confidence="UNKNOWN", method="No match",
        checksum_ok=cs_ok, crc32=crc, build_number=build,
        warnings=["ROM not recognised — map addresses need manual verification",
                  *(["Checksum invalid"] if not cs_ok else [])],
    )


# ── Axis helpers ──────────────────────────────────────────────────────────────

def read_axes_from_header(rom: bytes, header_addr: int,
                          rows: int = 16, cols: int = 16
                          ) -> tuple[list, list]:
    """
    Read RPM and load axis values from a Bosch descriptor header block.

    Layout: [type:1][count:1][axis_bytes×count] repeated for row then col axis.
    Used by 3B/RR maps where the header immediately precedes the map data.

    Returns (rpm_axis, load_axis) as lists of integers.
    If the header address is out of bounds, returns index lists [0..rows-1].
    """
    end = header_addr + 2 + rows + 2 + cols
    if end > len(rom) or header_addr < 0:
        return list(range(rows)), list(range(cols))

    row_count = rom[header_addr + 1]
    row_raw   = list(rom[header_addr + 2: header_addr + 2 + row_count])
    col_start = header_addr + 2 + row_count
    col_count = rom[col_start + 1]
    col_raw   = list(rom[col_start + 2: col_start + 2 + col_count])

    return row_raw, col_raw


def get_axes(rom: bytes, map_def: MapDef, variant: ROMVariant
             ) -> tuple[list, list]:
    """
    Return (rpm_axis, load_axis) appropriate for the variant and map.

    For 551C/551AA: returns the known confirmed axis arrays.
    For 3B/404/V8: reads axis bytes from the Bosch descriptor header,
                   which sits immediately before map_def.main_addr (data_addr - 36).
    Returns lists of raw integers — the caller decides how to label them.
    """
    sw = variant.software_id if variant else ""
    if sw in ("551C", "551AA"):
        return list(_RPM_AXIS_551), list(_LOAD_AXIS_551)

    if sw in ("404", "404V8"):
        # Header is at data_addr - 36 (descriptor + 2 axes of 16 bytes each + 2 type/count bytes each)
        header_addr = map_def.main_addr - 36
        return read_axes_from_header(rom, header_addr, map_def.rows, map_def.cols)

    # Unknown variant — use sequential indices
    return list(range(map_def.rows)), list(range(map_def.cols))


# ── Map I/O ───────────────────────────────────────────────────────────────────

def read_map(rom: bytes, map_def: MapDef) -> list[list[int]]:
    """Read raw bytes into 2D grid [row][col]."""
    addr = map_def.main_addr
    return [
        [rom[addr + r * map_def.cols + c]
         if addr + r * map_def.cols + c < len(rom) else 0
         for c in range(map_def.cols)]
        for r in range(map_def.rows)
    ]


def read_map_decoded(rom: bytes, map_def: MapDef) -> list[list[float]]:
    raw = read_map(rom, map_def)
    if not map_def.decode:
        return [[float(v) for v in row] for row in raw]
    return [[map_def.decode(v) for v in row] for row in raw]


def write_map(rom: bytearray, map_def: MapDef,
              data: list[list[int]]) -> bytearray:
    addr = map_def.main_addr
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            off = addr + r * map_def.cols + c
            if off < len(rom):
                rom[off] = max(0, min(255, int(val)))
    if len(rom) >= MAIN_CHIP_PHYSICAL:
        for i in range(MAIN_CHIP_WORKING):
            if MIRROR_OFFSET + i < len(rom):
                rom[MIRROR_OFFSET + i] = rom[i]
    return rom


def read_rev_limit(rom: bytes, variant: ROMVariant) -> Optional[int]:
    rev = next((m for m in variant.main_maps if m.name == "Rev Limit"), None)
    if not rev or rev.main_addr + 2 > len(rom):
        return None
    raw = (rom[rev.main_addr] << 8) | rom[rev.main_addr + 1]
    return round(30_000_000 / raw) if raw else None


def write_rev_limit(rom: bytearray, variant: ROMVariant, rpm: int) -> bytearray:
    rev = next((m for m in variant.main_maps if m.name == "Rev Limit"), None)
    if not rev:
        return rom
    raw  = round(30_000_000 / rpm)
    addr = rev.main_addr
    rom[addr]     = (raw >> 8) & 0xFF
    rom[addr + 1] = raw & 0xFF
    if len(rom) >= MAIN_CHIP_PHYSICAL:
        rom[MIRROR_OFFSET + addr]     = rom[addr]
        rom[MIRROR_OFFSET + addr + 1] = rom[addr + 1]
    return rom
