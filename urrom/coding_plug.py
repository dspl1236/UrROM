"""
Coding-plug decode for Bosch Motronic M2.3 (404) and M2.3.2 (551/557) firmware.

Every firmware in roms/ carries the same 9-byte comparison ladder

    FF DC CD A9 85 66 3D 32 1F

that bins ADC channel 4 (the coding-plug input) into nine bands.  The loop
(3B 0x363B, ADU 0x4F87) walks R3 = 8..1 and stops at the first k where
``adc + ladder[k]`` carries, i.e. ``adc >= 256 - ladder[k]``; band 0 is
"below all thresholds".  What the band then selects differs per family:

404 (3B / RR / S2), ladder at L (3B: 0x3669):
    A0h  = code[band]        code table  L+9  (9 bytes, 3B: 4C 04 14 08 00 44 40 10 20)
    9Eh  = idx[band] (+9 if 21h.0, +18 if 20h.2)   idx table L+18 (9 bytes)
           read through an identity table at L+26 -> the tester "coding number" 1..27
    A0h.6 is the bank bit used by the ignition selector (docs 3c):
    boost bit 20h.2 clear -> Ign Map 2 (A0h.6=0) / Ign Map 5 (A0h.6=1)
    boost bit 20h.2 set   -> Ign Map 6 (A0h.6=0) / Ign Map 7 (A0h.6=1)

551 / 557 (AAN / ABY / ADU / RS2 / V8), ladder at L (ADU: 0x4FB0):
    idx  = idx[band] (+3 if 21h.0)     idx table    L+9   (9 bytes)
    A2h  = coding_no[idx]              number table L+18  (6 bytes) -> tester coding number
    A4h  = code[idx]                   code table   L+24  (6 bytes)
    A4h bit 5 -> set C (Ign Maps 6/7), bit 4 -> set B (4/5), else set A (2/3)  (docs 3e)

The ADC is 8-bit against the 80C535's VAREF (5 V): 1 count = 19.6 mV.  The
plug *resistance* depends on the ECU's pull-up, which is not in the ROM, so
this module reports counts and volts and leaves ohms to a meter on the car.
"""
from __future__ import annotations

from dataclasses import dataclass, field

LADDER_SIG = bytes.fromhex("ffdccda985663d321f")
ADC_VREF = 5.0


@dataclass
class CodingBand:
    band: int
    adc_lo: int
    adc_hi: int
    code: int | None = None        # A0h (404) / A4h (551)
    coding_no: int | None = None   # 9Eh (404) / A2h (551): tester coding number
    ign_set: str = ""              # "A"/"B"/"C" (551) or "bank 0"/"bank 1" (404)
    main_map: str = ""             # the ignition map that runs in this band
    alt_map: str = ""              # 551: tester-flag alternate; 404: with boost bit set

    @property
    def v_lo(self) -> float:
        return round(self.adc_lo * ADC_VREF / 256, 2)

    @property
    def v_hi(self) -> float:
        return round((self.adc_hi + 1) * ADC_VREF / 256, 2)


@dataclass
class CodingDecode:
    structure: str                 # "404" | "551" | "single"
    ladder_addr: int
    ladder: list[int]
    bands: list[CodingBand] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def band_for_adc(self, adc: int) -> CodingBand:
        for b in self.bands:
            if b.adc_lo <= adc <= b.adc_hi:
                return b
        return self.bands[-1]

    def band_for_volts(self, volts: float) -> CodingBand:
        return self.band_for_adc(max(0, min(255, int(volts * 256 / ADC_VREF))))


def _band_edges(ladder: list[int]) -> list[tuple[int, int]]:
    """ladder[1..8] are the compare bytes; band k >= 1 starts at 256 - ladder[k]."""
    starts = [0] + [256 - ladder[k] for k in range(1, 9)]
    edges = []
    for k in range(9):
        lo = starts[k]
        hi = 255 if k == 8 else starts[k + 1] - 1
        edges.append((lo, hi))
    return edges


def find_ladder(fw: bytes) -> int:
    """Offset of the comparison ladder in a firmware image, or -1."""
    return fw.find(LADDER_SIG)


def _detect_structure(fw: bytes, L: int) -> str:
    # 404: identity table at L+26 (index 1..27 reads back itself)
    if L + 26 + 27 < len(fw) and all(fw[L + 26 + a] == a for a in range(1, 10)):
        return "404"
    # 551/557: 9 idx bytes all small, followed by 6 coding numbers and 6 codes
    idx = fw[L + 9:L + 18]
    if L + 30 <= len(fw) and idx and max(idx) <= 5:
        return "551"
    return "single"


