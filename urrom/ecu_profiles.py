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
BOOST_CHIP_WORKING  = 0x8000    # 32KB boost chip (551AA/B/C); 8KB chips (3B/404) also fit within
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

    # Idle ignition map A (0x3D05) — PROVISIONAL
    # ABY direct chip read: 25-byte (5×5?) descriptor, RPM axis ~2320 RPM area.
    # Values 13–22°BTDC — consistent with low-load idle ign advance.
    # Identical mirror at 0x3E65. Function not confirmed from disassembly.
    MapDef("Idle Ign map A (3D05)",
           "Low-RPM ignition map. ABY: values 13–22°BTDC at ~2320 RPM axis. "
           "Possible idle/off-idle ignition trim. Identical mirror at WH 0x3E65. "
           "PROVISIONAL — function not confirmed from disassembly.",
           main_addr=0x3D05, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="PROVISIONAL",
           notes="Mirror map at 0x3E65. Investigate before writing."),

    MapDef("Idle Ign map B (3E65)",
           "Low-RPM ignition map (mirror of 0x3D05). "
           "ABY direct chip read: identical values to 3D05. PROVISIONAL.",
           main_addr=0x3E65, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="PROVISIONAL",
           notes="Mirror of 0x3D05. Do not write unless 3D05 is confirmed."),

    # End-of-calibration RPM table (replaces mis-labelled "Rev Limit")
    MapDef("Idle Ign map A (3D05)",
           "Low-RPM ignition map. ABY: values 13–22°BTDC at ~2320 RPM axis. "
           "Possible idle/off-idle ignition trim. Identical mirror at WH 0x3E65. "
           "PROVISIONAL — function not confirmed from disassembly.",
           main_addr=0x3D05, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="PROVISIONAL",
           notes="Mirror map at 0x3E65. Investigate before writing."),

    MapDef("Idle Ign map B (3E65)",
           "Low-RPM ignition map (mirror of 0x3D05). "
           "ABY direct chip read: identical values to 3D05. PROVISIONAL.",
           main_addr=0x3E65, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="PROVISIONAL",
           notes="Mirror of 0x3D05. Do not write unless 3D05 is confirmed."),

        MapDef("End-of-Cal RPM table",
           "32-byte RPM-encoded table at end of working half (WH 0x3FE0–0x3FFF). "
           "ABY direct chip read: values span 1000–8000 RPM (raw × 40). "
           "Pattern suggests tiered RPM thresholds — possibly ignition cut "
           "or fuel cut hysteresis. NOT the stock rev limit address. "
           "Stock rev limit is in 8051 code as an immediate compare; "
           "requires Ghidra disassembly to locate. "
           "For prjmod ROMs, LC/NLS rev limit is at WH 0x0617 / 0x066E.",
           main_addr=0x3FE0, rows=2, cols=16,
           map_type="raw", unit="RPM",
           confidence="PROVISIONAL",
           notes="DO NOT write — address not fully understood. Read-only reference only."),
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

    MapDef("End-of-Cal RPM table",
           "32-byte RPM table at WH 0x3FE0–0x3FFF. Values × 40 = RPM. "
           "Likely RPM-threshold array (ign/fuel cut). NOT a simple rev limit. "
           "See variant RE notes for details.",
           main_addr=0x3FE0, rows=2, cols=16,
           map_type="raw", unit="RPM",
           confidence="PROVISIONAL"),
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

    MapDef("End-of-Cal RPM table",
           "32-byte RPM table at WH 0x3FE0–0x3FFF. Values × 40 = RPM. "
           "Likely RPM-threshold array (ign/fuel cut). NOT a simple rev limit.",
           main_addr=0x3FE0, rows=2, cols=16,
           map_type="raw", unit="RPM",
           confidence="PROVISIONAL"),
]

# ── V8 maps ───────────────────────────────────────────────────────────────────
# Single 32KB flat EPROM. Limited community documentation.
# Addresses UNCONFIRMED — V8 has different codebase from I5 turbo.

# V8 map addresses confirmed from binary RE of ABH 557A + V8Q 557E (2026-03).
# ABH and V8Q share identical firmware (only 3-byte LJMP offset diff) → same cal layout.
# S6 557C uses different firmware build → different cal addresses (not yet RE'd).
# The working half (WH) = upper 32KB of the 65536B split-bank EPROM file.
# Fuel:  WH 0x2D20 — 10×14 = 140 cells (102=lean bound, 128=stoich, 243=rich max)
# Ign:   7 maps in WH 0x33DF–0x38F0, dimensions 58-63 cells each (not 16×16)
#        Same decode formula as 5-cyl: raw × 0.6491 − 8.22 = °BTDC
#        ABH avg 33–36°, V8Q avg 37–41° (+4° across all maps)

def _v8_fuel_decode(r):   return r            # raw; 128 = stoich reference
def _v8_fuel_encode(v):   return max(0,min(255,int(round(v))))

