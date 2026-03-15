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
    VARIANT_404,
    VARIANT_V8_ABH,
    VARIANT_V8_PT,
]


# ── Known CRC32 fingerprints ──────────────────────────────────────────────────
# CRC32 of the 32KB working half.
# Working half = upper 32KB of 64KB file (offset 0x8000) for 551x.
# Working half = entire file for 3B/V8.

KNOWN_CRCS: dict[int, tuple[str, str]] = {
    0xCCE86BB3: ("551C",  "Stock — ADU RS2 (adu_fuel-ign_551c.bin)"),
    0x4929C54C: ("551AA", "Stock — ABY S2 Coupe (aby_fuel-ign_551aa.bin)"),
    0x0AE3CACD: ("404",   "Stock — 3B 200 20vT (stock fuel.BIN)"),
    0x9245FA10: ("404",   "Stock — 3B Audi S2 (0261200484 MapFinder bin)"),
    0x594F97FB: ("404V8", "Stock — PT V8 3.6L"),
    0x750A9EB0: ("404V8", "ABT tune — PT V8 3.6L"),
}

# Build number ranges for MEDIUM confidence detection
BUILD_RANGES: dict[str, tuple[int, int]] = {
    "551C":  (0x4000, 0x7000),
    "551AA": (0x0000, 0x3FFF),
    "404":   (0xE000, 0xFFFF),
    "404V8": (0xA000, 0xCFFF),
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
