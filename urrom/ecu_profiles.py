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

def _prj_ign_decode(b: int) -> float:
    return round(b * 0.75 - 22.5, 1)

def _prj_ign_encode(v: float) -> int:
    return max(0, min(255, round((v + 22.5) / 0.75)))

def _prj_fuel_decode(b: int) -> float:
    return round(1 / (b / 128) * 14.7, 2) if b > 0 else 0.0

def _prj_fuel_encode(v: float) -> int:
    return max(1, min(255, round(14.7 / v * 128))) if v > 0 else 128

def _prj_map_kpa_decode(b: int) -> float:
    return round(b / 1.035, 1)

def _prj_wgdc_decode(b: int) -> float:
    return round(b / 192 * 100, 1)

def _prj_rpm_decode(b: int) -> int:
    return b * 40

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
           decode=lambda b: b * 10,
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
           decode=lambda b: round(b * 0.010667, 3),
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

    # ── Full PRJ XDF table set (all 136 confirmed addresses) ─────────────────
    # Source: VWnut8392/m232 TunerPro/m232.xdf z-axis mmedaddress values
    # All addresses subtract 0x8000 from XDF file address → WH offset

    MapDef("DTC Classes",
           "Diagnostic trouble code class mapping. 60×60 lookup table.",
           main_addr=0x055B, rows=60, cols=60,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Launch RPM Limit Ign Cut Curve",
           "256-cell ignition cut RPM curve for launch control. raw×40 = RPM.",
           main_addr=0x06C0, rows=256, cols=1,
           map_type="raw", unit="RPM",
           decode=lambda b: b*40,
           confidence="CONFIRMED"),

    MapDef("Wall Film Events (SD)",
           "Number of ignition events for wall film enrichment, SD mode (10-cell).",
           main_addr=0x09B0, rows=10, cols=1,
           map_type="raw", unit="events", confidence="CONFIRMED"),

    MapDef("Wall Film Load Gradient Multiplier",
           "Load gradient multiplier for wall film enrichment (31-cell, LOAD delta axis).",
           main_addr=0x0A00, rows=31, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Retard on Positive Load Delta",
           "Ignition retard applied on positive load delta (31-cell). raw×0.75 = °.",
           main_addr=0x0A20, rows=31, cols=1,
           map_type="raw", unit="°",
           decode=lambda b: round(b*0.75, 2),
           confidence="CONFIRMED"),

    MapDef("Wall Film Events (MAF)",
           "Number of ignition events for wall film enrichment, MAF mode (8-cell).",
           main_addr=0x0A40, rows=8, cols=1,
           map_type="raw", unit="events", confidence="CONFIRMED"),

    MapDef("MAF Low Voltage RPM Axis",
           "RPM axis for MAF low-voltage correction table (12-cell).",
           main_addr=0x0C22, rows=12, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("MAF Low Voltage Voltage Axis",
           "Voltage axis for MAF low-voltage correction table (12-cell).",
           main_addr=0x0C30, rows=12, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("MAF Low Voltage Correction",
           "MAF sensor low-voltage correction table (12×12). "
           "Corrects MAF reading at low sensor voltages (idle/light load).",
           main_addr=0x0C3C, rows=12, cols=12,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Fuel Cut Hard Load Limit",
           "Hard load limit for fuel cut (5×4). IF(Y==0x3F, X, X×10.24).",
           main_addr=0x0CD9, rows=5, cols=4,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Fuel Cut MAF Error Limp",
           "Fuel cut threshold on MAF error / limp mode (5-cell).",
           main_addr=0x0CF4, rows=5, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("pATM Correction Axis",
           "Atmospheric pressure correction axis (4-cell).",
           main_addr=0x0CFB, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Air Charge for Cranking",
           "Air charge value used during cranking (4-cell).",
           main_addr=0x0CFF, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Max Calculated Load",
           "Maximum calculated load clamp (4-cell RPM axis).",
           main_addr=0x0D09, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film RPM Axis",
           "RPM axis for wall film enrichment table (6-cell).",
           main_addr=0x0D1B, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film Load Axis",
           "Load axis for wall film enrichment (4-cell).",
           main_addr=0x0D23, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Load Filter",
           "Load signal smoothing filter (6×4). 255 = no smoothing.",
           main_addr=0x0D27, rows=6, cols=4,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film RPM Axis (small)",
           "Small RPM axis for wall film warmup enrichment (3-cell).",
           main_addr=0x0D41, rows=3, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film Load Axis (small)",
           "Small load axis for wall film warmup enrichment (4-cell).",
           main_addr=0x0D46, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film Enrichment (RPM×Load)",
           "Wall film (transient) fuel enrichment table, RPM vs load (3×4).",
           main_addr=0x0D4A, rows=3, cols=4,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film Coolant Axis",
           "Coolant temp axis for wall film Coolant×RPM table (6-cell).",
           main_addr=0x0D58, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film RPM Axis (CoolantxRPM)",
           "RPM axis for wall film Coolant×RPM enrichment table (6-cell).",
           main_addr=0x0D60, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film Enrichment (Coolant×RPM)",
           "Wall film enrichment table indexed by coolant temp and RPM (6×6).",
           main_addr=0x0D66, rows=6, cols=6,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Wall Film Coolant Axis 2",
           "Secondary coolant axis for wall film table (6-cell).",
           main_addr=0x0D8C, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Warmup Enrichment IDC",
           "Warmup enrichment injector duty cycle correction (5×3).",
           main_addr=0x0DCA, rows=5, cols=3,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Fuel IATxRPM Correction",
           "Fuel enrichment correction by intake air temp and RPM (6×4). "
           "Decode: 1/(X/128)*14.7 = AFR.",
           main_addr=0x0F21, rows=6, cols=4,
           map_type="fuel", unit="AFR",
           decode=_prj_fuel_decode, encode=_prj_fuel_encode,
           confidence="CONFIRMED"),

    MapDef("Fuel Continuous Knock",
           "Fuel enrichment during continuous knock (5-cell). AFR decode.",
           main_addr=0x0F40, rows=5, cols=1,
           map_type="fuel", unit="AFR",
           decode=_prj_fuel_decode, encode=_prj_fuel_encode,
           confidence="CONFIRMED"),

    MapDef("Crank Fuel vs Coolant",
           "Cranking fuel injection amount vs coolant temperature (6-cell). raw/8.",
           main_addr=0x0F65, rows=6, cols=1,
           map_type="raw", unit="raw",
           decode=lambda b: round(b/8, 2),
           confidence="CONFIRMED"),

    MapDef("Cylinder Re-Activation RPM",
           "RPM axis for cylinder re-activation delta correction (4-cell).",
           main_addr=0x0FDC, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Cylinder Re-Activation Delta",
           "Cylinder re-activation delta correction (4-cell). raw×40 = RPM.",
           main_addr=0x0FE0, rows=4, cols=1,
           map_type="raw", unit="RPM",
           decode=lambda b: b*40,
           confidence="CONFIRMED"),

    MapDef("IAT Correction Axis",
           "IAT axis for MAF IAT correction table (9-cell).",
           main_addr=0x1024, rows=9, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("MAF Linearisation (low, full)",
           "MAF low-range linearisation full table (11-cell, input×16 range).",
           main_addr=0x102F, rows=11, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("MAF Linearisation (mid, full)",
           "MAF mid-range linearisation full table (13-cell, input×4 range).",
           main_addr=0x1049, rows=13, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("MAF Linearisation (high)",
           "MAF high-range linearisation (11-cell, input×1 range).",
           main_addr=0x1063, rows=11, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("MAF Linearisation (high, full)",
           "MAF high-range linearisation full table (13-cell).",
           main_addr=0x1065, rows=13, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ignition Phase Correction RPM",
           "RPM axis for ignition phase correction (3-cell).",
           main_addr=0x1070, rows=3, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ignition Phase Correction RPM 2",
           "Alternate RPM axis for ignition phase correction (3-cell). (X-30)×0.75 = °.",
           main_addr=0x1073, rows=3, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ignition Phase Correction",
           "Phase correction applied to ignition timing (3-cell). (X-30)×0.75 = °BTDC.",
           main_addr=0x1081, rows=3, cols=1,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Idle Ign RPM Axis",
           "RPM axis for idle ignition closed-throttle map (7-cell).",
           main_addr=0x11AA, rows=7, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle Ign Closed Throttle",
           "Ignition timing at idle with throttle closed (7×4). °BTDC decode.",
           main_addr=0x11B7, rows=7, cols=4,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Cat Converter Heating Retard",
           "Catalytic converter heating ignition retard (6×5). °BTDC decode.",
           main_addr=0x11E2, rows=6, cols=5,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Idle Ign Warmup RPM Axis",
           "RPM axis for idle ignition warmup map (6-cell).",
           main_addr=0x1202, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle Ign Warmup Coolant Axis",
           "Coolant temp axis for idle ignition warmup map (5-cell).",
           main_addr=0x120A, rows=5, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle Ign Warmup",
           "Ignition timing during idle warmup phase (6×5). °BTDC decode.",
           main_addr=0x120F, rows=6, cols=5,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign IAT Correction",
           "Ignition timing correction by intake air temperature (6-cell). °BTDC decode.",
           main_addr=0x1235, rows=6, cols=1,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Load Threshold for IAT Correction",
           "Load threshold above which IAT ignition correction is applied (6-cell).",
           main_addr=0x148B, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ignition Dwell RPM Axis",
           "RPM axis for ignition dwell table (12-cell).",
           main_addr=0x14F9, rows=12, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ignition Dwell",
           "Ignition dwell time table (12×7). Dwell in ms.",
           main_addr=0x150E, rows=12, cols=7,
           map_type="raw", unit="ms", confidence="CONFIRMED"),

    MapDef("Cat Converter Heating Retard (Limp)",
           "Catalytic converter heating retard in limp mode (6×5). °BTDC decode.",
           main_addr=0x17C7, rows=6, cols=5,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign CoolantxIAT Correction",
           "Ignition timing correction by coolant and IAT (6×6). °BTDC decode.",
           main_addr=0x1A3D, rows=6, cols=6,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Idle ISV RPM Axis",
           "RPM axis for idle stepper valve linearisation (7-cell).",
           main_addr=0x1A93, rows=7, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle ISV Coolant Axis",
           "Coolant temp axis for idle stepper valve linearisation (6-cell).",
           main_addr=0x1A9C, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle ISV Linearisation",
           "Idle stepper valve (ISV) opening linearisation table (7×6).",
           main_addr=0x1AA2, rows=7, cols=6,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle ISV RPM Axis (AC)",
           "RPM axis for idle ISV linearisation with AC on (5-cell).",
           main_addr=0x1ACE, rows=5, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle ISV Coolant Axis (AC)",
           "Coolant temp axis for idle ISV linearisation with AC on (6-cell).",
           main_addr=0x1AD5, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle ISV Linearisation (AC On)",
           "Idle stepper valve linearisation with air conditioning active (5×6). "
           "prjmod removed AC idle increase; AC fix patch restores this table. "
           "RAM_20.6 flag = 1 when AC on.",
           main_addr=0x1ADB, rows=5, cols=6,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("ISV Crank RPM Axis",
           "RPM axis for ISV frequency during cranking (4-cell).",
           main_addr=0x1B0B, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("ISV Crank Frequency (RPM)",
           "ISV opening frequency during cranking, RPM-indexed (4-cell).",
           main_addr=0x1B0F, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("ISV Crank Coolant Axis",
           "Coolant temp axis for ISV cranking frequency (4-cell).",
           main_addr=0x1B15, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("ISV Crank Frequency (ECT)",
           "ISV opening frequency during cranking, coolant-indexed (4-cell).",
           main_addr=0x1B19, rows=4, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle Setpoint RPM",
           "Target idle speed (3-cell coolant stepped). raw×10 = RPM. "
           "Stock: 1300/1000/800. 3071 tunes raise to 1350/1050/950.",
           main_addr=0x1BAF, rows=3, cols=1,
           map_type="raw", unit="RPM",
           decode=lambda b: b*10,
           confidence="CONFIRMED"),

    MapDef("Lambda Threshold",
           "Lambda sensor switching threshold (6-cell).",
           main_addr=0x1D1C, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Lambda Threshold (Knock)",
           "Lambda threshold during continuous knock condition (6-cell).",
           main_addr=0x1D2A, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Alpha/N RPM Axis",
           "RPM axis for Alpha/N MAF error filling map (12-cell).",
           main_addr=0x1EBE, rows=12, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Alpha/N TPS Axis",
           "TPS axis for Alpha/N MAF error filling map (6-cell).",
           main_addr=0x1ECC, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Alpha/N MAF Error Filling",
           "MAF error / Alpha-N fallback fuelling map (12×6). "
           "Used when MAF signal is out of range.",
           main_addr=0x1ED2, rows=12, cols=6,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("0x9F7C RPM×Load 4x4",
           "Small 4×4 ignition correction table (RPM×Load). (X-30)×0.75 = °BTDC.",
           main_addr=0x1F88, rows=4, cols=4,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("VE RPM Axis",
           "RPM axis for VE (speed-density) table (16-cell).",
           main_addr=0x2052, rows=16, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("VE MAP Axis",
           "MAP axis for VE (speed-density) table (16-cell).",
           main_addr=0x2064, rows=16, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ign P/T Failsafe RPM Axis",
           "RPM axis for ignition failsafe/meth map (6-cell).",
           main_addr=0x2182, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ign P/T Failsafe Load Axis",
           "Load axis for ignition failsafe/meth map (6-cell).",
           main_addr=0x218A, rows=6, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ign P/T (LPG)",
           "Ignition P/T map for LPG fuel mode (16×16). °BTDC decode.",
           main_addr=0x2198, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("KR RPM Axis (0x50)",
           "RPM axis for cylinder-selective knock retard (P5.5 threshold, 8-cell).",
           main_addr=0x21D9, rows=8, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("KR RPM Axis (0x4F)",
           "RPM axis for cylinder-selective knock retard (global, 8-cell).",
           main_addr=0x2206, rows=8, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Knock Threshold Cyl 1",
           "Knock detection threshold for cylinder 1 (5×8).",
           main_addr=0x2218, rows=5, cols=8,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Knock Threshold Cyl 2",
           "Knock detection threshold for cylinder 2 (5×8).",
           main_addr=0x2240, rows=5, cols=8,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Knock Threshold Cyl 3",
           "Knock detection threshold for cylinder 3 (5×8).",
           main_addr=0x2268, rows=5, cols=8,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Knock Threshold Cyl 4",
           "Knock detection threshold for cylinder 4 (5×8).",
           main_addr=0x2290, rows=5, cols=8,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Injector Latency (LPG)",
           "Injector dead-time compensation for LPG mode (5-cell). 0.010667×raw = ms.",
           main_addr=0x229F, rows=5, cols=1,
           map_type="raw", unit="ms",
           decode=lambda b: round(b*0.010667, 3),
           confidence="CONFIRMED"),

    MapDef("Fuel IATxRPM Correction (LPG)",
           "Fuel enrichment IATxRPM correction for LPG mode (6×4). AFR decode.",
           main_addr=0x22B2, rows=6, cols=4,
           map_type="fuel", unit="AFR",
           decode=_prj_fuel_decode, encode=_prj_fuel_encode,
           confidence="CONFIRMED"),

    MapDef("Knock Threshold Cyl 5",
           "Knock detection threshold for cylinder 5 (5×8).",
           main_addr=0x22B8, rows=5, cols=8,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle Ign Closed Throttle (LPG)",
           "Idle ignition closed-throttle map for LPG mode (7×4). °BTDC decode.",
           main_addr=0x22D9, rows=7, cols=4,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Cyl KR Activation Threshold (MAP)",
           "Cylinder-selective knock retard activation threshold vs MAP (8-cell). kPa decode.",
           main_addr=0x22E2, rows=8, cols=1,
           map_type="raw", unit="kPa",
           decode=_prj_map_kpa_decode,
           confidence="CONFIRMED"),

    MapDef("Throttle Gradient KR Activation",
           "Throttle rate gradient threshold for dynamic knock retard activation (8-cell).",
           main_addr=0x22FA, rows=8, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ign IAT Correction (LPG)",
           "Ignition IAT correction for LPG mode (6-cell). °BTDC decode.",
           main_addr=0x22FD, rows=6, cols=1,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Dynamic KR Duration",
           "Duration of dynamic knock retard, reset on activation (8-cell).",
           main_addr=0x2302, rows=8, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Idle Ign Warmup (LPG)",
           "Idle ignition warmup map for LPG mode (6×5). °BTDC decode.",
           main_addr=0x2312, rows=6, cols=5,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Cyl KR Retard Step P/T",
           "Cylinder-selective knock retard step at part-throttle (8-cell). raw×0.5 = °.",
           main_addr=0x2317, rows=8, cols=1,
           map_type="raw", unit="°",
           decode=lambda b: round(b*0.5, 2),
           confidence="CONFIRMED"),

    MapDef("Cyl KR Retard Step WOT",
           "Cylinder-selective knock retard step at wide-open-throttle (8-cell). raw×0.5 = °.",
           main_addr=0x231F, rows=8, cols=1,
           map_type="raw", unit="°",
           decode=lambda b: round(b*0.5, 2),
           confidence="CONFIRMED"),

    MapDef("Cyl KR Retard Limit P/T",
           "Cylinder-selective knock retard limit at part-throttle (8-cell). raw×0.5 = °.",
           main_addr=0x2327, rows=8, cols=1,
           map_type="raw", unit="°",
           decode=lambda b: round(b*0.5, 2),
           confidence="CONFIRMED"),

    MapDef("Cyl KR Retard Limit WOT",
           "Cylinder-selective knock retard limit at wide-open-throttle (8-cell). raw×0.5 = °.",
           main_addr=0x232F, rows=8, cols=1,
           map_type="raw", unit="°",
           decode=lambda b: round(b*0.5, 2),
           confidence="CONFIRMED"),

    MapDef("Cyl KR Phase-In Delay",
           "Cylinder-selective knock retard phase-in delay (8-cell).",
           main_addr=0x2337, rows=8, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Boost TPS Axis",
           "TPS axis for boost control maps (10-cell).",
           main_addr=0x2363, rows=10, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Boost RPM Axis (wide)",
           "Wide RPM axis for boost control maps (16-cell).",
           main_addr=0x2370, rows=16, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Boost IAT Axis",
           "IAT axis for boost IAT correction maps (8-cell).",
           main_addr=0x2385, rows=8, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Boost RPM Axis (narrow)",
           "Narrow RPM axis for boost correction maps (10-cell).",
           main_addr=0x2391, rows=10, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Ign P/T Race Fuel",
           "Ignition P/T map for race fuel mode (16×16). °BTDC decode.",
           main_addr=0x2424, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=_prj_ign_decode, encode=_prj_ign_encode,
           confidence="CONFIRMED"),

    MapDef("Boost RPM Axis (raw)",
           "Raw RPM axis for boost control (7-cell).",
           main_addr=0x2600, rows=7, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Boost RPM Axis (scaled)",
           "Scaled RPM axis for boost control (5-cell).",
           main_addr=0x2602, rows=5, cols=1,
           map_type="raw", unit="raw", confidence="CONFIRMED"),

    MapDef("Boost PID P-Term",
           "Boost control PID proportional term (5-cell). raw/192×100 = %.",
           main_addr=0x2609, rows=5, cols=1,
           map_type="raw", unit="%",
           decode=_prj_wgdc_decode,
           confidence="CONFIRMED"),

    MapDef("Boost PID I-Term",
           "Boost control PID integral term (5-cell). raw/192×100 = %.",
           main_addr=0x263C, rows=5, cols=1,
           map_type="raw", unit="%",
           decode=_prj_wgdc_decode,
           confidence="CONFIRMED"),

    MapDef("Boost PID D-Term",
           "Boost control PID derivative term (5-cell). raw/192×100 = %.",
           main_addr=0x2641, rows=5, cols=1,
           map_type="raw", unit="%",
           decode=_prj_wgdc_decode,
           confidence="CONFIRMED"),

    MapDef("Boost PID D-Term 2",
           "Boost control PID secondary derivative term (5-cell). raw/192×100 = %.",
           main_addr=0x2646, rows=5, cols=1,
           map_type="raw", unit="%",
           decode=_prj_wgdc_decode,
           confidence="CONFIRMED"),

    MapDef("Boost IAT Correction Alt 1",
           "Boost IAT correction table, alternate 1 (8×10). raw/128.",
           main_addr=0x264B, rows=8, cols=10,
           map_type="raw", unit="corr",
           decode=lambda b: round(b/128, 3),
           confidence="CONFIRMED"),

    MapDef("Boost IAT Correction Alt 2",
           "Boost IAT correction table, alternate 2 (8×10). raw/128.",
           main_addr=0x269B, rows=8, cols=10,
           map_type="raw", unit="corr",
           decode=lambda b: round(b/128, 3),
           confidence="CONFIRMED"),

    MapDef("Boost IAT Correction Alt 3",
           "Boost IAT correction table, alternate 3 (8×10). raw/128.",
           main_addr=0x26EB, rows=8, cols=10,
           map_type="raw", unit="corr",
           decode=lambda b: round(b/128, 3),
           confidence="CONFIRMED"),

    MapDef("Base WGDC IAT Correction",
           "Base wastegate duty cycle IAT correction (8×10). raw/128.",
           main_addr=0x282B, rows=8, cols=10,
           map_type="raw", unit="corr",
           decode=lambda b: round(b/128, 3),
           confidence="CONFIRMED"),

    MapDef("Boost pATM Correction 1",
           "Boost atmospheric pressure correction table 1 (8×10). raw/128.",
           main_addr=0x2893, rows=8, cols=10,
           map_type="raw", unit="corr",
           decode=lambda b: round(b/128, 3),
           confidence="CONFIRMED"),

    MapDef("Boost pATM Correction 2",
           "Boost atmospheric pressure correction table 2 (8×10). raw/128.",
           main_addr=0x28E3, rows=8, cols=10,
           map_type="raw", unit="corr",
           decode=lambda b: round(b/128, 3),
           confidence="CONFIRMED"),

    MapDef("Base WGDC pATM Correction",
           "Base wastegate duty cycle atmospheric pressure correction (8×10). raw/128.",
           main_addr=0x2933, rows=8, cols=10,
           map_type="raw", unit="corr",
           decode=lambda b: round(b/128, 3),
           confidence="CONFIRMED"),

    MapDef("Cyl KR Sum Threshold",
           "Cylinder-selective KR sum threshold for global knock retard (8-cell). raw×0.5 = °.",
           main_addr=0x2AA0, rows=8, cols=1,
           map_type="raw", unit="°",
           decode=lambda b: round(b*0.5, 2),
           confidence="CONFIRMED"),

    MapDef("Global KR Activation Threshold",
           "Global knock retard activation threshold vs MAP (8-cell). kPa decode.",
           main_addr=0x2AAC, rows=8, cols=1,
           map_type="raw", unit="kPa",
           decode=_prj_map_kpa_decode,
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
    name                = "3B / RR — 200 20vT / UrQ RR (404)",
    software_id         = "404",
    engine_codes        = ["3B", "RR"],
    # ECU assembly PNs:
    #   447907404 AA — Type 44 / C3 body (Audi 200 20vT)       cal tag 0x029B
    #   857907404 B  — Type 85 / B2 body (UrQuattro RR)         cal tag 0x0253
    # The 447/857 prefix encodes the Audi body platform, not a hardware revision.
    # Both variants use firmware build 0xF004 and share 81.8% of code bytes.
    ecu_pns             = ["447907404AA", "857907404B",
                           "895907404BA", "443907404", "895907404"],
    bosch_pns           = ["0261200451",   # 3B / 447907404 AA
                           "0261200453",   # RR / 857907404 B
                           "0261200484"],  # earlier production
    # Bosch ROM part numbers (embedded in fuel/ign chip ID string @ 0x7F00):
    #   1267356462 — 3B fuel/ign chip  (B57741L / Intel 27C256 / ©1984)
    #   1267356261 — RR fuel/ign chip
    dual_eprom          = True,
    working_half_offset = 0,       # 32KB flat file, no offset needed
    main_maps           = _MAPS_3B_MAIN,
    boost_maps          = _MAPS_BOOST_551,
    notes               = (
        "Distributor ignition (not coil packs). "
        "Map addresses CONFIRMED from PRJ MapFinder output and verified "
        "against direct chip reads (2026-03 RE session). "
        # CPU
        "CPU: Siemens BD26422 (DIP-40, Intel 8051 core, ©INTEL 90) — same "
        "silicon family as AAN SAB80C535. BD26422 is Bosch's internal PN for "
        "custom-labelled Siemens 8051 derivative. "
        # Firmware
        "Firmware build 0xF004 is shared between 3B-AA and RR-B variants. "
        "Ign map 1 (0x7052) is byte-for-byte identical across both variants. "
        "Fuel maps differ ~69% between variants (RR runs leaner mid-range). "
        "Large code diff at 0x4893-0x5B23 (4753 bytes) — likely "
        "distributor sequencing / platform-specific I/O. "
        # Boost chip architecture
        "BOOST CHIP ARCHITECTURE: The boost chip is NOT a data-only ROM. "
        "It is executable 8051 code running on a second independent MCU on "
        "the MAP sub-board. Starts with CLR EA (0xC2 0xAF) at 0x0000 — "
        "inline startup, no LJMP redirect. Has its own interrupt handlers "
        "(Timer0, Timer1, INT0, INT1, Serial) and calibration tables. "
        "Boost chip build: 0x0254 (3B-AA) / 0x0255 (RR-B). "
        # IPC
        "IPC registers between fuel CPU and boost MCU: "
        "0xA040 and 0xA080 (MOVX targets, 8+ refs each in fuel chip). "
        # Hardware
        "EPROM1 silicon: Intel 27C256 (©1984), Bosch label B57741L. "
        "EPROM2 silicon: manufacturer unconfirmed (obscured on board). "
        "EPROM2 retention: factory RTV silicone + wire-form spring clip. "
        "Stock MAP sensor: Bosch 0 273 003 204, 200kPa."
    ),
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
    # Direct chip reads — 2026-03 RE session (447907404AA and 857907404B)
    0xFBE0A74A: ("404",       "Stock — RR fuel/ign, 857907404B,  ROM PN 1267356261 (direct read)"),
    0xF50660DA: ("404_boost", "Stock — 3B boost chip, 447907404AA, build 0x0254 (direct read)"),
    0xEA8D46DF: ("404_boost", "Stock — RR boost chip, 857907404B,  build 0x0255 (direct read)"),
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

# 551AA_0202 axis address lookup: data_offset → (row_axis_addr, col_axis_addr)
# row  = y-axis in TunerPro convention (load / MAP / coolant etc.)
# col  = x-axis (RPM or secondary dimension)
# None means no dedicated axis table — caller falls back to sequential indices
# Source: PRJ m232.xdf z-axis + y-axis + x-axis mmedaddress values
_AXES_0202: dict[int, tuple[int | None, int | None]] = {
    # Fuel P/T family (rows=load, cols=RPM×40)
    0x0E13: (0x0E03, 0x0DF1),
    0x21A4: (0x0E03, 0x0DF1),
    0x2224: (0x0E03, 0x0DF1),
    # Ignition P/T family (rows=load, cols=RPM×40)
    0x125F: (0x124F, 0x123D),
    0x1594: (0x124F, 0x123D),
    0x10A8: (0x124F, 0x123D),
    0x2198: (0x124F, 0x123D),   # Ign LPG
    0x2424: (0x124F, 0x123D),   # Ign race fuel
    # VE table (rows=MAP kPa, cols=RPM×40)
    0x2074: (0x2064, 0x2052),
    # Boost / WG maps (rows=RPM narrow, cols=RPM raw/scaled)
    0x2480: (0x2391, 0x2600),
    0x2520: (0x2391, 0x2600),
    # Boost correction tables 8×10 (rows=RPM narrow, cols=IAT or pATM)
    0x264B: (0x2391, 0x2385),
    0x269B: (0x2391, 0x2385),
    0x26EB: (0x2391, 0x2385),
    0x282B: (0x2391, 0x2385),
    0x2893: (0x2391, 0x2385),
    0x28E3: (0x2391, 0x2385),
    0x2933: (0x2391, 0x2385),
    # Idle / ISV (rows=coolant, cols=RPM×40)
    0x1AA2: (0x1A9C, 0x1A93),
    0x1ADB: (0x1AD5, 0x1ACE),
    # Wall film (rows=coolant, cols=RPM×40)
    0x0D66: (0x0D58, 0x0D60),
    0x0D4A: (0x0D46, 0x0D41),
    # MAF low-voltage correction (rows=voltage, cols=RPM×40)
    0x0C3C: (0x0C30, 0x0C22),
    # Ignition idle / warmup (rows=coolant, cols=RPM×40)
    0x11B7: (None,   0x11AA),
    0x11E2: (None,   0x1202),
    0x120F: (0x120A, 0x1202),
    0x17C7: (None,   0x1202),
    0x1A3D: (None,   0x1A93),
    0x22D9: (None,   0x11AA),
    0x2312: (0x120A, 0x1202),
    # Dwell (col=RPM×40 only)
    0x150E: (None,   0x14F9),
    # Fuel IATxRPM correction (rows=IAT, cols=RPM×40 subset)
    0x0F21: (0x1024, 0x0DF1),
    0x22B2: (0x1024, 0x0DF1),
    # Knock threshold per cylinder (rows=MAP, cols=RPM×40)
    0x2218: (None,   0x21D9),
    0x2240: (None,   0x21D9),
    0x2268: (None,   0x21D9),
    0x2290: (None,   0x21D9),
    0x22B8: (None,   0x21D9),
    # Load filter (rows=load, cols=RPM×40)
    0x0D27: (None,   0x0D1B),
    # Alpha/N error filling map (rows=TPS, cols=RPM×40)
    0x1ED2: (0x1ECC, 0x1EBE),
    # 0x9F7C RPM×Load 4×4 (reuse ign axes, first 4 values)
    0x1F88: (0x124F, 0x123D),
    # Warmup enrichment (no dedicated axes — use sequential)
    # Wall film load gradient, retard, events — 1D, no axes needed
}

# RPM-scaled columns in 0x0202: raw × 40 = RPM for these axis addresses
_RPM_SCALE_ADDRS_0202 = {
    0x0DF1, 0x123D, 0x2052, 0x2600, 0x2391, 0x1A93,
    0x11AA, 0x1202, 0x14F9, 0x0C22, 0x0D60, 0x1EBE, 0x21D9,
}

# MAP kPa columns in 0x0202: raw / 1.035 ≈ kPa absolute
_MAP_SCALE_ADDRS_0202 = {0x2064}

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
    Return (row_axis, col_axis) appropriate for the variant and map.

    For 551AA_0202 (prjmod/034EFI): reads axis values from ROM at the
    confirmed WH addresses for each map, decoded appropriately (RPM×40,
    MAP kPa, or raw). Falls back to sequential indices for unmapped tables.

    For 551C/551AA: returns the known confirmed axis arrays.
    For 3B/404/V8: reads axis bytes from the Bosch descriptor header,
                   which sits immediately before map_def.main_addr (data_addr - 36).

    Returns lists of decoded values — the caller uses them directly as
    row/column header labels in the map table.
    """
    sw = variant.software_id if variant else ""

    if sw == "551AA_0202":
        row_addr, col_addr = _AXES_0202.get(map_def.main_addr, (None, None))

        def _read_axis(addr: int | None, n: int) -> list:
            if addr is None or addr + n > len(rom):
                return list(range(n))
            raw = list(rom[addr: addr + n])
            if addr in _RPM_SCALE_ADDRS_0202:
                return [b * 40 for b in raw]
            if addr in _MAP_SCALE_ADDRS_0202:
                return [round(b / 1.035, 1) for b in raw]
            return raw

        rows = _read_axis(row_addr, map_def.rows)
        cols = _read_axis(col_addr, map_def.cols)
        return rows, cols

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
