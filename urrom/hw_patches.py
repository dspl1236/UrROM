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
     └── Swap resistor R201 on motor board + install 300 kPa MAP sensor
         + install correct ADU/RS2 chip set (551C fuel chip, RS2 boost chip)
     R201 spec: 5.6 kΩ 1%, 1206 SMD (confirmed by PRJ, S2Forum 2013)
       - AAN stock R201 ≈ 6.15 kΩ; RS2 conversion requires 5.6 kΩ 1% 1206
     Note: AAN and RS2 ECU share identical PCB hardware. No other board
           differences exist — only R201, MAP sensor, and chip set.

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
M2.3.2 ECU ADC pin map — motor chip processor
(Source: vwnut8392, S2Forum "Added feature Patcher for PRJmod", 2018–2022)

  Motor chip ADCs:
    AN0 = TPS  — Throttle Position Sensor
    AN1 = UBAT — Battery voltage
    AN2 = IAT  — Intake Air Temperature,  ECU pin 44
    AN3 = ECT  — Engine Coolant Temperature, ECU pin 45
    AN4 = Coding Plug Pin 2, ECU pin 39  ← FREE ADC (ethanol sensor, WB logging)
    AN5 = (automatic transmission related in stock)  ← MAP sensor input in SD mode
          When R660 is removed and the boost→motor inter-board wire is added,
          the MAP sensor signal from the boost board arrives here.
    AN6 = ECU pin 46 (map switching on early ECUs) ← repurposed for WB logging
    AN7 = possibly Lambda on S600 variant

  Boost chip ADCs (AN2 and AN6 are grounded by boost processor CPU — cannot be
  activated without physically lifting/desoldering the boost chip processor):
    AN2 = tied to ground by CPU  [requires boost processor lifted to use]
    AN3 = TPS for boost code → RAM_6C
    AN4 = IAT for boost → RAM_69
    AN5 = ECT for boost → RAM_6A
    AN6 = tied to ground by CPU  [requires boost processor lifted to use]
    AN7 = Altitude sensor signal → RAM_67

──────────────────────────────────────────────────────────────────────────────
PRJmod firmware patches (from vwnut8392 patcher XDF, target: 8A0 907 551B only)

  1. Wideband input logging (pin 46 / AN6):
     Replaces MAF-related bytes in high-speed logging stream with ADC4 raw value.
     8051 patch code at free space: 90 BE 04 12 17 CF 22
     Works with Zeitronix ZT-2/ZT-3 or any 0-5V wideband controller.
     Patcher also corrects motor chip checksum. Only works on unmodified 551B.
     "Base data does not match" error = bin was already modified; hand-port instead.

  2. NLS activation via coding plug pin 1 (instead of pin 2 + 12V):
     Requires clutch pedal brake-light switch (open=up, ground=pressed).

  3. Race fuel MAP switching via ECU pin 42 (BETA, untested as of 2018):
     Grounding pin 42 switches motor chip to race fuel MAP.
     May require adding wire to ECU connector.
     Note: pin 42 also used as prjmod SD enable ground in some builds.

  4. CEL as shift light:
     CEL is a grounding output (ECU sinks). RPM threshold set ~300 RPM below shift.
     European cars may need CEL wire added to harness.

  AC idle fix (separate patch thread):
     prjmod removed AC idle stepper increase from 551B. RAM_20.6 is the AC-on flag.
     Patch restores reading of "in-drive" automatic idle map when AC is on.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class PatchResult:
    name: str
    category: str          # "hardware" | "firmware" | "sensor"
    status: str            # "DETECTED" | "NOT DETECTED" | "UNKNOWN" | "STOCK"
    detail: str            # human-readable explanation
    confidence: str        # "HIGH" | "MEDIUM" | "LOW"
    wh_offset: Optional[int] = None   # where the patch was found
    applicable: bool = False           # can this patch be applied via UI?
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

# Confirmed from 551AA ROM disassembly (0x0202 firmware):
# PUSH DPL; PUSH DPH at entry of Motorsport_Features_Code
# NOTE: originally documented as 0x0610 (forum post), confirmed at 0x062E
# via binary analysis of aan_fuel-ign_551aa.bin.
LC_NLS_SIGNATURE = (0x062E, bytes([0xC0, 0x82, 0xC0, 0x83]))

# Speed Density VE table (0x0202 firmware, WH[0x2074]).
# Stock map is filled with 0x02 (blank).  A tuned SD file has varied values.
SD_VE_TABLE_OFFSET  = 0x2074
SD_VE_TABLE_SIZE    = 256  # 16×16

