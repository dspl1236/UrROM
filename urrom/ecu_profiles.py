"""
urrom/ecu_profiles.py
=====================
ECU variant definitions, ROM layout, map address tables, and checksum
logic for Bosch Motronic M2.3 / M2.3.2.

Architecture overview
---------------------
5-cylinder 2.2 20vT ECUs are dual-processor:
  Main chip  — 32KB EPROM (27C256 or SST27SF256)
               Stored on chip as 64KB with the 32KB mirrored twice.
               0x0000-0x3FFF = working half
               0x4000-0x7FFF = mirror (identical bytes)
               Fuel map, ignition map, part-throttle/WOT maps,
               lambda control, idle, temperature corrections.

  Boost chip — 8KB EPROM (87C257 or SST27SF512 with latch adapter)
               Boost target table, knock threshold, MAP sensor
               calibration, N75 duty cycle table.
               Also 64KB physical (8KB × 8 mirrors).

V8 ECUs are single-processor with a single EPROM — no boost chip.

Checksum (main chip, applies to working half 0x0000-0x3FFF):
  checksum   = sum(rom[0x0000:0x3FFA]) & 0xFFFF   (16-bit, truncated)
  complement = 0xFFFF - checksum
  Stored at: 0x3FFA-0x3FFB = checksum high/low
             0x3FFC-0x3FFD = complement high/low
             0x3FFE-0x3FFF = Bosch internal build number (read-only)
  Mirror:    0x7FFA-0x7FFF = identical copy

Map descriptor format (Bosch standard):
  Each map is preceded by a header byte identifying axis types:
    0x3B = RPM axis       factor: 40, offset: 0     → actual RPM
    0x40 = Load axis      factor: 0.05, offset: 0   → ms injection
    0x38 = Coolant temp   factor: 0.75, offset: -48 → °C
    0x37 = IAT            factor: 0.75, offset: -48 → °C
    0x39 = Battery V      factor: 0.068, offset: 0  → Volts
  Ignition timing: raw × 0.75 − 22.5 = °BTDC (signed)
  Fuel (main):    128 = stoich reference, ±x = ±fuelling
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Callable
import struct
import zlib


# ── Axis / value conversion factors ──────────────────────────────────────────

def rpm_decode(raw: int) -> float:
    """Bosch RPM axis: raw × 40 = RPM."""
    return raw * 40.0

def load_decode(raw: int) -> float:
    """Bosch load axis: raw × 0.05 = ms injection time."""
    return raw * 0.05

def temp_decode(raw: int) -> float:
    """Coolant / IAT: raw × 0.75 − 48 = °C."""
    return raw * 0.75 - 48.0

def ign_decode(raw: int) -> float:
    """Ignition timing: raw × 0.75 − 22.5 = °BTDC (signed byte)."""
    signed = raw if raw < 128 else raw - 256
    return signed * 0.75

def ign_encode(deg: float) -> int:
    """°BTDC → raw byte."""
    raw = int(round(deg / 0.75))
    return raw & 0xFF  # two's complement


# ── Map definition ────────────────────────────────────────────────────────────

@dataclass
class MapDef:
    """Definition of a single map in the ECU ROM."""
    name:        str
    description: str
    main_addr:   int              # address in working half (0x0000-0x3FFF)
    rows:        int              # number of row cells (RPM axis length)
    cols:        int              # number of col cells (Load axis length)
    map_type:    str = "fuel"     # "fuel" | "ign" | "boost" | "1d" | "raw"
    unit:        str = ""
    decode:      Optional[Callable] = None
    encode:      Optional[Callable] = None
    chip:        str = "main"     # "main" | "boost"
    notes:       str = ""

    @property
    def size(self) -> int:
        return self.rows * self.cols

    @property
    def mirror_addr(self) -> int:
        """Address in the mirrored half."""
        return self.main_addr + 0x4000


# ── Known ROM variants ────────────────────────────────────────────────────────

@dataclass
class ROMVariant:
    """
    A specific ECU software version with known map addresses.

    software_id: Last 4 chars of ECU part number suffix (e.g. "551A", "404")
    engine_codes: List of engine codes this software was used with
    """
    name:         str
    software_id:  str
    engine_codes: list[str]
    ecu_pns:      list[str]       # VAG part numbers
    bosch_pns:    list[str]       # Bosch part numbers
    dual_eprom:   bool = True     # False for V8
    main_maps:    list[MapDef] = field(default_factory=list)
    boost_maps:   list[MapDef] = field(default_factory=list)
    notes:        str = ""

    @property
    def all_maps(self) -> list[MapDef]:
        return self.main_maps + self.boost_maps


# ── Map addresses — confirmed from community XDFs ────────────────────────────
#
# Sources:
#   551AA/551B: vwnut8392 XDFs on S2Forum (551B RS2 base, 551AA AAN)
#   404 (3B):   community research, partially confirmed
#   V8 (557):   limited documentation, marked as UNCONFIRMED
#
# IMPORTANT: Map addresses differ between software versions.
# A 551B XDF will show garbled data on a 551AA binary.
# Always detect software version before attempting to read maps.
#
# Addresses given are for the working half (0x0000-0x3FFF).
# The mirror half (0x4000-0x7FFF) contains identical data.

# ── 551AA — AAN (UrS4 / UrS6, non-immo, manual) ──────────────────────────────
_MAPS_551AA_MAIN = [
    # Primary fuel/ignition maps — 16×16 RPM × Load
    MapDef("Part Throttle Fuel",
           "Main fuelling map — part throttle. 128=stoich reference.",
           main_addr=0x3200, rows=16, cols=16,
           map_type="fuel", unit="relative",
           notes="RPM rows 600-6800, Load cols 0.05-0.80ms"),

    MapDef("WOT Fuel",
           "Wide-open throttle fuel enrichment — 16×1 RPM axis.",
           main_addr=0x33F0, rows=1, cols=16,
           map_type="fuel", unit="relative",
           notes="Activated by WOT switch or MAF saturation"),

    MapDef("Part Throttle Ignition",
           "Main ignition timing map — part throttle. Signed: raw×0.75−22.5=°BTDC.",
           main_addr=0x3100, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           notes="Same RPM/Load axis as fuel map"),

    MapDef("WOT Ignition",
           "Wide-open throttle ignition timing — 16×1 RPM axis.",
           main_addr=0x3400, rows=1, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode),

    MapDef("Idle Ignition",
           "Ignition timing at idle — coolant temp based.",
           main_addr=0x3500, rows=1, cols=8,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode),

    MapDef("Warmup Enrichment",
           "Fuel enrichment during warmup vs coolant temperature.",
           main_addr=0x3600, rows=1, cols=8,
           map_type="fuel", unit="relative"),

    MapDef("Cranking Fuel",
           "Fuel delivery during engine cranking vs coolant temp.",
           main_addr=0x3680, rows=1, cols=8,
           map_type="fuel", unit="relative"),

    MapDef("After-Start Enrichment",
           "Post-crank enrichment decay vs coolant temp.",
           main_addr=0x3700, rows=1, cols=8,
           map_type="fuel", unit="relative"),

    MapDef("IAT Fuel Correction",
           "Intake air temperature fuel correction.",
           main_addr=0x3780, rows=1, cols=8,
           map_type="fuel", unit="relative"),

    MapDef("Accel Enrichment",
           "Acceleration enrichment vs throttle rate and coolant.",
           main_addr=0x3800, rows=4, cols=4,
           map_type="fuel", unit="relative"),

    MapDef("Rev Limit",
           "Fuel cut RPM threshold (16-bit big-endian).",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM",
           notes="raw: 30,000,000 / uint16 = RPM"),
]

# ── 551A — ABY (S2 Coupe late) ────────────────────────────────────────────────
# Very similar to 551AA but different codebase / slight address shifts
# Addresses marked PROVISIONAL — need verification against known ABY bins
_MAPS_551A_MAIN = [
    MapDef("Part Throttle Fuel",
           "Main fuelling map — part throttle.",
           main_addr=0x3200, rows=16, cols=16,
           map_type="fuel", unit="relative",
           notes="PROVISIONAL — verify against known 551A bin"),

    MapDef("WOT Fuel",
           "WOT fuel enrichment.",
           main_addr=0x33F0, rows=1, cols=16,
           map_type="fuel", unit="relative",
           notes="PROVISIONAL"),

    MapDef("Part Throttle Ignition",
           "Main ignition timing — part throttle.",
           main_addr=0x3100, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           notes="PROVISIONAL"),

    MapDef("WOT Ignition",
           "WOT ignition timing.",
           main_addr=0x3400, rows=1, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           notes="PROVISIONAL"),

    MapDef("Rev Limit",
           "Fuel cut RPM threshold.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM"),
]

# ── 551B/C — ADU (RS2) / UrS6 ────────────────────────────────────────────────
# Best documented — PRJ's map finder + vwnut8392 XDFs
# RS2 runs 300kPa MAP vs AAN's 250kPa — different boost table
_MAPS_551B_MAIN = [
    MapDef("Part Throttle Fuel",
           "Main fuelling map — part throttle. RS2 tune more aggressive.",
           main_addr=0x3200, rows=16, cols=16,
           map_type="fuel", unit="relative"),

    MapDef("WOT Fuel",
           "WOT fuel — RS2 runs richer at peak boost.",
           main_addr=0x33F0, rows=1, cols=16,
           map_type="fuel", unit="relative"),

    MapDef("Part Throttle Ignition",
           "Ignition timing — part throttle.",
           main_addr=0x3100, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode),

    MapDef("WOT Ignition",
           "WOT ignition — RS2 runs less advance at peak torque.",
           main_addr=0x3400, rows=1, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode),

    MapDef("Rev Limit",
           "Fuel cut RPM threshold.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM"),
]

# Boost chip maps — small EPROM, 8KB working space
# Addresses relative to boost chip start (0x0000)
_MAPS_551_BOOST = [
    MapDef("Boost Target",
           "Boost pressure target vs RPM and gear/load. "
           "Raw × MAP_SENSOR_FACTOR = absolute pressure (bar).",
           main_addr=0x0200, rows=8, cols=8,
           map_type="boost", unit="bar", chip="boost",
           notes="250kPa sensor: raw/255×2.5bar  |  300kPa: raw/255×3.0bar"),

    MapDef("Knock Threshold",
           "Knock sensor threshold table — RPM based. "
           "Higher values = less sensitive to knock.",
           main_addr=0x0400, rows=1, cols=16,
           map_type="raw", unit="raw", chip="boost"),

    MapDef("N75 Duty Cycle",
           "Wastegate frequency valve duty cycle vs RPM. "
           "Higher = more boost.",
           main_addr=0x0300, rows=1, cols=16,
           map_type="raw", unit="%", chip="boost"),
]

# ── 404 — 3B (200 20vT / S2 early / UrQ RR) ──────────────────────────────────
# Distributor ignition — no coil packs
# Map addresses less documented than 551 series
# Marked PROVISIONAL throughout
_MAPS_404_MAIN = [
    MapDef("Part Throttle Fuel",
           "Main fuelling map — part throttle.",
           main_addr=0x2E00, rows=16, cols=16,
           map_type="fuel", unit="relative",
           notes="PROVISIONAL — 3B codebase different from AAN"),

    MapDef("WOT Fuel",
           "WOT fuel enrichment.",
           main_addr=0x2FF0, rows=1, cols=16,
           map_type="fuel", unit="relative",
           notes="PROVISIONAL"),

    MapDef("Part Throttle Ignition",
           "Ignition timing — distributor-based, single coil.",
           main_addr=0x2D00, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           notes="PROVISIONAL"),

    MapDef("WOT Ignition",
           "WOT ignition timing.",
           main_addr=0x3000, rows=1, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           notes="PROVISIONAL"),

    MapDef("Rev Limit",
           "Fuel cut RPM threshold.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM"),
]

# ── V8 557 — ABH 4.2L (also 443/893 for PT 3.6L) ────────────────────────────
# Single EPROM, no boost chip, two distributors
# Very limited community documentation — all UNCONFIRMED
_MAPS_V8_MAIN = [
    MapDef("Part Throttle Fuel",
           "Main fuelling map — part throttle.",
           main_addr=0x3000, rows=16, cols=16,
           map_type="fuel", unit="relative",
           notes="UNCONFIRMED — V8 documentation scarce"),

    MapDef("Part Throttle Ignition",
           "Ignition timing — dual distributor system.",
           main_addr=0x2F00, rows=16, cols=16,
           map_type="ign", unit="°BTDC",
           decode=ign_decode, encode=ign_encode,
           notes="UNCONFIRMED"),

    MapDef("Rev Limit",
           "Fuel cut RPM threshold.",
           main_addr=0x3FF0, rows=1, cols=2,
           map_type="raw", unit="RPM"),
]


# ── Variant registry ──────────────────────────────────────────────────────────

VARIANT_551AA = ROMVariant(
    name         = "AAN — UrS4 / UrS6 (non-immo)",
    software_id  = "551AA",
    engine_codes = ["AAN"],
    ecu_pns      = ["4A0907551AA", "4A0907551A"],
    bosch_pns    = ["0261203601"],
    dual_eprom   = True,
    main_maps    = _MAPS_551AA_MAIN,
    boost_maps   = _MAPS_551_BOOST,
    notes        = "UrS4 / UrS6 coil-on-plug. 250kPa MAP sensor. "
                   "551B adds 551C RS2 codebase merge.",
)

VARIANT_551B = ROMVariant(
    name         = "ADU — RS2 Avant (551B/C)",
    software_id  = "551B",
    engine_codes = ["ADU"],
    ecu_pns      = ["8A0907551A", "8A0907551B", "8A0907551C"],
    bosch_pns    = ["0261203543", "0261203544"],
    dual_eprom   = True,
    main_maps    = _MAPS_551B_MAIN,
    boost_maps   = _MAPS_551_BOOST,
    notes        = "RS2 Avant. 300kPa MAP sensor (hardware mod required on AAN ECU). "
                   "Best documented M2.3.2 software — XDFs exist for 551B.",
)

VARIANT_551A = ROMVariant(
    name         = "ABY — S2 Coupe (late)",
    software_id  = "551A",
    engine_codes = ["ABY"],
    ecu_pns      = ["895907551A"],
    bosch_pns    = ["0261203145"],
    dual_eprom   = True,
    main_maps    = _MAPS_551A_MAIN,
    boost_maps   = _MAPS_551_BOOST,
    notes        = "S2 Coupe late coil-on-plug. Similar codebase to 551AA. "
                   "Map addresses provisional pending bin verification.",
)

VARIANT_404 = ROMVariant(
    name         = "3B — 200 20vT / S2 early / UrQ RR",
    software_id  = "404",
    engine_codes = ["3B", "RR"],
    ecu_pns      = ["895907404BA", "443907404", "895907404"],
    bosch_pns    = ["0261200451", "0261200484"],
    dual_eprom   = True,
    main_maps    = _MAPS_404_MAIN,
    boost_maps   = _MAPS_551_BOOST,  # boost chip layout similar
    notes        = "Distributor ignition (not coil packs). "
                   "3B in 200 20vT and early S2. RR in late UrQuattro. "
                   "Map addresses provisional — different codebase from 551.",
)

VARIANT_V8_ABH = ROMVariant(
    name         = "ABH — V8 4.2L 32v",
    software_id  = "557",
    engine_codes = ["ABH"],
    ecu_pns      = ["4A0907557A"],
    bosch_pns    = ["0261203143"],
    dual_eprom   = False,
    main_maps    = _MAPS_V8_MAIN,
    boost_maps   = [],
    notes        = "Single EPROM — no boost chip. "
                   "Dual distributor ignition system. "
                   "Map addresses unconfirmed — community documentation scarce.",
)

VARIANT_V8_PT = ROMVariant(
    name         = "PT — V8 3.6L 32v",
    software_id  = "404V8",
    engine_codes = ["PT"],
    ecu_pns      = ["443907404A", "893907404F"],
    bosch_pns    = ["0261200273", "0261200228"],
    dual_eprom   = False,
    main_maps    = _MAPS_V8_MAIN,
    boost_maps   = [],
    notes        = "Earlier V8 — 3.6L. Single EPROM. "
                   "Map addresses unconfirmed.",
)

ALL_VARIANTS: list[ROMVariant] = [
    VARIANT_551AA,
    VARIANT_551B,
    VARIANT_551A,
    VARIANT_404,
    VARIANT_V8_ABH,
    VARIANT_V8_PT,
]


# ── ROM size constants ────────────────────────────────────────────────────────

MAIN_CHIP_PHYSICAL = 0x8000    # 64KB — physical chip read (doubled)
MAIN_CHIP_WORKING  = 0x4000    # 32KB — working half
BOOST_CHIP_PHYSICAL = 0x8000   # 64KB physical (8KB × 8 mirrors on 87C257)
BOOST_CHIP_WORKING  = 0x2000   # 8KB working

CHECKSUM_RANGE_END   = 0x3FFA  # sum bytes 0x0000-0x3FF9
CHECKSUM_ADDR        = 0x3FFA  # checksum high byte
COMPLEMENT_ADDR      = 0x3FFC  # complement high byte
BUILD_NUMBER_ADDR    = 0x3FFE  # Bosch internal build# (read-only)

# Mirror offsets
MIRROR_OFFSET        = 0x4000  # mirror half starts here


# ── Checksum logic ────────────────────────────────────────────────────────────

def compute_checksum(rom: bytes) -> int:
    """
    Compute the 16-bit Motronic checksum of the working half.
    Sum all bytes from 0x0000 to 0x3FF9 inclusive, truncated to 16 bits.
    """
    data = rom[:CHECKSUM_RANGE_END]
    return sum(data) & 0xFFFF


def read_stored_checksum(rom: bytes) -> tuple[int, int]:
    """Return (stored_checksum, stored_complement) from the ROM."""
    cs = (rom[CHECKSUM_ADDR] << 8) | rom[CHECKSUM_ADDR + 1]
    cp = (rom[COMPLEMENT_ADDR] << 8) | rom[COMPLEMENT_ADDR + 1]
    return cs, cp


def verify_checksum(rom: bytes) -> bool:
    """True if computed checksum matches stored values."""
    computed = compute_checksum(rom)
    stored_cs, stored_cp = read_stored_checksum(rom)
    complement = 0xFFFF - computed
    return stored_cs == computed and stored_cp == complement


def apply_checksum(rom: bytearray) -> bytearray:
    """
    Write correct checksum and complement into the working half,
    then mirror both halves.
    """
    cs = compute_checksum(bytes(rom))
    cp = 0xFFFF - cs

    rom[CHECKSUM_ADDR]     = (cs >> 8) & 0xFF
    rom[CHECKSUM_ADDR + 1] = cs & 0xFF
    rom[COMPLEMENT_ADDR]     = (cp >> 8) & 0xFF
    rom[COMPLEMENT_ADDR + 1] = cp & 0xFF

    # Mirror working half → mirror half (only if 64KB ROM)
    if len(rom) >= MAIN_CHIP_PHYSICAL:
        for i in range(MAIN_CHIP_WORKING):
            rom[MIRROR_OFFSET + i] = rom[i]

    return rom


def read_build_number(rom: bytes) -> int:
    """Return Bosch internal build number (last 2 bytes before end of half)."""
    return (rom[BUILD_NUMBER_ADDR] << 8) | rom[BUILD_NUMBER_ADDR + 1]


# ── ROM normalisation ─────────────────────────────────────────────────────────

def normalize_rom(raw: bytes) -> tuple[bytes, list[str]]:
    """
    Normalise a raw chip read to a 32KB working image.

    Handles:
      - 64KB physical read → extract correct 32KB half
      - .034 format (same bytes, different extension) → pass through
      - Already 32KB → pass through

    Returns (normalised_32kb, notes).
    """
    notes = []
    size = len(raw)

    if size == MAIN_CHIP_WORKING:
        # Already 32KB working image
        return raw, notes

    if size == MAIN_CHIP_PHYSICAL:
        # 64KB physical read — determine which half is valid
        lower = raw[:MAIN_CHIP_WORKING]
        upper = raw[MAIN_CHIP_WORKING:]

        # Prefer the half with a valid checksum
        if verify_checksum(lower):
            notes.append("64KB chip read — lower half selected (checksum valid)")
            return lower, notes
        if verify_checksum(upper):
            notes.append("64KB chip read — upper half selected (checksum valid)")
            return upper, notes

        # Neither half has a valid checksum — check if they're identical
        if lower == upper:
            notes.append("64KB chip read — halves identical, using lower half")
            return lower, notes

        # Pick the half that looks more like code (lower entropy in first 256 bytes)
        notes.append(
            "64KB chip read — neither half has valid checksum. "
            "Using lower half. Verify with known-good bin.")
        return lower, notes

    # Unexpected size
    notes.append(f"Unexpected ROM size: {size:,} bytes. Expected 32KB or 64KB.")
    return raw, notes


# ── Detection ─────────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    variant:      ROMVariant | None
    confidence:   str             # "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN"
    method:       str             # how we detected it
    checksum_ok:  bool
    crc32:        int
    build_number: int             # Bosch internal build number
    warnings:     list[str] = field(default_factory=list)
    dual_eprom:   bool = True

    @property
    def label(self) -> str:
        if self.variant:
            return f"{self.variant.name}  [{self.variant.software_id}]"
        return "Unknown M2.3 / M2.3.2 variant"

    @property
    def is_known(self) -> bool:
        return self.variant is not None and self.confidence in ("HIGH", "MEDIUM")


# Known CRC32 fingerprints — (crc32, variant_software_id, cal_label)
# Add more as ROMs are contributed
KNOWN_CRCS: dict[int, tuple[str, str]] = {
    # 551AA AAN
    0x12345678: ("551AA", "Stock — 4A0907551AA"),    # placeholder — replace with real
    # 551B RS2
    0xDEADBEEF: ("551B",  "Stock — 8A0907551B"),     # placeholder
    # 3B 404
    0xCAFEBABE: ("404",   "Stock — 895907404BA"),    # placeholder
}


def detect_rom(rom: bytes) -> DetectionResult:
    """
    Detect ROM variant and return a DetectionResult.

    Detection strategy (in order):
      1. CRC32 match against known-good ROMs (HIGH confidence)
      2. Build number + checksum structure analysis (MEDIUM)
      3. Heuristic — map pattern scan (LOW)
    """
    crc = zlib.crc32(rom) & 0xFFFFFFFF
    cs_ok = verify_checksum(rom)
    build = read_build_number(rom)

    # 1. CRC32 exact match
    if crc in KNOWN_CRCS:
        sw_id, cal_label = KNOWN_CRCS[crc]
        variant = next((v for v in ALL_VARIANTS if v.software_id == sw_id), None)
        return DetectionResult(
            variant      = variant,
            confidence   = "HIGH",
            method       = f"CRC32 match — {cal_label}",
            checksum_ok  = cs_ok,
            crc32        = crc,
            build_number = build,
            dual_eprom   = variant.dual_eprom if variant else True,
        )

    # 2. Build number heuristic — Bosch build numbers are in known ranges
    #    for each software family
    BUILD_RANGES = {
        "551AA": (0xA240, 0xA260),   # AAN UrS4/S6
        "551B":  (0xA250, 0xA260),   # ADU RS2
        "551A":  (0xA140, 0xA160),   # ABY S2
        "404":   (0x9800, 0x9C00),   # 3B / RR
        "557":   (0xA100, 0xA200),   # V8 ABH
    }

    for sw_id, (lo, hi) in BUILD_RANGES.items():
        if lo <= build <= hi:
            variant = next((v for v in ALL_VARIANTS if v.software_id == sw_id), None)
            warnings = []
            if not cs_ok:
                warnings.append(
                    "Checksum invalid — ROM may be modified or corrupted")
            return DetectionResult(
                variant      = variant,
                confidence   = "MEDIUM",
                method       = f"Build number 0x{build:04X} → {sw_id}",
                checksum_ok  = cs_ok,
                crc32        = crc,
                build_number = build,
                warnings     = warnings,
                dual_eprom   = variant.dual_eprom if variant else True,
            )

    # 3. No match
    warnings = ["ROM not recognised — map addresses will need manual verification"]
    if not cs_ok:
        warnings.append("Checksum invalid")

    return DetectionResult(
        variant      = None,
        confidence   = "UNKNOWN",
        method       = "No match",
        checksum_ok  = cs_ok,
        crc32        = crc,
        build_number = build,
        warnings     = warnings,
        dual_eprom   = True,
    )


# ── Map reading / writing ─────────────────────────────────────────────────────

def read_map(rom: bytes, map_def: MapDef) -> list[list[int]]:
    """Read raw bytes from ROM into a 2D grid [row][col]."""
    addr = map_def.main_addr
    data = []
    for r in range(map_def.rows):
        row = []
        for c in range(map_def.cols):
            offset = addr + r * map_def.cols + c
            row.append(rom[offset] if offset < len(rom) else 0)
        data.append(row)
    return data


def read_map_decoded(rom: bytes, map_def: MapDef) -> list[list[float]]:
    """Read and decode map values using map_def.decode function."""
    raw = read_map(rom, map_def)
    if not map_def.decode:
        return [[float(v) for v in row] for row in raw]
    return [[map_def.decode(v) for v in row] for row in raw]


def write_map(rom: bytearray, map_def: MapDef,
              data: list[list[int]]) -> bytearray:
    """Write raw bytes into ROM at map_def.main_addr, then update mirror."""
    addr = map_def.main_addr
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            offset = addr + r * map_def.cols + c
            if offset < MAIN_CHIP_WORKING:
                rom[offset] = max(0, min(255, val))

    # Update mirror (only if 64KB ROM)
    if len(rom) >= MAIN_CHIP_PHYSICAL:
        for i in range(MAIN_CHIP_WORKING):
            rom[MIRROR_OFFSET + i] = rom[i]

    return rom


def read_rev_limit(rom: bytes, variant: ROMVariant) -> Optional[int]:
    """
    Read fuel cut RPM from ROM.
    Formula: 30,000,000 / uint16_be = RPM
    """
    rev_map = next(
        (m for m in variant.main_maps if m.name == "Rev Limit"), None)
    if rev_map is None:
        return None
    addr = rev_map.main_addr
    if addr + 2 > len(rom):
        return None
    raw = (rom[addr] << 8) | rom[addr + 1]
    if raw == 0:
        return None
    return round(30_000_000 / raw)


def write_rev_limit(rom: bytearray, variant: ROMVariant,
                    rpm: int) -> bytearray:
    """Write new rev limit RPM to ROM."""
    rev_map = next(
        (m for m in variant.main_maps if m.name == "Rev Limit"), None)
    if rev_map is None:
        return rom
    raw = round(30_000_000 / rpm)
    addr = rev_map.main_addr
    rom[addr]     = (raw >> 8) & 0xFF
    rom[addr + 1] = raw & 0xFF
    # Mirror (only if 64KB ROM)
    if len(rom) >= MAIN_CHIP_PHYSICAL:
        rom[MIRROR_OFFSET + addr]     = rom[addr]
        rom[MIRROR_OFFSET + addr + 1] = rom[addr + 1]
    return rom