_MAPS_V8_MAIN = [
    MapDef("Fuel Map",
           "Main V8 fuel map. 128 = stoich reference. "
           "Values 102–243 (102 = lean limit, 243 = max enrichment). "
           "10 load rows × 14 RPM cols. ABH 557A and V8Q 557E are identical here.",
           main_addr=0x2D20, rows=10, cols=14,
           map_type="fuel", unit="raw",
           decode=_v8_fuel_decode, encode=_v8_fuel_encode,
           confidence="CONFIRMED",
           notes="Confirmed: ABH==V8Q (0 diffs). S6 557C differs (85 diffs, different layout)."),

    MapDef("Ign Map A (primary)",
           "Primary P/T ignition map. ABH avg 34.5°, V8Q avg 38.8° (+4.3°). "
           "3F n=59 header block. Decode: raw×0.6491−8.22=°BTDC.",
           main_addr=0x33DF+2, rows=4, cols=15,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED",
           notes="Confirmed: both ABH and V8Q decode to plausible ign values 25–40°."),

    MapDef("Ign Map B",
           "Ignition map B. ABH avg 32.6°, V8Q avg 36.7° (+4.1°). 3F n=58.",
           main_addr=0x3547+2, rows=4, cols=15,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map C",
           "Ignition map C. ABH avg 36.1°, V8Q avg 40.5° (+4.4°). 3F n=63.",
           main_addr=0x3585+2, rows=4, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map D",
           "Ignition map D. ABH avg 33.0°, V8Q avg 36.7° (+3.7°). 3F n=58.",
           main_addr=0x3627+2, rows=4, cols=15,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map E",
           "Ignition map E. ABH avg 35.7°, V8Q avg 40.5° (+4.8°). 3F n=63.",
           main_addr=0x3665+2, rows=4, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map F",
           "Ignition map F. ABH avg 32.6°, V8Q avg 36.5° (+3.9°). 3F n=58.",
           main_addr=0x38B2+2, rows=4, cols=15,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

    MapDef("Ign Map G",
           "Ignition map G. ABH avg 36.1°, V8Q avg 40.5° (+4.4°). 3F n=63.",
           main_addr=0x38F0+2, rows=4, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           confidence="CONFIRMED"),

]

# ── Boost chip maps ───────────────────────────────────────────────────────────
# Addresses relative to boost chip working half start.
# Source: community research — still PROVISIONAL.

# Boost chip map addresses confirmed from vwnut8392 XDF (8D0907551B RS2 Boost.xdf, 2013)
# and verified by direct binary inspection of ABY/AAN/ADU boost chips.
# All 32KB boost chips (551AA / 551B / 551C) use the same addresses.
# Each map has a mirror at address + 0x4000 (A15 state irrelevant — verified MATCH).
# Axes are hardcoded in boost MCU code; not stored in data section.
#   Approximate RPM axis (10 pts): 600,1000,1500,2000,2500,3000,4000,5000,6000,7200
#   MAF/load axis (16 pts):        1–16 (relative load, MAF-derived)
_BOOST_RPM_AXIS  = [600, 1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 7200]
_BOOST_LOAD_AXIS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

_MAPS_BOOST_551 = [
    MapDef("Boost Pressure Target",
           "Boost target vs RPM (rows) × load (cols). "
           "Decode: raw/255 × 250 = kPa (stock 250kPa sensor). "
           "RS2/AAN→RS2 with 300kPa mod: raw/255 × 300 = kPa. "
           "vwnut8392 XDF note: same values as 551AA S4/S6.",
           main_addr=0x2520, rows=10, cols=16,
           map_type="boost", unit="kPa", chip="boost",
           confidence="CONFIRMED"),

    MapDef("N75 Wastegate Duty Cycle",
           "Wastegate solenoid duty cycle % vs RPM (rows) × load (cols). "
           "Higher value = more boost. Decode: raw/255 × 100 = %. "
           "vwnut8392 XDF note: same as 551AA S4/S6.",
           main_addr=0x2480, rows=10, cols=16,
           map_type="raw", unit="%", chip="boost",
           confidence="CONFIRMED"),

    MapDef("Boost Pressure Limit",
           "Per-RPM absolute boost ceiling (8 RPM points). "
           "Decode: raw/255 × 250 = kPa. Acts as hard overboost cut.",
           main_addr=0x2A96, rows=8, cols=1,
           map_type="boost", unit="kPa", chip="boost",
           confidence="CONFIRMED"),

    MapDef("Characteristic Map",
           "Boost control characteristic map vs RPM × load. "
           "Influences boost response shape (not a direct target).",
           main_addr=0x264B, rows=8, cols=10,
           map_type="raw", unit="raw", chip="boost",
           confidence="CONFIRMED"),

    MapDef("Correction Table (2218)",
           "Boost correction vs RPM × load. "
           "vwnut8392 XDF: MODIFIED in most RS2 tunes, traces constantly.",
           main_addr=0x2218, rows=25, cols=8,
           map_type="raw", unit="raw", chip="boost",
           confidence="CONFIRMED"),

    # Additional tables from vwnut8392 8D0907551B RS2 Boost.xdf (2013-02-17)
    # These three appear in every tuned RS2/AAN boost chip — unknown function,
    # but all "trace constantly" (live-updated by ECU during operation).
    MapDef("Boost unknown A (2ACE)",
           "Unknown 16×10 table. Traces constantly — likely boost correction. "
           "Mirror at 0x6ACE.",
           main_addr=0x2ACE, rows=10, cols=16,
           map_type="raw", unit="raw", chip="boost",
           confidence="PROVISIONAL"),

    MapDef("Boost unknown B (2B6E)",
           "Unknown 16×10 table. May be related to boost/N75 correction. "
           "Mirror at 0x6B6E.",
           main_addr=0x2B6E, rows=10, cols=16,
           map_type="raw", unit="raw", chip="boost",
           confidence="PROVISIONAL"),

    MapDef("Boost unknown C (2C0E)",
           "Unknown 16×10 table. "
           "Mirror at 0x6C0E.",
           main_addr=0x2C0E, rows=10, cols=16,
           map_type="raw", unit="raw", chip="boost",
           confidence="PROVISIONAL"),

    MapDef("Boost limit detail (2A8C)",
           "Detailed boost limit 9×2. Supplements the 1D limit table at 0x2A96. "
           "Mirror at 0x6A8C.",
           main_addr=0x2A8C, rows=2, cols=9,
           map_type="boost", unit="kPa", chip="boost",
           confidence="PROVISIONAL"),
]

# 3B/RR boost chip (8KB, 27C64): different architecture, code-only chip
# Data tables at 0x1650+ per direct RE session (March 2026)
_BOOST_3B_RPM_AXIS  = [500, 1000, 1500, 2000, 2500, 3000, 4000, 5000]
_BOOST_3B_LOAD_AXIS = [1, 2, 3, 4, 5, 6, 7, 8]

_MAPS_BOOST_404 = [
    MapDef("Boost Target",
           "3B/RR boost target tables at 0x1650+ (direct RE session 2026). "
           "Executable MCU code chip — tables in upper data region.",
           main_addr=0x1650, rows=8, cols=8,
           map_type="boost", unit="raw", chip="boost",
           confidence="PROVISIONAL"),
]


# ── Variant registry ──────────────────────────────────────────────────────────

VARIANT_551C = ROMVariant(
    name                = "ADU — RS2 Avant (551C)",
    software_id         = "551C",
    engine_codes        = ["ADU"],
    ecu_pns             = ["8A0907551C"],
    bosch_pns           = ["0261203543"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_551C_MAIN,
    boost_maps          = _MAPS_BOOST_551,
    notes               = (
        "ADU — Audi RS2 Avant 2.2L 20v turbo. "
        "ID string: '8A0907551C  2,2l R5 MOTR.RHV RS2D01PMC 0261203543 1267358668'. "
        "ECU PN 8A0907551C. Bosch ECU PN 0261203543. ROM PN 1267358668. "
        "Firmware build 0x0274, cal tag 0x7F02, reset LJMP 0x1329. "
        "Boost chip: 32KB (27C256), build 0x0202. "
        "FIRMWARE SIBLING OF ABY 551B: identical build, trigger (D01), reset vector. "
        "Zero code diff vs ABY — all 4172 differing WH bytes are calibration (>= 0x2000). "
        "RS2 runs richer fuel calibration and different ignition advance vs ABY S2. "
        "300kPa MAP sensor hardware mod required (R201 swap). "
        "XDF: RS2.xdf is a TunerPro XDF for the ADU chip (not a GM ECM file). "
        "BinSize=0x4000 (16KB WH). XDF addresses are WH+0x8000-based (i.e. 0xAE17=WH 0x2E17). "
        "XDF confirms: fuel@0xAE17(WH 0x2E17), ign PT@0xB0AC(WH 0x30AC), "
        "ign PT 2-7@0xB263/0xB387/0xB598/0xB6BC/0xB80D/0xB931 (WH 0x3263-0x3931). "
        "XDF decode formulas verified against direct chip read: "
        "fuel=X×0.0078125 (1/128, stoich ref=1.000), ign=X×0.6491−8.2186 °BTDC. "
        "ADU stock ign map 1 row0: 23.6°BTDC at PT — RS2 advance confirmed. "
        "XDF checksum: DataStart=0x08 DataEnd=0x3FFF StoreAddr=0x06 — "
        "this is the PRJmod/TunerPro checksum scheme for tuned ROMs, "
        "stock chips carry code/data at WH[0x0006], not a computed checksum."
    ),
)

# AAN existed in two distinct hardware generations differentiated by trigger system.
# The Bosch ID string encodes this directly:
#
#   551A  (early): "4A0907551A  MOTOR D02PMC 0261200465 1267356703"
#                  D02 = distributor-referenced trigger (hall sensor in distributor,
#                  same architecture as 3B/RR). Boost chip: 8KB (27C64).
#
#   551AA (late):  "4A0907551AA 2,2l R5 MOTR.RHV HS D03PMC 0261200465 1267357391"
#                  HS D03 = cam pulley hall sensor trigger. "HS" = Hall Sensor flag.
#                  Boost chip: 32KB (27C256) — 8KB code + 24KB calibration tables.
#
# Both share Bosch ECU PN 0261200465 (same hardware), firmware build 0x0202,
# and 80.6% working half code similarity. Calibration tables are entirely different.
# Reset handlers are byte-identical opening (shared peripheral init), diverging
# at trigger system ISR code.

VARIANT_551AA = ROMVariant(
    name                = "AAN — UrS4 / UrS6, cam trigger (551AA / D03)",
    software_id         = "551AA",
    engine_codes        = ["AAN"],
    ecu_pns             = ["4A0907551AA"],
    bosch_pns           = ["0261200465"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_551AA_MAIN,   # NOTE: uses ABY-confirmed addresses — AAN addresses unverified
    boost_maps          = _MAPS_BOOST_551,
    notes               = (
        "Late AAN — cam pulley hall sensor trigger (HS D03 in ID string). "
        "ECU PN 4A0907551AA, Bosch ECU PN 0261200465, ROM PN 1267357391. "
        "Firmware build 0x0812, cal tag 0x0202, reset LJMP 0x1297. "
        "Boost chip: 32KB (27C256), 8KB MCU code + 24KB calibration tables. "
        "IMPORTANT: Map addresses here are ABY-confirmed (fuel 0x2E17, ign 0x30AC+). "
        "AAN map addresses from PRJ XDF differ (fuel@0x0E13, ign@0x125F) — "
        "AAN-specific address verification pending against direct chip read. "
        "See VARIANT_551B for ABY S2 Coupe (D01 trigger, addresses confirmed). "
        "See VARIANT_551A for early distributor-trigger AAN (D02, 8KB boost)."
    ),
)

VARIANT_551B = ROMVariant(
    name                = "ABY — S2 Coupe, cam trigger (551B / D01)",
    software_id         = "551B",
    engine_codes        = ["ABY"],
    ecu_pns             = ["895907551B"],
    bosch_pns           = ["0261203643"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_551AA_MAIN,   # addresses confirmed on ABY bin direct read
    boost_maps          = _MAPS_BOOST_551,
    notes               = (
        "ABY — Audi S2 Coupe (Type 85 body), cam pulley hall sensor trigger. "
        "ID string: '895907551B  2,2l R5 MOTR.RHV HS D01PMC 0261203643 1267358375'. "
        "ECU PN 895907551B (Type 85 / B2 platform prefix, vs AAN's Type 44 prefix). "
        "Bosch ECU PN 0261203643 (different from AAN's 0261200465). "
        "ROM PN 1267358375. Firmware build 0x0274, cal tag 0x7F02, reset LJMP 0x1329. "
        "Boost chip: 32KB (27C256), build 0x0202 — same architecture as AAN. "
        "Map addresses CONFIRMED by direct chip read (2026-03 RE session): "
        "fuel WH 0x2E17, ign WH 0x30AC / 0x3263 / 0x3387 / 0x3598 / 0x36BC / 0x380D / 0x3931. "
        "D01 vs AAN D03: both cam-referenced; code difference may reflect "
        "sensor connector or harness pinout between Type 85 and Type 44 bodies. "
        "Working half similarity vs AAN 551AA: 57.1%% identical (42.9%% differ, 8 clusters). "
    ),
)

VARIANT_551B_D02 = ROMVariant(
    name                = "RS2 early — distributor trigger (551B / D02)",
    software_id         = "551B_D02",
    engine_codes        = ["ADU"],
    ecu_pns             = ["8A0907551B"],
    bosch_pns           = ["0261203478"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_551AA_MAIN,
    boost_maps          = _MAPS_BOOST_551,
    notes               = (
        "Early RS2 — distributor hall sensor trigger (D02 in ID string). "
        "ID: '8A0907551B  2,2l R5 MOTR.RHV RS2D02PMC 0261203478 1267358289'. "
        "ECU PN 8A0907551B. Bosch ECU PN 0261203478. ROM PN 1267358289. "
        "XDF confirmed (RS2 551B fuel timing.xdf, vwnut8392 / Matt@S&M Autosport 2013): "
        "fuel map WH 0x0E13 (64KB addr 0x8E13), ign maps WH 0x125F/0x159F/0x16C3/"
        "0x1809/0x192D/0x108C — note: same WH addresses as PRJ 551AA XDF. "
        "Firmware build 0x0202, cal tag 0xA1E9, reset LJMP 0x1329. "
        "Mirrors the AAN D02→D03 transition: RS2 also had an early distributor "
        "variant (D02) before the cam-trigger D01 version (551C). "
        "Boost chip: 32KB, build 0x0202. "
        "Map addresses PROVISIONAL — same layout as 551B/551C assumed, unverified."
    ),
)

VARIANT_551A = ROMVariant(
    name                = "AAN early — distributor hall trigger (551A / D02)",
    software_id         = "551A",
    engine_codes        = ["AAN"],
    ecu_pns             = ["4A0907551A"],
    bosch_pns           = ["0261200465"],
    dual_eprom          = True,
    working_half_offset = 0x8000,
    main_maps           = _MAPS_551AA_MAIN,   # same address layout, different cal values
    boost_maps          = _MAPS_BOOST_551,
    notes               = (
        "Early AAN — distributor-referenced hall sensor trigger (D02 in ID string). "
        "Same hardware as 551AA (Bosch ECU PN 0261200465), different firmware. "
        "Boost chip is 8KB (27C64), same size as 3B/RR — no extended table region. "
        "Boost chip build 0xA04B (different numbering scheme from 551AA's 0x0202). "
        "ROM PN 1267356703. Firmware build 0x0202, calibration tag 0xA04A. "
        "Reset vector LJMP 0x117A (vs 551AA's 0x1297). "
        "Shares 80.6%% of working half code with 551AA. "
        "Map addresses PROVISIONAL — same layout assumed, unverified independently."
    ),
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
    boost_maps          = _MAPS_BOOST_404,
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

VARIANT_551D = ROMVariant(
    name                = "AAN/ADU — A6 C4 2.2T (551D)",
    software_id         = "551D",
    engine_codes        = ["AAN", "ADU"],
    ecu_pns             = ["4A0907551D"],
    bosch_pns           = ["0261203603"],
    dual_eprom          = True,
    working_half_offset = 32768,
    main_maps           = _MAPS_551C_MAIN,   # same layout as 551C — confirmed same family
    boost_maps          = _MAPS_BOOST_551,
    notes               = (
        "Audi A6 C4 2.2T AAN application. "
        "4A0907551D — previously undocumented variant discovered 2026-03. "
        "ROM ID: '4A0907551D  2,2l R5 MOTR.RHV AT D01PMC 0261203603 1267358374'. "
        "Trigger: D01PMC cam+Hall (same as ABY 551B). "
        "Calibration blank in collected sample. "
        "Map layout assumed identical to 551A/AA/B/C (same ECU family). "
        "UNCONFIRMED — map addresses need verification from real calibration chip."
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
    notes               = (
        "Bosch Motronic M2.3 — same 8051 CPU family as 5-cyl 404/551x. NOT M3 or M5. "
        "Single 27C512 EPROM in split-bank configuration: "
        "lower 32KB (0x0000-0x7FFF) = firmware code (10321 code bytes), "
        "upper 32KB (0x8000-0xFFFF) = calibration data (3A/3F format, same as 551x). "
        "Reset→0x1497. INT0→0x2000→0x0431 (bank 1 distributor), "
        "INT1→0x2030 (bank 2 distributor). "
        "ABH/S6 share identical INT0 handler at 0x0431. "
        "Calibration format confirmed compatible with 5-cyl: same 3A/3F headers, "
        "same ign advance decode (×0.6491-8.22). Map addresses UNCONFIRMED — "
        "use upper 32KB (0x2D00+) of ISMF file for calibration RE."
    ),
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
    VARIANT_551A,       # early AAN, distributor trigger (D02), 8KB boost chip
    VARIANT_551AA,      # late AAN, cam trigger (D03+HS), 32KB boost chip
    VARIANT_551B,       # ABY S2 Coupe, cam trigger (D01), 32KB boost chip
    VARIANT_551B_D02,   # early RS2, distributor trigger (D02), 32KB boost chip
    VARIANT_551AA_0202,
    VARIANT_404,
    VARIANT_551D,
    VARIANT_V8_ABH,
    VARIANT_V8_PT,
]


# ── Known CRC32 fingerprints ──────────────────────────────────────────────────
# CRC32 of the 32KB working half.
# Working half = upper 32KB of 64KB file (offset 0x8000) for 551x.
# Working half = entire file for 3B/V8.

KNOWN_CRCS: dict[int, tuple[str, str]] = {
    0x0808B2E5: ("551B",       "Stock — ABY/early-AAN fuel/ign WH, build 0x0274 (lower 32KB only)"),
    0xBF11DB48: ("551AA",      "BLANK AAN — factory-erased 4A0907551AA (D03PMC). Calibration all 0x02, ID string only. 65535B reader error. NOT a usable tuning baseline."),
    0xF6E33043: ("551b_boost", "Stock — ABY boost chip, 32KB, 895907551B, build 0x0202 (direct read)"),
    0x4A3CB7DC: ("551b_boost", "ABY boost chip WH core 16KB, 895907551B, build 0x0202"),
    # RS2 D02 (early distributor RS2) — from RS2_551B_bins_XDF.zip
    0xC5B30158: ("551B_D02",  "Stock — RS2 D02 fuel/ign WH, 8A0907551B, ROM PN 1267358289"),
    0xC349075E: ("551B_D02",  "Stock — RS2 D02 fuel/ign full 64KB, 8A0907551B"),
    0x288CBFBC: ("551B_D02_boost", "Stock — RS2 D02 boost 32KB, build 0x0202"),
    0x4378E077: ("551C",      "Stock — ADU/RS2 fuel/ign WH, 8A0907551C, ROM PN 1267358668 (direct read)"),
    0x1529520A: ("551C",      "Stock — ADU/RS2 fuel/ign full 64KB, 8A0907551C"),
    0x4EE87833: ("551c_boost","Stock — ADU/RS2 boost chip 32KB, 8A0907551C, build 0x0202 (direct read)"),
    0xA98CB481: ("551B",       "Stock — ABY fuel/ign WH, 895907551B, D01+HS trigger, ROM PN 1267358375"),
    0x97D26DD1: ("551B",       "Stock — ABY fuel/ign full 64KB, 895907551B"),
    # 3B / RR dual-EPROM ECU
    # Fuel/ign chip: 32KB 27C256. ROM ID string @ 0x7F00.
    # Boost chip:     8KB (executable 8051 MCU code — NOT a data ROM).
    # Correct filenames: 3b_fuel-ign_404aa.bin, 3b_boost_404aa.bin
    #                    rr_fuel-ign_404b.bin,  rr_boost_404b.bin
    # NOTE: upload files rr_boost_404b.bin and rr_fuel-ign_404b.bin had
    #       swapped names — roms/ directory has corrected copies.
    0x0AE3CACD: ("404",      "Stock — 3B fuel/ign, 447907404AA (Audi 200 20vT / UrQ / S2 early). "
                              "ROM ID: 447907404 AA  MOTOR  PMC 02. Direct chip read."),
    0xFBE0A74A: ("404",      "Stock — RR fuel/ign, 857907404B (UrQuattro RR S2 Coupe). "
                              "ROM ID: 857907404 B  MOTOR  PMC 02. Direct chip read. "
                              "Firmware identical to 3B (0xF004). Calibration differs ~5976 bytes."),
    0x9245FA10: ("404",       "Stock — 3B Audi S2 (0261200484 MapFinder bin)"),
    # AAN 551A/551AA direct chip reads — 2026-03 RE session
    # 551A = early AAN, D02 trigger (distributor hall, like 3B), 8KB boost chip
    # 551AA = late AAN, D03+HS trigger (cam pulley hall), 32KB boost chip
    0xF7432BB5: ("551A",
                           "Stock — AAN fuel/ign, 4A0907551A, D02 distributor trigger (direct read). "
                           "Calibration all 0x02 (factory-erased). "
                           "Firmware: 6376 code bytes, reset→0x0400. "
                           "NOTE: The 65536B file in roms/ contains TWO different 32KB images: "
                           "upper=this stock blank, lower=PRJmod D02PMC base ROM (reset→0x117A). "
                           "NOT a usable calibration baseline."),
    0xBBAFE260: ("551A_boost", "Stock — AAN boost, 4A0907551A,  8KB, build 0xA04B (direct read)"),
    0xB9A49F8A: ("551AA",      "Stock — AAN fuel/ign, 4A0907551AA, D03+HS cam trigger (direct read WH)"),
    0x16707F66: ("551AA_boost","Stock — AAN boost, 4A0907551AA, 32KB, build 0x0202 (direct read)"),
    0xF50660DA: ("404_boost", "Stock — 3B boost chip, 447907404AA, build 0x0254 (direct read)"),
    0xEA8D46DF: ("404_boost", "Stock — RR boost chip, 857907404B, build 0x0255 (direct read). "
                               "Executable 8051 MCU code — NOT a data-only ROM. "
                               "Correct filename: rr_boost_404b.bin"),
    0x594F97FB: ("404V8",     "Stock — PT V8 3.6L"),
    0x750A9EB0: ("404V8",     "ABT tune — PT V8 3.6L manual. "
                               "Part 441907404 (no suffix), Bosch 0261200183, ROM PN 1267355684. "
                               "Different base firmware from PT stock (reset→0x11EF vs 0x128B). "
                               "32KB single chip, real calibration. "
                               "Main cal block 0x6201-0x7650 — blank in PT stock, populated here. "
                               "Sought-after tune swapped into auto ABH 4.2L V8Q cars."),
    # 034EFI Rip Chip / prjmod 0x0202 firmware (confirmed from .034 diff analysis)
    0x956BFC9C: ("551AA_0202", "034EFI Stock Rip Chip — 4A0907551AA reconstructed stock. Fuel chip. PAIR with stock 551AA boost chip 0x16707F66. No hardware mods required."),
    0xA47011AB: ("551AA_0202", "034EFI K24/Stage 1+ — Fuel chip. PAIR with GT2871 boost 0x69156B3A or GT3071 boost 0x39DC67DA. Requires: 3.0 BAR MAP, RS2 replica injectors, 4.0 BAR FPR, stock MAF. 20psi OB / 14psi / 7200rpm / ~+40whp"),
    0x16FD8953: ("551AA_0202", "034EFI Stage 1 K24 (fuel chip variant). PAIR with boost chip. Requires: 3.0 BAR MAP, RS2 injectors, 4.0 BAR FPR, stock MAF."),
    0x9A8A6B4E: ("551AA_0202", "034EFI GT28RS Stage 1 R2 — Fuel chip. PAIR with GT2871 or GT3071 boost chip. Requires: 3.0 BAR MAP, 550cc Bosch injectors + adapters, stock MAF. 27psi OB / 16psi / 7000rpm / 285whp / 355ft-lb"),
    0x6F3AE675: ("551AA_0202", "034EFI GT3071 Stage 1 R8 42lb Green Tops — Fuel chip. PAIR with GT3071 boost 0x39DC67DA. Requires: 3.0 BAR MAP, 440cc injectors, stock MAF. 26psi OB / 23psi / 7200rpm / 346whp"),
    0x07DA1752: ("551AA_0202", "034EFI GT3071 Stage 1 R9.1 550cc 91Oct — Fuel chip. PAIR with GT3071 boost 0x39DC67DA. Requires: 3.0 BAR MAP, 550cc injectors, stock MAF. 26psi OB / 23psi / 7200rpm"),
    0x2EB58546: ("551AA_0202", "034EFI GT2871 Stage 1 R9.1 550cc EV14 — Fuel chip. PAIR with GT2871 boost 0x69156B3A. Requires: 3.0 BAR MAP, 550cc EV14 injectors, stock MAF. 26psi OB / 22psi / 7200rpm / 330whp"),
    0xA77BB88E: ("551AA_0202", "034EFI GT2871 Stage 1 R9 440cc Siemens — Fuel chip. PAIR with GT2871 boost 0x69156B3A. Requires: 3.0 BAR MAP, 440cc Siemens injectors, stock MAF. 26psi OB / 22psi / 7200rpm / 330whp"),
    0x28C04D7B: ("551AA_0202", "034EFI Rip Chip RS2 91Oct — ABY/ADU fuel chip. PAIR with stock 551B/C boost chip. Requires: 3.0 BAR MAP, 440cc injectors, stock MAF. RS2/ADU/ABY application."),
    0x81D197CF: ("551AA_0202", "PRJ AAN/ABY stock (m232.org)"),
    0xAD9330AC: ("551AA_0202", "PRJ AAN bigturbo WMI"),
    # vwnut8392/M232-Firmware
    0x9DD68BD3: ("551AA_0202", "PRJmod AAN D03PMC (vwnut8392/M232-Firmware TMS27C512)"),
    # 0xF7432BB5 = 551A stock D02 — listed above in direct-read section


    # ── Additional 404 variants ─────────────────────────────────────────────
    0xE66098C8: ("404",      "Stock — 3B fuel/ign earlier revision, 447907404A (no AA suffix), "
                              "ROM PN 1267356259. Bosch 0261200451 (same as 404AA). "
                              "Different calibration AND firmware vs 404AA (4862 byte diff). "
                              "Direct chip read with real calibration."),

    # ── 551x blank chips — documented variants without calibration data ──────
    # All have factory-erased calibration (0x02 fill) but different firmware/hardware
    # The trigger type (D01/D02/D03/RS2) in the ROM ID string is the key differentiator
    0x37B475FF: ("551A",     "Stock blank — 895907551A, D02PMC distributor trigger. "
                              "Bosch 0261203145, ROM PN 1267358022. "
                              "Early RS2 / S2 Avant variant. Calibration erased."),
    0xE392CCBC: ("551C",     "Stock blank — 4A0907551C, D01PMC cam trigger. "
                              "Bosch 0261203601, ROM PN 1267358373. "
                              "Audi S6 C4 Turbo — DIFFERENT from ADU/RS2 551C (8A0907551C). "
                              "Calibration erased. '551C' covers multiple applications."),
    0x138957FE: ("551B_D02", "Stock blank — 8A0907551B, RS2D03PMC cam trigger. "
                              "Bosch 0261203478, ROM PN 1267358289. "
                              "Later RS2 with cam trigger — different from early RS2 D02. "
                              "Calibration erased."),
    0xC906D08C: ("551D",     "Stock blank — 4A0907551D, D01PMC cam trigger. "
                              "Bosch 0261203603, ROM PN 1267358374. "
                              "Audi A6 C4 2.2T AAN — PREVIOUSLY UNDOCUMENTED VARIANT. "
                              "Calibration erased. Likely shares map layout with 551A/AA/B/C."),
    0x4DD34924: ("551AA",    "Stock blank — 4A0907551AA, D01PMC cam trigger. "
                              "Bosch 0261200465, ROM PN 1267356711. "
                              "AAN cam trigger build. Calibration erased."),
    0x0DF3620A: ("551AA",    "Stock blank — 4A0907551AA, D02PMC distributor trigger. "
                              "Bosch 0261200465, ROM PN 1267357248. "
                              "AAN distributor trigger build. Calibration erased."),
    0x742F4983: ("551B",     "Stock blank — 4A0907551B, D01PMC cam trigger AT. "
                              "Bosch 0261203005, ROM PN 1267357249. "
                              "AAN 230HP Avant AT application. Calibration erased."),

    # ── 034EFI additional fuel chips (from 034_Files.zip, 2026-03) ────────────
    # GT3071 Stage 1 R9 440cc Siemens — paired with GT3071 boost chip below
    0xB9F0FD51: ("551AA_0202", "034EFI GT3071 Stage 1 R9 440cc Siemens — Fuel chip. PAIR with GT3071 boost 0x39DC67DA. Requires: 3.0 BAR MAP, 440cc Siemens injectors, stock MAF. 26psi OB / 23psi / 7200rpm / 346whp"),

    # ── 034EFI custom boost chips (build 0x0054, NOT stock 0x0202) ────────────
    # These use a custom 034EFI firmware (build 0x0054) that requires:
    #   - 3.0 BAR (300 kPa) MAP sensor swap (stock is 200 kPa)
    #   - Paired fuel chip from the same GT package (matching turbo spec)
    # Diffs between 2871 and 3071 boost chips cluster at 0x2480 (N75 wastegate
    # duty cycle table) — confirms turbo-specific boost profiles.
    0x69156B3A: ("551AA_0202_boost", "034EFI GT2871 Stage 1 Boost chip — Build 0x0054. PAIR with GT2871/GT28RS fuel chips. Requires 3.0 BAR MAP sensor. 26psi OB / 22psi to redline."),
    0x39DC67DA: ("551AA_0202_boost", "034EFI GT3071 Stage 1 Boost chip 26-23psi — Build 0x0054. PAIR with GT3071 fuel chips. Requires 3.0 BAR MAP sensor. 26psi OB / 23psi to redline."),

    # ── 034EFI Hitachi-based ECUs (7A 20v, AAH 12v V6) ───────────────────────
    # These are DIFFERENT ECU families from M2.3.2 — Hitachi 893906266x.
    # They use the .034 scramble format but have completely different architecture.
    # The 7A files belong to the 7A 20v Tuner / HachiRom project.
    # Detection fallback classifies them as 404/551AA — overridden by CRC here.
    # 7A NA Big MAF R2 — 893906266B (early 2-connector ECU)
    0x84B0504E: ("7A_NA", "034EFI 7A NA Big MAF 91Oct R2 — ECU 893906266B (early 2-connector). Requires 034 billet MAF housing with stock 7A element (80mm inlet). Direct upgrade, no turbo."),
    # 7A Stage 1 R1 — 893906266B (early 2-connector ECU)
    0xC075767F: ("7A_Stage1", "034EFI 7A Stage 1 91Oct R1 — ECU 893906266B (early 2-connector). Direct plug-in upgrade, stock engine components."),
    # 7A Turbo Stage 2 550cc R1 — 893906266D (late 4-connector ECU)
    0xA01C4EDA: ("7A_Turbo", "034EFI 7A Turbo Stage 2 550cc 91Oct — ECU 893906266D (late 4-connector). Requires 034 turbo kit: T3/T4 turbo, billet MAF, 9.5:1 HG, 550cc injectors. ~200whp / 250crank."),
    # 7A Turbo Kit Stage 1 R2 — 893906266B (early ECU, stock T3/T4 turbo kit)
    0x55177DDB: ("7A_Turbo", "034EFI 7A Turbo Kit Stage 1 R2 — ECU 893906266B (early 2-connector). 034 T3/T4 turbo kit, billet MAF, 9.5:1 compression HG. ~200whp Stage 1. Stage 2 available at 14psi w/ intercooler."),
    # AAH/AKH 12v V6 Stage 1+ — MMS-200 ECU (8A0 906 266A)
    # Requires: MMS-200 ECU (not MMS-300+), big bore MAF (078 133 471A)
    0x4818FA0B: ("AAH", "034EFI AAH/AKH 12v V6 Stage 1+ R1 — MMS-200 ECU only (8A0 906 266A). Big bore MAF required (Audi 078 133 471A). +12HP / +13ft-lb TQ. MMS300+ ECUs cannot be chipped."),
    # ── V8 confirmed calibration ────────────────────────────────────────────
    0x976CA7AB: ("557", "Stock — S6 V8 4.2L, 4A0907557C, D02PMC dual distributor. "
                        "Bosch 0261203599, ROM PN 1267355858. 290HP. "
                        "Real calibration — confirmed from running S6 4.2. "
                        "Same cal structure as ABH 557A, ~10-12° more advance throughout. "
                        "Different firmware build: reset→0x3820 vs ABH 0x81E3."),
    0xDECDF5C4: ("557", "Stock/tune — V8Q 441907557E, D01PMC cam trigger. "
                        "Bosch 0261203226, ROM PN 1267357441. "
                        "441 prefix = V8 Quattro body (Type 44 V8Q, not 4A/100/A6). "
                        "4175 cal bytes differ vs ABH 557A — this is a tuned calibration. "
                        "V8Q uses D01PMC cam trigger, ABH uses D02PMC distributor."),
    0x5B911FDE: ("557", "Stock — ABH V8 4.2L, 4A0907557A, D02PMC dual distributor. "
                        "Bosch 0261203143, ROM PN 1267357764. "
                        "First confirmed ABH chip with real calibration data. "
                        "Reset → 0x81E3, different firmware family from 5-cyl."),

}

# ── Hardware requirements per chip CRC ───────────────────────────────────────
# Maps WH CRC → (sensor_kpa, injector_cc, fpr_bar, notes)
# Used by Overview and Hardware tabs to surface install requirements.
CHIP_REQUIREMENTS: dict[int, dict] = {
    # 034EFI AAN/ABY/ADU tunes — all require MAP swap + matched injectors
    0x956BFC9C: {"map_kpa": 300, "injector_cc": None,  "fpr_bar": None,
                 "turbo": "K24 (stock)",  "notes": "Stock Rip Chip — 034EFI baseline. No hardware mods beyond 300kPa MAP."},
    0xA47011AB: {"map_kpa": 300, "injector_cc": None, "fpr_bar": 5.0,
                 "turbo": "K24 (stock)",
                 "notes": "Stage 1+ K24 — 20psi overboost, 14psi to redline, 7200rpm. "
                          "RS2/replica injectors (550cc), 5.0 BAR FPR, 300kPa MAP, stock MAF."},
    0x16FD8953: {"map_kpa": 300, "injector_cc": None, "fpr_bar": 5.0,
                 "turbo": "K24 (stock)",
                 "notes": "Stage 1 variant — 300kPa MAP, 5.0 BAR FPR, stock MAF."},
    0x9A8A6B4E: {"map_kpa": 300, "injector_cc": 550,  "fpr_bar": 4.0,
                 "turbo": "GT28RS",
                 "notes": "GT28RS Stage 1 R2 — 27psi overboost, 16psi redline, 7000rpm, 285whp. "
                          "550cc injectors + adapters, 300kPa MAP, 4.0 BAR FPR, stock MAF. "
                          "Recommend 7A cams for +20whp at top end."},
    0x28C04D7B: {"map_kpa": 300, "injector_cc": None, "fpr_bar": None,
                 "turbo": "RS2 (stock)",
                 "notes": "034EFI RS2 Rip Chip 91Oct — stock RS2 turbo, 300kPa MAP, stock MAF."},
    0x6F3AE675: {"map_kpa": 300, "injector_cc": 440,   "fpr_bar": 4.0,
                 "turbo": "GT3071",       "notes": "GT3071 R8 — 42lb green tops, 7200rpm, 346whp"},
    0x07DA1752: {"map_kpa": 300, "injector_cc": 550,   "fpr_bar": 4.0,
                 "turbo": "GT3071",       "notes": "GT3071 R9.1 — 550cc 91Oct, 7200rpm, 346whp, 300kPa MAP required"},
    0x2EB58546: {"map_kpa": 300, "injector_cc": 550,   "fpr_bar": 4.0,
                 "turbo": "GT2871",       "notes": "GT2871 R9.1 — 550cc EV14, 7200rpm, 330whp, 300kPa MAP required"},
    0xA77BB88E: {"map_kpa": 300, "injector_cc": 440,   "fpr_bar": 4.0,
                 "turbo": "GT2871 AAN",   "notes": "GT2871 R9 — AAN spec, 440cc Siemens, 300kPa MAP required"},
    0xB9F0FD51: {"map_kpa": 300, "injector_cc": 440,   "fpr_bar": 4.0,
                 "turbo": "GT3071",       "notes": "GT3071 R9 — 440cc Siemens, 300kPa MAP required"},
    # 034EFI boost chips — always paired with matching fuel chip
    0x69156B3A: {"map_kpa": 300, "injector_cc": None,  "fpr_bar": None,
                 "turbo": "GT2871",
                 "boost_chip": True,
                 "paired_fuel_crc": None,  # paired with GT2871 fuel chips
                 "notes": "BOOST CHIP — GT2871 Stage 1. MUST use with matched fuel chip. "
                          "034EFI custom firmware build 0x0054 (not stock 0x0202). "
                          "26psi overboost, 22psi to redline, 7200rpm. 330whp. "
                          "3.0 BAR MAP sensor REQUIRED (300kPa). Stock MAF."},
    0x39DC67DA: {"map_kpa": 300, "injector_cc": None,  "fpr_bar": None,
                 "turbo": "GT3071",
                 "boost_chip": True,
                 "paired_fuel_crc": None,
                 "notes": "BOOST CHIP — GT3071 Stage 1. MUST use with matched fuel chip. "
                          "034EFI custom firmware build 0x0054 (not stock 0x0202). "
                          "26psi overboost, 23psi to redline, 7200rpm. 346whp. "
                          "3.0 BAR MAP sensor REQUIRED (300kPa). Stock MAF."},
    # 7A Hitachi chips
    0x84B0504E: {"map_kpa": None, "injector_cc": None,  "fpr_bar": None,
                 "turbo": "NA",
                 "notes": "7A NA Big MAF — 034EFI billet MAF housing + stock element, "
                          "893906266B (2-connector ECU). No turbo."},
    0xC075767F: {"map_kpa": None, "injector_cc": None,  "fpr_bar": None,
                 "turbo": "NA",
                 "notes": "7A Stage 1 — direct upgrade, stock components, 893906266B"},
    0xA01C4EDA: {"map_kpa": None, "injector_cc": 550,   "fpr_bar": None,
                 "turbo": "T3/T4",
                 "notes": "7A Turbo Stage 2 550cc — 893906266D (4-connector ECU), "
                          "034EFI T3/T4 turbo kit, big MAF, 9.5:1 head gasket"},
    0x55177DDB: {"map_kpa": None, "injector_cc": None,  "fpr_bar": None,
                 "turbo": "T3/T4",
                 "notes": "7A Turbo Kit Stage 1 — 893906266B (2-connector ECU), "
                          "034EFI T3/T4 turbo kit, big MAF housing"},
    # AAH
    0x4818FA0B: {"map_kpa": None, "injector_cc": None,  "fpr_bar": None,
                 "turbo": "NA",
                 "notes": "AAH/AKH 12v V6 Stage 1+ — MMS-200 ECU required (8A0906266A). "
                          "Big bore MAF required (Audi 078 133 471A). "
                          "MMS-300+ ECUs CANNOT be chipped — must retrofit MMS-200."},
}

# ── Boost chip pairing table ─────────────────────────────────────────────────
#
# Maps a fuel chip CRC to its required/recommended boost chip CRC(s).
# Used to validate loaded chip pairs and show pairing warnings.
#
BOOST_CHIP_PAIRINGS: dict[int, list[int]] = {
    # 034EFI fuel chips -> required boost chip(s)
    0x956BFC9C: [0x16707F66],              # Stock Rip Chip -> stock AAN boost
    0xA47011AB: [0x69156B3A, 0x39DC67DA],  # Stage 1+ K24 -> GT2871 or GT3071
    0x16FD8953: [0x69156B3A, 0x39DC67DA],  # Stage 1 variant
    0x9A8A6B4E: [0x69156B3A, 0x39DC67DA],  # GT28RS R2
    0x6F3AE675: [0x39DC67DA],              # GT3071 R8 42lb
    0x07DA1752: [0x39DC67DA],              # GT3071 R9.1 550cc
    0x2EB58546: [0x69156B3A],              # GT2871 R9.1 550cc EV14
    0xA77BB88E: [0x69156B3A],              # GT2871 R9 440cc Siemens
    0x28C04D7B: [0xF6E33043, 0x4EE87833],  # RS2 91Oct -> ABY or ADU boost
    0xB9F0FD51: [0x39DC67DA],              # GT3071 R9 440cc Siemens
}


def get_boost_pairing(fuel_crc: int) -> list[int]:
    """Return list of acceptable boost chip CRCs for a given fuel chip CRC."""
    return BOOST_CHIP_PAIRINGS.get(fuel_crc, [])


def check_chip_pair(fuel_crc: int, boost_crc: int) -> tuple[str, str]:
    """
    Validate a fuel+boost chip pair.
    Returns (status, message): status is 'ok', 'warn', or 'mismatch'.
    """
    expected = get_boost_pairing(fuel_crc)
    if not expected:
        return ('ok', 'No specific boost chip required for this fuel chip.')
    if boost_crc in expected:
        return ('ok', 'Boost chip confirmed: correct pair for this fuel chip.')
    fuel_label  = KNOWN_CRCS.get(fuel_crc,  (None, 'unknown'))[1]
    # Use short labels (strip long description — just keep name before first dash)
    def _short(label: str) -> str:
        return label.split(" — ")[0].split(". ")[0][:60]
    boost_short = _short(KNOWN_CRCS.get(boost_crc, (None, f"0x{boost_crc:08X}"))[1])
    fuel_short  = _short(fuel_label)
    exp_shorts  = [_short(KNOWN_CRCS.get(c, (None, f"0x{c:08X}"))[1]) for c in expected]
    return ('mismatch',
            f'Boost chip MISMATCH\n'
            f'Loaded:   {boost_short}\n'
            f'Required: {" OR ".join(exp_shorts)}\n'
            f'Fuel chip: {fuel_short}')


# ── Chip hardware requirements ───────────────────────────────────────────────
#
# Maps a CRC to the hardware required to run it safely.
# Shown in the InfoStrip when a known 034EFI chip is loaded.
#
CHIP_REQUIREMENTS: dict[int, dict] = {
    # 034EFI AAN/ABY/ADU fuel chips
    0x956BFC9C: {"map_kpa": 300, "injectors": "RS2 replica", "fpr_bar": 4.0, "maf": "stock", "turbo": "stock K24"},
    0xA47011AB: {"map_kpa": 300, "injectors": "RS2 replica", "fpr_bar": 4.0, "maf": "stock", "turbo": "stock K24", "notes": "20psi OB/14psi, 7200rpm, +40whp"},
    0x16FD8953: {"map_kpa": 300, "injectors": "RS2 replica", "fpr_bar": 4.0, "maf": "stock"},
    0x9A8A6B4E: {"map_kpa": 300, "injectors": "550cc Bosch", "fpr_bar": 4.0, "maf": "stock", "turbo": "GT28RS", "notes": "27psi OB/16psi, 7000rpm, 285whp/355ft-lb"},
    0x6F3AE675: {"map_kpa": 300, "injectors": "440cc", "fpr_bar": 4.0, "maf": "stock", "turbo": "GT3071", "notes": "26psi OB/23psi, 7200rpm, 346whp"},
    0x07DA1752: {"map_kpa": 300, "injectors": "550cc 91Oct", "fpr_bar": 4.0, "maf": "stock", "turbo": "GT3071", "notes": "26psi OB/23psi, 7200rpm"},
    0x2EB58546: {"map_kpa": 300, "injectors": "550cc EV14", "fpr_bar": 5.0, "maf": "stock", "turbo": "GT2871", "notes": "26psi OB/22psi, 7200rpm, 330whp"},
    0xA77BB88E: {"map_kpa": 300, "injectors": "440cc Siemens", "fpr_bar": 5.0, "maf": "stock", "turbo": "GT2871", "notes": "26psi OB/22psi, 7200rpm, 330whp"},
    0x28C04D7B: {"map_kpa": 300, "injectors": "440cc", "fpr_bar": 5.0, "maf": "stock", "turbo": "any AAN/ABY/ADU", "notes": "RS2/ADU/ABY application"},
    0xB9F0FD51: {"map_kpa": 300, "injectors": "440cc Siemens", "fpr_bar": 4.0, "maf": "stock", "turbo": "GT3071", "notes": "26psi OB/23psi, 7200rpm, 346whp"},
    # 7A chips
    0x84B0504E: {"map_kpa": None, "injectors": "stock", "maf": "034 billet 80mm", "turbo": "NA", "notes": "Big MAF: stock MAF element in 034 billet housing"},
    0xC075767F: {"map_kpa": None, "injectors": "stock", "maf": "stock", "turbo": "NA", "notes": "Direct plug-in, no mods required"},
    0xA01C4EDA: {"map_kpa": None, "injectors": "550cc", "maf": "034 billet", "turbo": "034 T3/T4 kit", "notes": "9.5:1 compression HG required. Stage 2=250whp w/ intercooler."},
    0x55177DDB: {"map_kpa": None, "injectors": "034 injector kit", "maf": "034 billet", "turbo": "034 T3/T4 kit", "notes": "9.5:1 compression HG, ~200whp Stage 1"},
    # AAH
    0x4818FA0B: {"map_kpa": None, "injectors": "stock", "maf": "078 133 471A big bore", "turbo": "NA", "notes": "MMS-200 ECU only (8A0 906 266A). +12HP/+13ft-lb."},
    # 034EFI boost chips
    0x69156B3A: {"boost_chip": True, "map_kpa": 300, "turbo": "GT2871", "notes": "GT2871 Stage 1 boost chip. 26psi OB/22psi. Pair with GT2871 fuel chips."},
    0x39DC67DA: {"boost_chip": True, "map_kpa": 300, "turbo": "GT3071", "notes": "GT3071 Stage 1 26-23psi boost chip. Pair with GT3071 fuel chips."},
}


def get_chip_requirements(crc: int) -> Optional[dict]:
    """Return hardware requirements dict for a known chip CRC, or None."""
    return CHIP_REQUIREMENTS.get(crc)


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

# V8 557 series reset vector targets (lower 32KB)
_V8_RESET_TARGETS = {0x1497, 0x14E2, 0x14E3}

def _is_v8_split_bank(raw: bytes) -> bool:
    """
    Detect Bosch Motronic M2.3 V8 split-bank 65536B EPROM image.

    V8 557 series (ABH/S6/V8Q): lower 32KB = firmware, upper 32KB = calibration.
    Signature: lower half starts with LJMP (0x02) to a V8-specific reset target.
    5-cyl 551x doubled chips: both halves are mirrors — lower starts with firmware
    that goes to 0x1329 / 0x1297 / 0x0400, or is an identical mirror of upper.

    We detect V8 specifically by the reset target address.
    """
    if len(raw) != MAIN_CHIP_PHYSICAL:
        return False
    lower = raw[:MAIN_CHIP_WORKING]
    upper = raw[MAIN_CHIP_WORKING:]
    # Quick check: if halves are identical it's a 5-cyl doubled chip
    if lower == upper:
        return False
    # Check lower half reset vector
    if lower[0] != 0x02:
        return False
    reset_target = (lower[1] << 8) | lower[2]
    return reset_target in _V8_RESET_TARGETS


def normalize_rom(raw: bytes, variant: ROMVariant | None = None
                  ) -> tuple[bytes, list[str]]:
    """
    Normalise a raw chip read to the 32KB working half.

    For 551x (64KB doubled files): upper half is the working half (firmware + cal).
    For V8 557 (64KB split-bank): lower = firmware code, upper = calibration.
      normalize_rom returns the UPPER half — the calibration working half.
    For 3B/PT/ABT (32KB flat): returns as-is.

    V8 split-bank detection: lower half starts with LJMP to 0x1497/0x14E2 (V8
    firmware reset targets). 5-cyl chips either have identical halves (mirror)
    or a lower half that is not a valid 8051 reset vector to a V8 address.
    """
    notes = []
    size = len(raw)

    # Already a 32KB working half (flat 3B/PT or pre-extracted)
    if size == MAIN_CHIP_WORKING:
        return raw, notes

    if size == MAIN_CHIP_PHYSICAL:
        # V8 split-bank: firmware in lower, calibration in upper
        if _is_v8_split_bank(raw):
            working = raw[WORKING_HALF_OFFSET:WORKING_HALF_OFFSET + MAIN_CHIP_WORKING]
            reset = (raw[1] << 8) | raw[2]
            notes.append(
                f"V8 split-bank chip (lower reset → 0x{reset:04X}). "
                f"Upper half selected as calibration working half.")
            return working, notes

        # 5-cyl 551x doubled: upper half is always the working half
        working = raw[WORKING_HALF_OFFSET:WORKING_HALF_OFFSET + MAIN_CHIP_WORKING]
        notes.append("64KB doubled chip — upper half selected (working half @ 0x8000)")
        return working, notes

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

    # 2. ROM ID string match — survives calibration edits (firmware area unchanged)
    #    Format: "895907551B  2,2l R5 MOTR..." at WH 0x7F00
    #    HIGH if checksum valid (stock or correctly re-checksummed after edit)
    #    MEDIUM if checksum invalid (mid-edit, loaded without apply_checksum)
    if len(rom) >= 0x7F20:
        id_chunk = rom[0x7F00:0x7F20]
        id_str   = ''.join(chr(b) if 32 <= b <= 126 else '' for b in id_chunk).strip()
        for v in ALL_VARIANTS:
            for pn in (v.ecu_pns or []):
                pn_clean = pn.replace(' ', '').replace('-', '')
                if pn_clean and pn_clean in id_str.replace(' ', ''):
                    confidence = "HIGH" if cs_ok else "MEDIUM"
                    warnings = [] if cs_ok else ["Checksum invalid — ROM modified or not yet checksummed"]
                    return DetectionResult(
                        variant=v, confidence=confidence,
                        method=f"ROM ID string match — {id_str[:40].strip()}",
                        checksum_ok=cs_ok, crc32=crc, build_number=build,
                        warnings=warnings,
                    )

    # 3. Build number heuristic
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

    Real layout (confirmed from 3B, ABY, ADU direct chip reads 2026-03):
        [0x3A] [rows × RPM bytes] [misc bytes] [0x3F] [cols × load bytes] [2 bytes]

    0x3A is the RPM axis type marker. 0x3F is the load axis type marker.
    RPM bytes decoded as raw × 40 = RPM.
    Load bytes returned raw (varies by firmware — display as-is or apply /2.56 for %).
    Descriptor is always 36 bytes for 16×16 maps.
    Used by 3B/RR/ABY/ADU maps where the header immediately precedes map data.

    Returns (rpm_axis, load_axis). Falls back to sequential indices if parsing fails.
    """
    if header_addr < 0 or header_addr + 36 > len(rom):
        return list(range(rows)), list(range(cols))

    desc = rom[header_addr: header_addr + 36]
    rpm_raw: list[int] = []
    load_raw: list[int] = []

    i = 0
    while i < len(desc) - rows:
        if desc[i] == 0x3A and not rpm_raw:
            rpm_raw = list(desc[i + 1: i + 1 + rows])
            i += 1 + rows
            continue
        if desc[i] == 0x3F and not load_raw:
            load_raw = list(desc[i + 1: i + 1 + cols])
            i += 1 + cols
            continue
        i += 1

    rpm_axis  = [b * 40 for b in rpm_raw] if rpm_raw else list(range(rows))
    load_axis = load_raw if load_raw else list(range(cols))
    return rpm_axis, load_axis


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

    # Boost chip maps use hardcoded RPM × load axes
    if map_def.chip == "boost":
        if sw in ("404", "RR_B"):
            return (_BOOST_3B_RPM_AXIS[:map_def.rows],
                    _BOOST_3B_LOAD_AXIS[:map_def.cols])
        else:  # 551AA/B/C — 10x16 boost maps
            return (_BOOST_RPM_AXIS[:map_def.rows],
                    _BOOST_LOAD_AXIS[:map_def.cols])

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

    if sw in ("551C", "551AA", "551B", "551B_D02", "551A"):
        # These variants all use Bosch descriptor headers immediately before map data.
        # Dynamic axes: values are runtime-updated by the ECU (last seen operating point).
        # Fall back to known static axis if header parsing fails or map has no descriptor.
        header_addr = map_def.main_addr - 36
        rpm, load = read_axes_from_header(rom, header_addr, map_def.rows, map_def.cols)
        # Validate: if rpm values are all identical or very low, fall back to static
        # Only use static axis if the map dimensions match (16x16); otherwise use indices
        if len(set(rpm)) <= 2 or max(rpm) < 200:
            if map_def.rows == 16 and map_def.cols == 16:
                return list(_RPM_AXIS_551), list(_LOAD_AXIS_551)
            return list(range(map_def.rows)), list(range(map_def.cols))
        return rpm, load

    if sw in ("404", "404V8", "RR"):
        # Header is at data_addr - 36
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
    """Rev limit address is not confirmed for any stock 551x variant.
    For prjmod 551AA_0202, the LC/NLS hard RPM limit is at WH 0x0617 (raw × 40).
    This function returns None for all non-prjmod variants until confirmed."""
    sw = variant.software_id if variant else ""
    if sw == "551AA_0202":
        # LC/NLS hard RPM limit scalar (confirmed prjmod address)
        if 0x0617 < len(rom):
            return rom[0x0617] * 40
    return None


def write_rev_limit(rom: bytearray, variant: ROMVariant, rpm: int) -> bytearray:
    """Write rev limit scalar. Only implemented for prjmod 551AA_0202 (WH 0x0617).
    Stock 551x variants have no confirmed rev limit address — returns rom unchanged."""
    sw = variant.software_id if variant else ""
    if sw == "551AA_0202" and 0x0617 < len(rom):
        raw = max(0, min(255, rpm // 40))
        rom[0x0617] = raw
        # Mirror to lower half if full 64KB doubled ROM
        if len(rom) >= MAIN_CHIP_PHYSICAL:
            rom[MIRROR_OFFSET + 0x0617] = raw
    return rom