# ── Patch variant compatibility ──────────────────────────────────────────────
# Each patch is only safe on firmware bases where the offsets have been
# confirmed via disassembly. Applying a patch to the wrong firmware base
# would modify arbitrary bytes with potentially catastrophic results.
MFTS_COMPATIBLE_VARIANTS  = {"551AA_0202", "551AA", "551B", "551C"}
LOAD_DECAP_COMPATIBLE     = {"551AA_0202"}  # Only confirmed on prjmod firmware
LAMBDA_DELAY_COMPATIBLE   = {"551AA_0202"}  # Only confirmed on prjmod firmware

# ── MFTS Boost Cut Bypass ────────────────────────────────────────────────────
# Confirmed from 551AA disassembly:
#   WH 0x1240–0x1292: MFTS/ECT boost cut routine
#   0x1248: JNB 01h, 0x1287  (if flag clear, skip entire check)
#   0x124B: LCALL 0x1726 with DPTR=0xBE00  (read MAP sensor, AN0)
#   0x1251: CJNE A, #0xFC   (is MAP < 252?)
#   0x1254: JC 0x1287        (if MAP < 252, skip — no boost cut)
#   0x1256: LCALL 0x1726 with DPTR=0xBE03  (read MFTS, AN3)
#   0x125C: CJNE A, #0x06   (is MFTS signal >= 6?)
#   ... if all checks fail: decrements timer, eventually clears boost enable bit
#   0x1287: "OK" path — sets timer, enables boost
#
# Patch: change JC (0x40) at 0x1254 to SJMP (0x80) — unconditional "OK"
MFTS_BYPASS_OFFSET = 0x1254
MFTS_BYPASS_STOCK  = bytes([0x40, 0x31])   # JC +0x31 (to 0x1287)
MFTS_BYPASS_PATCH  = bytes([0x80, 0x31])   # SJMP +0x31 (unconditional)

# ── Load Overflow Decap ──────────────────────────────────────────────────────
# Confirmed from 551AA disassembly:
#   WH 0x3662–0x3689: Load calculation, first path (output to XRAM 0xA008)
#   0x3662: MOV B, #0xE0    (multiplier = 224)
#   0x3665: MOV A, 2Fh      (load factor from RAM)
#   0x3667: MUL AB
#   0x3668: MOV A, B         (take high byte)
#   0x366A: ADD A, #0x10     (add offset — OVERFLOW POINT)
#   0x366C: MOV B, A
#   0x3678: CJNE A, #0xF0   (stock: compare against 240)
#   0x367D: MOV B, #0xF0    (stock: clamp to 240)
#
# Bug: ADD A, #0x10 wraps uint8 past 255. Result gets clamped to min (0x10)
# instead of max. Engine sees idle load at full boost — catastrophic.
# The second load path at 0x36D6 HAS overflow protection; this one doesn't.
#
# Patch: raise comparison and clamp from 0xF0 to 0xFF
LOAD_DECAP_PATCHES = [
    (0x3679, bytes([0xF0]), bytes([0xFF])),  # CJNE compare: 0xF0 → 0xFF
    (0x367F, bytes([0xF0]), bytes([0xFF])),  # MOV B clamp:  0xF0 → 0xFF
]