def decode_coding_plug(fw: bytes, software_id: str = "") -> CodingDecode | None:
    """
    Decode the coding-plug band table from a firmware image (32 KB flat 404
    chip, or the lower 32 KB of a 551/557 chip).  Returns None when the
    ladder is not present.
    """
    L = find_ladder(fw)
    if L < 0:
        return None
    ladder = list(fw[L:L + 9])
    structure = _detect_structure(fw, L)
    dec = CodingDecode(structure=structure, ladder_addr=L, ladder=ladder)
    edges = _band_edges(ladder)

    if structure == "404":
        codes = fw[L + 9:L + 18]
        idx = fw[L + 18:L + 27]
        for k, (lo, hi) in enumerate(edges):
            code = codes[k]
            bank = 1 if code & 0x40 else 0
            dec.bands.append(CodingBand(
                band=k, adc_lo=lo, adc_hi=hi, code=code, coding_no=idx[k],
                ign_set=f"bank {bank} (A0h.6={bank})",
                main_map="Ign Map 5" if bank else "Ign Map 2",
                alt_map="Ign Map 7" if bank else "Ign Map 6"))
        dec.notes += [
            "A0h.6 picks the ignition bank; the boost-board status bit 20h.2 "
            "switches Map 2/5 to Map 6/7 (docs 3c).",
            "Coding number (9Eh) shown is for 21h.0 = 0 and 20h.2 = 0; +9 with "
            "21h.0, +18 with 20h.2.",
            "On the 3B chip maps 5 and 6 are identical and 2/5/6/7 differ by "
            "at most 3 deg in mid-rpm part load, so the class only trims timing.",
        ]
    elif structure == "551":
        idx = fw[L + 9:L + 18]
        nums = fw[L + 18:L + 24]
        codes = fw[L + 24:L + 30]
        sets = {0x20: ("C", "Ign Map 6", "Ign Map 7"),
                0x10: ("B", "Ign Map 4", "Ign Map 5")}
        for k, (lo, hi) in enumerate(edges):
            i = idx[k]
            code = codes[i]
            s, main, alt = sets.get(code & 0x30, ("A", "Ign Map 2", "Ign Map 3"))
            if code & 0x30 == 0x30:
                s, main, alt = "C", "Ign Map 6", "Ign Map 7"   # bit 5 tested first
            dec.bands.append(CodingBand(
                band=k, adc_lo=lo, adc_hi=hi, code=code, coding_no=nums[i],
                ign_set=s, main_map=main, alt_map=alt))
        dec.notes += [
            "A4h bit 5 -> set C, bit 4 -> set B, else set A (docs 3e). The "
            "alternate map of a set only runs under the tester flag.",
            "Coding number (A2h) shown is for 21h.0 = 0; with 21h.0 the index "
            "moves +3 into the second half of the tables.",
        ]
        if software_id and not software_id.startswith(("551", "557")):
            dec.notes.append(f"Structure inferred from bytes; variant '{software_id}' untraced.")
    else:
        codes = fw[L + 9:L + 18]
        for k, (lo, hi) in enumerate(edges):
            dec.bands.append(CodingBand(band=k, adc_lo=lo, adc_hi=hi, code=codes[k]))
        dec.notes.append("Single code table after the ladder; its bit meaning is untraced "
                         "on this firmware.")
    return dec


def format_table(dec: CodingDecode) -> str:
    """Plain-text band table for the CLI."""
    lines = [f"coding ladder @0x{dec.ladder_addr:04X}  structure {dec.structure}  "
             f"(ADC ch4, {ADC_VREF:.1f} V ref)",
             f"{'band':>4} {'ADC':>9} {'volts':>11} {'code':>5} {'no.':>4}  set / main map"]
    for b in dec.bands:
        code = f"{b.code:02X}" if b.code is not None else "--"
        no = f"{b.coding_no}" if b.coding_no is not None else "--"
        tail = f"{b.ign_set}  {b.main_map}" if b.main_map else ""
        lines.append(f"{b.band:>4} {b.adc_lo:>3}..{b.adc_hi:<3}  {b.v_lo:>4.2f}..{b.v_hi:<4.2f}  "
                     f"{code:>4} {no:>4}  {tail}")
    lines += ["  " + n for n in dec.notes]
    return "\n".join(lines)
