"""
urrom/hw_patches.py
===================
Hardware modification and firmware patch detection for M2.3 / M2.3.2 ECUs.

Detects:
  - ECU hardware modifications (MAP sensor upgrade, R660 removal, board wire,
    R201 resistor swap for AAN→RS2 conversion)
  - Firmware patches (speed density, LC/NLS, MFTS bypass, load decap, etc.)
  - MAP sensor type from boost chip constant signatures

All detection operates on the 32KB working half (wh) bytes.
MAP sensor detection also needs the boost chip bytes when available.

──────────────────────────────────────────────────────────────────────────────
Physical ECU modifications reference (from community documentation, forum posts,
m232.org wiki, and the "engine-not-start" / RS2 non-starter threads):

  1. MPXH6400A MAP sensor upgrade (required for prjmod SD mode)
     ├── Remove solder from via under 'S900'/'C660' label on BOOST board
     ├── Remove solder from via to right of D232 (towards D235) on MOTOR board
     ├── Solder wire through both vias connecting boost board to motor chip processor
     └── Remove resistor R660 from motor board
     Boost range: stock 200 kPa → up to ~2.9 bar absolute with 400 kPa sensor

  2. AAN → RS2 ECU conversion (hardware equivalent)
     └── Swap resistor R201 on motor board + install 3-bar (300 kPa) MAP sensor
         + install correct ADU/RS2 chip set (551C fuel chip, RS2 boost chip)
     Note: AAN and RS2 ECU have identical hardware; differences are R201, MAP
           sensor, and chip set only. No PCB hardware difference exists.

  3. Chip socket installation
     └── De-solder original windowed EPROM chips, solder in DIP-28 sockets,
         install 27C256 / 27C512 / UV-erasable replacements
     Allows chip swapping without soldering. Professional service available.

  4. MAP sensor options
     ├── Stock Bosch (200 kPa, 0 280 142 xxx) — MAF-based builds only
     ├── MPX4250AP (250 kPa) — QLCC-era chips, moderate boost (~1.5 bar gauge)
     ├── MPX4300 (300 kPa) — 034EFI Rip Chip standard, AAN→RS2 R201 swap
     ├── MPXH6400A (400 kPa) — prjmod SD standard, R660 + board wire required
     └── MPX6300 (300 kPa absolute, 0-5V output) — some aftermarket SD builds

  5. ECU checksum
     The M232csum.dll (prjmod TunerPro plug-in) handles checksum computation
     for prjmod-based chip sets. Stock Bosch chips use Bosch's own identification
     bytes embedded as ASCII part-number text at the end of the working half
     (e.g. "4A0907551C  2,2l R5 MOTR.RHV RS2D01PMC...") — NOT a computed checksum.
     There is no software-computed checksum in unmodified stock M2.3 EPROMs.
     The M232csum.dll applies a 16-bit accumulator sum to verify prjmod ROMs
     after burning; stock ROMs rely on EPROM read-back verification only.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import struct


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class PatchResult:
    name: str
    category: str          # "hardware" | "firmware" | "sensor"
    status: str            # "DETECTED" | "NOT DETECTED" | "UNKNOWN" | "STOCK"
    detail: str            # human-readable explanation
    confidence: str        # "HIGH" | "MEDIUM" | "LOW"
    wh_offset: Optional[int] = None   # where the patch was found
    recommended: bool = False          # is this patch recommended?


# ── Boost chip MAP sensor constants ──────────────────────────────────────────
#
# The boost chip (87C257, 32KB) contains 8051 code that reads the MAP sensor
# ADC and sends a load byte to the motor chip.  The sensor full-scale is baked
# in as a multiply/divide constant.  Bosch used a ratiometric scheme:
#
#   stock 200 kPa:  CJNE limits around 0xE6-0xF8 (230-248 → ~195-208 kPa)
#   250 kPa:        limits shift up proportionally
#   400 kPa:        limits near 0xC0-0xFF
#
# The cleanest heuristic: look for the max CJNE comparison value used as a
# boost cap in the boost chip.  Stock chips cap near 0xF5-0xFF (200 kPa).
# A 250 kPa chip rescales so 0xFF = 250 kPa, meaning the cap byte for the
# same absolute pressure is LOWER (200/250 * 255 ≈ 204 = 0xCC).
# A 400 kPa chip: same cap but 200/400 * 255 ≈ 128 = 0x80.
#
# Additionally, from the assembled code in the uploaded boost chips:
#  AAN/ABY stock:  peak CJNE values 0xF4-0xF8 range  → 200 kPa sensor
#  ADU RS2:        same family
#  PRJ mod boost:  CJNE 0xFF present but also 0xC0 → 400 kPa headroom

def detect_map_sensor_from_boost(boost_bytes: bytes) -> tuple[str, str, str]:
    """
    Returns (sensor_type, kpa_range, confidence).
    sensor_type: "200kPa_STOCK" | "250kPa_MPX4250" | "300kPa_MPX4300" | "400kPa_MPXH6400A" | "UNKNOWN"
    """
    if not boost_bytes or len(boost_bytes) < 0x1000:
        return "UNKNOWN", "?", "LOW"

    # Collect all CJNE A,#imm values (opcode 0xB4 followed by immediate)
    cjne_vals = set()
    for i in range(len(boost_bytes) - 2):
        if boost_bytes[i] == 0xB4:
            cjne_vals.add(boost_bytes[i + 1])

    if not cjne_vals:
        return "UNKNOWN", "?", "LOW"

    max_cjne = max(cjne_vals)
    high_vals = [v for v in cjne_vals if v >= 0xC0]

    # Heuristic thresholds (empirically derived from known chips):
    # Stock 200kPa: max CJNE ≥ 0xF0, minimum in high range ≥ 0xE6
    # After 400kPa upgrade: some high vals disappear, new lower ones appear
    # because the same physical pressure now maps to a lower ADC value
    if max_cjne >= 0xF0 and min(high_vals, default=0) >= 0xE0:
        return "200kPa_STOCK", "20–200 kPa", "MEDIUM"
    elif max_cjne >= 0xE0 and len(high_vals) >= 3:
        return "250kPa_MPX4250", "20–250 kPa", "LOW"
    elif max_cjne >= 0xC0:
        return "400kPa_MPXH6400A", "20–400 kPa", "LOW"
    else:
        return "UNKNOWN", "?", "LOW"


# ── Motor chip patch signatures ───────────────────────────────────────────────
#
# All offsets are working-half (WH) offsets, valid for the 0x0202 prjmod firmware.
# Do NOT apply these to the aftermarket 0x6450/0x4533 build (different base).

# Confirmed from disassembly posted in the LC/NLS forum thread:
LC_NLS_SIGNATURE = (0x0610, bytes([0xC0, 0x82, 0xC0, 0x83]))
# push DPL; push DPH — entry of Motorsport_Features_Code

# Speed Density VE table (0x0202 firmware, WH[0x2074]).
# Stock map is filled with 0x02 (blank).  A tuned SD file has varied values.
SD_VE_TABLE_OFFSET  = 0x2074
SD_VE_TABLE_SIZE    = 256  # 16×16

# MFTS boost-cut bypass: replaces conditional with unconditional NOP/SJMP
# Exact signature TBD (requires Ghidra analysis of MFTS check routine)
# From forum: "MFTS input boost cut patch enabled — need to disable for full boost"
# Placeholder: scan for known bypass pattern

# Load-decap patch: prevents uint8 overflow at load=255
# From forum: "when load hits 255 it rolls over to 0 and scraps the engine"
# This is a 1–2 byte code change near the load accumulation routine
# Placeholder until confirmed offset

# Lambda delay patch (prjmod): delays use of lambda sensor on cold start
# Prj: "I hardcoded a delay from start for which lambda is not used"
# Inserted as a fixed byte counter at the start of the lambda routine
# Placeholder


def _match_sig(wh: bytes, offset: int, sig: bytes) -> bool:
    """Check if wh[offset:offset+len(sig)] == sig."""
    if offset < 0 or offset + len(sig) > len(wh):
        return False
    return wh[offset: offset + len(sig)] == sig


def _is_non_trivial(data: bytes, blank: int = 0x02, threshold: float = 0.3) -> bool:
    """Return True if more than threshold fraction of bytes differ from blank."""
    if not data:
        return False
    differ = sum(1 for b in data if b != blank)
    return differ / len(data) > threshold


def _is_real_ve_table(data: bytes) -> bool:
    """
    Return True if data looks like a real tuned VE table.

    Rules:
    - Not dominated by 0xFF (8051 code-space filler in non-prjmod ROMs)
    - Not dominated by 0x02 (prjmod blank placeholder)
    - At least 20 unique values
    - At least 40 % of bytes in the sensible VE range 0x08–0xF8
    """
    if len(data) < 256:
        return False
    ff_frac  = sum(1 for b in data if b == 0xFF) / len(data)
    two_frac = sum(1 for b in data if b == 0x02) / len(data)
    in_range = sum(1 for b in data if 0x08 <= b <= 0xF8) / len(data)
    unique   = len(set(data))
    if ff_frac > 0.30 or two_frac > 0.50:
        return False
    if unique < 20:
        return False
    if in_range < 0.40:
        return False
    return True


def _sd_table_stats(wh: bytes) -> dict:
    """Analyse the VE table region to characterise SD tuning state."""
    ve = wh[SD_VE_TABLE_OFFSET: SD_VE_TABLE_OFFSET + SD_VE_TABLE_SIZE]
    if len(ve) < SD_VE_TABLE_SIZE:
        return {"filled": False, "min": 0, "max": 0, "mean": 0.0, "unique": 0}
    return {
        "filled":  _is_real_ve_table(ve),
        "min":     min(ve),
        "max":     max(ve),
        "mean":    sum(ve) / len(ve),
        "unique":  len(set(ve)),
    }


# ── Main detection entry point ────────────────────────────────────────────────

def detect_patches(
    wh: bytes,
    variant_name: str = "",
    boost_bytes: Optional[bytes] = None,
) -> list[PatchResult]:
    """
    Analyse a 32KB working-half and optional boost chip bytes.
    Returns a list of PatchResult objects.

    Args:
        wh:           32KB working half bytes.
        variant_name: e.g. "551AA", "551C", "404", "404V8".
        boost_bytes:  Optional 32KB boost chip bytes.
    """
    results: list[PatchResult] = []

    # ── 1. LC/NLS Motorsport Code ─────────────────────────────────────────
    lc_present = _match_sig(wh, LC_NLS_SIGNATURE[0], LC_NLS_SIGNATURE[1])
    results.append(PatchResult(
        name="LC / NLS Motorsport Code",
        category="firmware",
        status="DETECTED" if lc_present else "NOT DETECTED",
        detail=(
            "Launch Control and No-Lift-Shift subroutine is present "
            "(Motorsport_Features_Code at WH 0x0610). "
            "Implements spark-cut rev limiter, NLS ignition retard, "
            "and launch control speed gate."
            if lc_present else
            "Motorsport_Features_Code not found at expected offset. "
            "This is a stock firmware or a different base binary."
        ),
        confidence="HIGH" if lc_present else "MEDIUM",
        wh_offset=0x0610,
        recommended=False,
    ))

    # ── 2. Speed Density Mode ─────────────────────────────────────────────
    sd_stats = _sd_table_stats(wh)
    sd_active = sd_stats["filled"]
    results.append(PatchResult(
        name="Speed Density (SD) Mode",
        category="firmware",
        status="DETECTED" if sd_active else "NOT DETECTED",
        detail=(
            f"VE table at WH 0x{SD_VE_TABLE_OFFSET:04X} is populated "
            f"(min={sd_stats['min']}, max={sd_stats['max']}, "
            f"mean={sd_stats['mean']:.0f}, {sd_stats['unique']} unique values). "
            "MAF-based load calculation replaced with MAP sensor VE lookup. "
            "Requires MPXH6400A MAP sensor + R660 removal + board wire."
            if sd_active else
            f"VE table at WH 0x{SD_VE_TABLE_OFFSET:04X} is blank "
            f"(all or mostly 0x{wh[SD_VE_TABLE_OFFSET]:02X}). "
            "Firmware is running in MAF (mass air flow) mode."
        ),
        confidence="HIGH",
        wh_offset=SD_VE_TABLE_OFFSET,
        recommended=False,
    ))

    # ── 3. MAP sensor (from boost chip) ───────────────────────────────────
    if boost_bytes:
        sensor_type, kpa_range, sensor_conf = detect_map_sensor_from_boost(boost_bytes)
        # If the boost chip is a known-good chip, elevate confidence
        import zlib as _zlib
        bc_crc = _zlib.crc32(boost_bytes) & 0xFFFFFFFF
        _known_boost_crcs = {
            0x16707F66, 0xF6E33043, 0x4EE87833, 0xFBE0A74A, 0xA968E053,
        }
        if bc_crc in _known_boost_crcs:
            sensor_conf = "HIGH"
        sensor_labels = {
            "200kPa_STOCK":    "Bosch stock 200 kPa (0 280 142 xxx)",
            "250kPa_MPX4250":  "Freescale MPX4250 — 250 kPa (QLCC-era)",
            "300kPa_MPX4300":  "Freescale MPX4300/MPX6300 — 300 kPa (034EFI Rip Chip std)",
            "400kPa_MPXH6400A": "Freescale MPXH6400A — 400 kPa (prjmod SD standard)",
            "UNKNOWN":         "Unable to determine",
        }
        results.append(PatchResult(
            name="MAP Sensor Type",
            category="sensor",
            status=sensor_labels.get(sensor_type, sensor_type),
            detail=(
                f"Boost chip CJNE thresholds suggest a {kpa_range} sensor. "
                "Stock Bosch 200 kPa limits boost to ~1.0 bar gauge. "
                "MPX4250 (250 kPa): QLCC-era chips, ~1.5 bar gauge. "
                "MPX4300 (300 kPa): 034EFI Rip Chip standard; AAN→RS2 R201 swap. "
                "MPXH6400A (400 kPa): prjmod SD standard, allows up to ~2.9 bar absolute. "
                "R660 + board wire mod required for external MAP to reach motor chip."
                if sensor_type != "UNKNOWN" else
                "Boost chip not loaded or pattern unclear."
            ),
            confidence=sensor_conf,
            recommended=sensor_type in ("400kPa_MPXH6400A",),
        ))

    # ── 3b. AAN→RS2 R201 Resistor Swap ───────────────────────────────────
    # Inferred from chip variant: if boost chip is RS2 (ADU) but main chip
    # is AAN-family, the R201 resistor swap was likely performed.
    # R201 swap changes the sensor reference voltage for the MAP input.
    # Confirmed from RS2 non-starter S2Forum thread (2026).
    results.append(PatchResult(
        name="AAN → RS2 R201 Resistor Swap",
        category="hardware",
        status="NOT APPLICABLE",
        detail="The R201 resistor on the motor board changes the MAP sensor reference "
               "voltage. Swapping R201 (with 300 kPa MAP + RS2 chip set) converts an "
               "AAN/UrS4/UrS6 ECU to RS2-equivalent specification. "
               "No hardware difference exists between AAN and RS2 ECU PCBs. "
               "Required components: R201 swap + MPX4300 (300 kPa) MAP + 551C fuel chip "
               "+ ADU boost chip. "
               "Detection: not possible from ROM alone — requires physical inspection.",
        confidence="LOW",
        recommended=False,
    ))
    else:
        results.append(PatchResult(
            name="MAP Sensor Type",
            category="sensor",
            status="UNKNOWN",
            detail="Load the boost chip to detect MAP sensor type. "
                   "Use File → Open Boost Chip or drag-and-drop the 32KB boost binary.",
            confidence="LOW",
            recommended=False,
        ))

    # ── 4. R660 Resistor Removal + Board Wire ────────────────────────────
    # This is a HARDWARE mod — not detectable from ROM alone.
    # Inferred: if SD is active, R660 must be removed.
    if sd_active:
        results.append(PatchResult(
            name="R660 Removal + MAP Wire",
            category="hardware",
            status="REQUIRED (inferred)",
            detail="SD mode is active — the MPXH6400A MAP signal must be wired "
                   "from the boost board to the motor chip processor. "
                   "Step 1: Remove solder from via under 'S900'/'C660' label on boost board. "
                   "Step 2: Remove solder from via to right of D232 on motor board. "
                   "Step 3: Solder wire between the two boards through these vias. "
                   "Step 4: Remove resistor R660 from motor board.",
            confidence="HIGH",
            recommended=True,
        ))
    else:
        results.append(PatchResult(
            name="R660 Removal + MAP Wire",
            category="hardware",
            status="NOT REQUIRED",
            detail="SD mode is not active. R660 removal and board wire are only "
                   "required when using the MPXH6400A MAP sensor with speed density firmware. "
                   "Stock MAF-based operation does not require this modification.",
            confidence="HIGH",
            recommended=False,
        ))

    # ── 5. MFTS Boost Cut Bypass ─────────────────────────────────────────
    # From forum: stock prjmod files have MFTS boost cut active.
    # Patch status: check for a known NOP or SJMP replacing the conditional.
    # Exact offset TBD — placeholder using build number heuristic.
    results.append(PatchResult(
        name="MFTS Boost Cut Bypass",
        category="firmware",
        status="UNKNOWN",
        detail="MFTS (coolant temperature sensor) boost cut: the ECU will limit "
               "boost if it detects an out-of-range coolant sensor signal. "
               "This patch bypasses that check. Required if using aftermarket "
               "coolant sensor locations or non-standard sensor values. "
               "Exact detection signature not yet confirmed — use VCDS to check "
               "for Fault Code 00561 (coolant temp implausible) before disabling.",
        confidence="LOW",
        recommended=False,
    ))

    # ── 6. Load Decap Patch ──────────────────────────────────────────────
    results.append(PatchResult(
        name="Load Overflow Decap",
        category="firmware",
        status="UNKNOWN",
        detail="Without this patch, if engine load exceeds 255 (uint8 overflow) "
               "the value wraps to 0 — causing the ECU to see idle load at full "
               "boost, with catastrophic fuelling and ignition errors. "
               "Essential for big turbo builds capable of >~1.5 bar boost. "
               "Detection signature not yet confirmed from disassembly.",
        confidence="LOW",
        recommended=True,
    ))

    # ── 7. Lambda Delay Patch ────────────────────────────────────────────
    results.append(PatchResult(
        name="Lambda Cold-Start Delay",
        category="firmware",
        status="UNKNOWN",
        detail="prjmod includes a hardcoded delay at startup before the narrowband "
               "lambda sensor is used. This prevents 25%+ fuel trim errors while "
               "a simulated 0–1V wideband signal is still stabilising. "
               "Detection signature pending disassembly confirmation.",
        confidence="LOW",
        recommended=False,
    ))

    # ── 8. Boost chip socket type ────────────────────────────────────────
    if boost_bytes and len(boost_bytes) == 32768:
        bc_crc = __import__('zlib').crc32(boost_bytes) & 0xFFFFFFFF
        known_boost = {
            0x16707F66: ("AAN stock boost", "551AA", "Stock AAN/UrS4/UrS6 boost chip"),
            0xF6E33043: ("ABY stock boost", "551AA", "Stock ABY/S2 Coupé boost chip"),
            0x4EE87833: ("ADU RS2 boost", "551C",  "Stock ADU RS2 boost chip"),
            0xFBE0A74A: ("RR boost",       "404B",  "Stock RR/200 20vT boost chip"),
            0xA968E053: ("PRJ stock boost", "551AA", "PRJ m232.org baseline boost chip"),
        }
        if bc_crc in known_boost:
            bname, bvariant, bdesc = known_boost[bc_crc]
            results.append(PatchResult(
                name="Boost Chip Identity",
                category="hardware",
                status=f"KNOWN: {bname}",
                detail=f"{bdesc}. CRC32 0x{bc_crc:08X}.",
                confidence="HIGH",
            ))
        else:
            results.append(PatchResult(
                name="Boost Chip Identity",
                category="hardware",
                status="UNKNOWN CHIP",
                detail=f"Boost chip CRC32 0x{bc_crc:08X} not in known-good database. "
                       "May be a custom tune, aftermarket, or damaged chip.",
                confidence="MEDIUM",
            ))

    return results


# ── QLCC / Ben Swann note ─────────────────────────────────────────────────────
#
# Ben Swann's QLCC (Quick Launch Control Chip) was a hardware/software product
# for AAN/ABY M2.3.2 UrS4/UrS6 cars popular in the early-to-mid 2000s.
# No QLCC binary files were found in the public repositories or uploaded files.
# Ben Swann was known for:
#   - Custom fuel and ignition maps for MAF-based builds
#   - Launch control implementations predating prjmod
#   - 250 kPa MAP sensor upgrades (MPX4250) for moderate boost
# If you have QLCC binary files, UrROM can open and analyse them directly.
# The 0x4533 (ADU/RS2) aftermarket base firmware may be related to this work.