# ── Lambda Cold-Start Delay ──────────────────────────────────────────────────
# Confirmed from 551AA disassembly:
#   WH 0x5EF8–0x5F1E: Lambda sensor warmup/enable logic
#   0x5EF8: MOV 30h, #0x9F  (init warmup counter to 159 cycles)
#   0x5EFB: MOV 35h, SBUF   (reads sensor serial data)
#   0x5F00: CJNE A, #0x11   (compare reading to 17)
#   0x5F03: JC 0x5F16        (if < 17, lambda not ready)
#   Counter at RAM 30h decremented at WH 0x6ADB (DEC 30h),
#   but only when ECT (RAM 36h) >= 0x85 (engine warm).
#
# Patch: change init counter from 0x9F (159) to user-selectable value
# Default extended: 0xFF (255 cycles, ~60% longer warmup delay)
LAMBDA_DELAY_OFFSET = 0x5EFA   # immediate operand of MOV 30h, #imm
LAMBDA_DELAY_STOCK  = 0x9F     # 159 cycles
LAMBDA_DELAY_EXTENDED = 0xFF   # 255 cycles (maximum single-byte)
LAMBDA_DELAY_MINIMAL  = 0x01   # near-instant lambda enable


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

    if not boost_bytes:
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
    # Confirmed: WH 0x1254, JC 0x31 → SJMP 0x31
    mfts_current = wh[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2]
    if mfts_current == MFTS_BYPASS_PATCH:
        mfts_status, mfts_detail = "PATCHED", (
            "MFTS boost cut bypass is ACTIVE. The conditional jump at "
            f"WH 0x{MFTS_BYPASS_OFFSET:04X} has been replaced with an "
            "unconditional SJMP — ECU will never cut boost based on "
            "coolant sensor signal. Use Revert to restore stock behaviour.")
    elif mfts_current == MFTS_BYPASS_STOCK:
        mfts_status, mfts_detail = "STOCK", (
            "MFTS boost cut is ACTIVE (stock). ECU will limit boost if "
            "coolant sensor signal is out of range (MFTS < 6 on AN3). "
            "Apply this patch to bypass the check — required if using "
            "aftermarket coolant sensor or non-standard MFTS location.")
    else:
        mfts_status, mfts_detail = "UNKNOWN", (
            f"Unexpected bytes at WH 0x{MFTS_BYPASS_OFFSET:04X}: "
            f"{mfts_current.hex()}. Expected stock {MFTS_BYPASS_STOCK.hex()} "
            f"or patched {MFTS_BYPASS_PATCH.hex()}. May be a different firmware base.")
    mfts_applicable = variant_name in MFTS_COMPATIBLE_VARIANTS
    if not mfts_applicable and mfts_status != "UNKNOWN":
        mfts_detail += (f" NOTE: Patch offsets only confirmed for "
                        f"{', '.join(sorted(MFTS_COMPATIBLE_VARIANTS))}. "
                        f"Current variant '{variant_name}' is not in the compatibility list.")
    results.append(PatchResult(
        name="MFTS Boost Cut Bypass",
        category="firmware",
        status=mfts_status,
        detail=mfts_detail,
        confidence="HIGH" if mfts_status != "UNKNOWN" else "LOW",
        wh_offset=MFTS_BYPASS_OFFSET,
        recommended=mfts_status == "STOCK",
        applicable=mfts_applicable,
    ))

    # ── 6. Load Overflow Decap ───────────────────────────────────────────
    # Confirmed: WH 0x3679 compare byte + WH 0x367F clamp byte
    cmp_byte = wh[LOAD_DECAP_PATCHES[0][0]] if len(wh) > LOAD_DECAP_PATCHES[0][0] else 0
    clamp_byte = wh[LOAD_DECAP_PATCHES[1][0]] if len(wh) > LOAD_DECAP_PATCHES[1][0] else 0
    if cmp_byte == 0xFF and clamp_byte == 0xFF:
        load_status, load_detail = "PATCHED", (
            "Load overflow decap is ACTIVE. Comparison and clamp raised to 0xFF. "
            "Load value will saturate at 255 instead of wrapping to 0 at high boost.")
    elif cmp_byte == 0xF0 and clamp_byte == 0xF0:
        load_status, load_detail = "STOCK", (
            "Load overflow decap is NOT applied (stock). Load value is clamped at 240 "
            "(0xF0). At high boost (>~1.5 bar), ADD A, #0x10 at WH 0x366A can wrap "
            "past 255 → load drops to minimum → catastrophic lean/timing condition. "
            "STRONGLY RECOMMENDED for any build capable of >1.5 bar boost.")
    else:
        load_status, load_detail = "MODIFIED", (
            f"Load compare=0x{cmp_byte:02X}, clamp=0x{clamp_byte:02X}. "
            "Neither stock (0xF0) nor standard patch (0xFF). Custom modification.")
    load_applicable = variant_name in LOAD_DECAP_COMPATIBLE
    if not load_applicable:
        load_detail += (f" NOTE: Patch offsets only confirmed for "
                        f"{', '.join(sorted(LOAD_DECAP_COMPATIBLE))}. "
                        f"Current variant '{variant_name}' is not in the compatibility list.")
    results.append(PatchResult(
        name="Load Overflow Decap",
        category="firmware",
        status=load_status,
        detail=load_detail,
        confidence="HIGH" if load_status != "MODIFIED" else "MEDIUM",
        wh_offset=LOAD_DECAP_PATCHES[0][0],
        recommended=load_status == "STOCK",
        applicable=load_applicable,
    ))

    # ── 7. Lambda Cold-Start Delay ───────────────────────────────────────
    # Confirmed: WH 0x5EFA, immediate operand of MOV 30h, #imm
    lambda_val = wh[LAMBDA_DELAY_OFFSET] if len(wh) > LAMBDA_DELAY_OFFSET else 0
    if lambda_val == LAMBDA_DELAY_STOCK:
        lam_status, lam_detail = "STOCK", (
            f"Lambda warmup counter = 0x{lambda_val:02X} ({lambda_val} cycles). "
            "This is the stock prjmod value. Lambda sensor is disabled for ~159 "
            "engine cycles after cold start to allow wideband signal to stabilise.")
    elif lambda_val == LAMBDA_DELAY_EXTENDED:
        lam_status, lam_detail = "EXTENDED", (
            f"Lambda warmup counter = 0x{lambda_val:02X} ({lambda_val} cycles). "
            "Extended delay — maximum single-byte value. Lambda disabled for ~255 "
            "engine cycles after cold start.")
    elif lambda_val == LAMBDA_DELAY_MINIMAL:
        lam_status, lam_detail = "MINIMAL", (
            f"Lambda warmup counter = 0x{lambda_val:02X} ({lambda_val} cycle). "
            "Near-instant lambda enable. Only safe if wideband controller has "
            "fast warmup (e.g. Bosch LSU 4.9 with internal heater).")
    else:
        lam_status, lam_detail = "CUSTOM", (
            f"Lambda warmup counter = 0x{lambda_val:02X} ({lambda_val} cycles). "
            f"Custom value (stock=0x{LAMBDA_DELAY_STOCK:02X}, "
            f"extended=0x{LAMBDA_DELAY_EXTENDED:02X}).")
    lam_applicable = variant_name in LAMBDA_DELAY_COMPATIBLE
    if not lam_applicable:
        lam_detail += (f" NOTE: Patch offsets only confirmed for "
                       f"{', '.join(sorted(LAMBDA_DELAY_COMPATIBLE))}. "
                       f"Current variant '{variant_name}' is not in the compatibility list.")
    results.append(PatchResult(
        name="Lambda Cold-Start Delay",
        category="firmware",
        status=lam_status,
        detail=lam_detail,
        confidence="HIGH",
        wh_offset=LAMBDA_DELAY_OFFSET,
        recommended=False,
        applicable=lam_applicable,
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


# ── Apply / revert functions ──────────────────────────────────────────────────
#
# Each function operates on the full ROM bytearray (32KB or 64KB).
# For 64KB doubled ROMs, patches are applied to both halves.
# Returns (modified_rom, original_bytes) for revert support.


def _write_wh(rom: bytearray, offset: int, data: bytes) -> None:
    """Write bytes at working-half offset, mirroring to upper half if doubled."""
    rom[offset: offset + len(data)] = data
    if len(rom) >= 0x10000:
        rom[offset + 0x8000: offset + 0x8000 + len(data)] = data


def apply_mfts_bypass(rom: bytearray) -> bytes:
    """Apply MFTS boost cut bypass. Returns original bytes for revert."""
    original = bytes(rom[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2])
    _write_wh(rom, MFTS_BYPASS_OFFSET, MFTS_BYPASS_PATCH)
    return original


def revert_mfts_bypass(rom: bytearray) -> None:
    """Revert MFTS boost cut bypass to stock."""
    _write_wh(rom, MFTS_BYPASS_OFFSET, MFTS_BYPASS_STOCK)


def apply_load_decap(rom: bytearray) -> list[bytes]:
    """Apply load overflow decap. Returns list of original bytes for revert."""
    originals = []
    for offset, stock, patch in LOAD_DECAP_PATCHES:
        originals.append(bytes(rom[offset: offset + len(patch)]))
        _write_wh(rom, offset, patch)
    return originals


def revert_load_decap(rom: bytearray) -> None:
    """Revert load overflow decap to stock."""
    for offset, stock, _patch in LOAD_DECAP_PATCHES:
        _write_wh(rom, offset, stock)


def apply_lambda_delay(rom: bytearray, value: int = LAMBDA_DELAY_EXTENDED) -> int:
    """Set lambda cold-start delay counter. Returns original value for revert."""
    original = rom[LAMBDA_DELAY_OFFSET]
    _write_wh(rom, LAMBDA_DELAY_OFFSET, bytes([value & 0xFF]))
    return original


def revert_lambda_delay(rom: bytearray) -> None:
    """Revert lambda delay to stock value."""
    _write_wh(rom, LAMBDA_DELAY_OFFSET, bytes([LAMBDA_DELAY_STOCK]))


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
