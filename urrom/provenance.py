"""
urrom/provenance.py — where a map's address, axes and decode come from.

Every map UrROM shows carries an implicit chain of evidence: how the address
was found, which real chips confirmed it, what formula turns the byte into a
unit and where that formula was established.  This module makes the chain
explicit so the editor can show it on every cell (roadmap item 3).

    prov = provenance_for(map_def, variant)
    prov.lines()      -> short lines for a tooltip
    prov.summary()    -> one line for the editor header

The facts are the ones recorded in docs/ during the 2026-09 reverse-
engineering sessions; each entry names its document section so a reader can
check it.  Nothing here is guessed from the map name alone: the family and
chip decide the entry, and MapDef.confidence / notes are carried through.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Provenance:
    address_source: str            # how the address was found
    confirmed_on: list[str]        # real chips it was verified against
    decode: str                    # the formula in words
    decode_source: str             # where the formula was established
    axes: str = ""                 # where the axes come from
    doc: str = ""                  # docs/ file and section
    confidence: str = ""           # MapDef.confidence carried through
    caveat: str = ""               # anything still assumed

    def lines(self) -> list[str]:
        out = [f"Address: {self.address_source}"]
        if self.confirmed_on:
            out.append("Confirmed on: " + ", ".join(self.confirmed_on))
        if self.axes:
            out.append(f"Axes: {self.axes}")
        out.append(f"Decode: {self.decode}")
        if self.decode_source:
            out.append(f"Formula from: {self.decode_source}")
        if self.caveat:
            out.append(f"Caveat: {self.caveat}")
        if self.doc:
            out.append(f"See {self.doc}")
        return out

    def summary(self) -> str:
        parts = [self.address_source]
        if self.confirmed_on:
            parts.append("confirmed on " + ", ".join(self.confirmed_on))
        parts.append(self.decode)
        if self.caveat:
            parts.append("caveat: " + self.caveat)
        return "  ·  ".join(parts)


# ── decode descriptions per map type / family ────────────────────────────────

_IGN_DECODE = ("raw × 0.75 − 22.5 = °BTDC",
               "Bosch M2.3 standard; cross-checked 2026-09: 3B main map equals ADU maps 4–7 to "
               "rms 3 raw, and the ABY reports timing over KW1281 with formula 4, a = 75 "
               "(0.75°/count) — docs/3B_KW1281_RE.md")
_FUEL_DECODE = ("raw byte (injection-time factor; no absolute unit)",
                "no absolute unit exists in the firmware; compare chips in raw or as ratios")
_TEMP_DECODE = ("0.7 × (raw − 70) = °C",
                "ECU's own KW1281 formula 5, a = 7, b = raw + 30 (ABY E7 group 1 cell 2) — docs/3B_KW1281_RE.md")
_BOOST_TARGET_DECODE = ("raw / 255 × sensor span + offset = kPa abs (sensor selected in the Boost tab)",
                        "boost MCU compares raw target against raw ADC; the scale is the sensor's transfer "
                        "function — docs/3B_boost_chip_RE.md 'Sensor scale'")
_DUTY_DECODE = ("raw / 255 × 100 = % duty", "boost MCU PWM: on-time = value × period (docs/3B_boost_chip_RE.md)")


def _family(sw: str) -> str:
    if sw in ("404", "RR", "RR_B"):
        return "404"
    if sw in ("404V8", "404H"):
        return "404V8"
    if sw == "551AA_0202":
        return "0202"
    if sw in ("551C", "551D", "551B", "551AA"):
        return "2E17"
    if sw in ("551B_D02", "551A"):
        return "0E13"
    if sw.startswith("557"):
        return "557"
    return "?"


def provenance_for(map_def, variant) -> Provenance:
    sw = getattr(variant, "software_id", "") if variant is not None else ""
    fam = _family(sw)
    chip = getattr(map_def, "chip", "main")
    mt = getattr(map_def, "map_type", "raw")
    conf = getattr(map_def, "confidence", "")
    name = getattr(map_def, "name", "")

    # ── boost chips ─────────────────────────────────────────────────────
    if chip == "boost":
        if fam == "404":
            if mt == "boost":
                dec, src = _BOOST_TARGET_DECODE
                cav = "stock 3B/RR board sensor assumed linear 200 kPa until a logged boost reading"
            elif "Duty" in name:
                dec, src = _DUTY_DECODE; cav = ""
            else:
                dec, src = ("raw", "table role from the boost MCU disassembly"); cav = "role provisional"
            return Provenance(
                address_source="boost MCU table list at 0x1600 (X-axis, Y-axis, table triplets), traced in the 8 KB firmware",
                confirmed_on=["3B 447907404AA", "RR 857907404B", "S2 895907404"],
                decode=dec, decode_source=src,
                axes="delta tables [count][first][delta…]: X = TPS (RAM 64h), Y = Timer 2 period → rpm = 1.5e6 / value",
                doc="docs/3B_boost_chip_RE.md", confidence=conf, caveat=cav)
        # 551 boost chips
        dec, src = (_BOOST_TARGET_DECODE if mt == "boost" else
                    (_DUTY_DECODE if "Duty" in name else ("raw", "vwnut8392 RS2 Boost XDF")))
        return Provenance(
            address_source="vwnut8392's 8D0907551B RS2 Boost XDF (2013) and prj's aduboost IDA database",
            confirmed_on=["AAN 551AA boost", "ABY 551B boost", "RS2 D02 boost"],
            decode=dec, decode_source=src, axes="static 10×16 rpm × load axes from the XDF",
            doc="roms/README.md 'Map Addresses — 5-Cyl 551 Family'", confidence=conf,
            caveat="AAN/ABY stock sensor is an MPX4250A (S2Forum 65435); RS2 300 kPa")

    # ── main chips ──────────────────────────────────────────────────────
    if fam == "404":
        if mt == "ign":
            dec, src = _IGN_DECODE
        elif mt == "fuel":
            dec, src = _FUEL_DECODE
        elif "IAT" in name or "ECT" in name or "temp" in name.lower():
            dec, src = _TEMP_DECODE
        else:
            dec, src = ("raw", "")
        cav = ""
        if mt == "ign" and "Map 1" not in name and "Map 3" not in name and "Map 4" not in name:
            cav = "which of maps 2/5/6/7 runs depends on the boost-board bit 20h.2 and coding bit A0h.6 (docs 3c)"
        return Provenance(
            address_source="firmware descriptor tables (12 index tables 0x6093–0x621F, READ_MAP 0x0D92) — every map the firmware references",
            confirmed_on=["3B 447907404A", "3B 447907404AA", "RR 857907404B", "S2 895907404"],
            decode=dec, decode_source=src,
            axes="Bosch descriptor in front of the data: breakpoint = 256 − suffix sum of deltas, rpm × 40, load raw (3B 14…190)",
            doc="docs/551_calibration_descriptors_RE.md §3b–3c", confidence=conf, caveat=cav)

    if fam in ("2E17", "0E13"):
        if mt == "ign":
            dec, src = _IGN_DECODE
        elif mt == "fuel":
            dec, src = _FUEL_DECODE
        else:
            dec, src = ("raw", "")
        cav = ("selector traced on the ADU (docs 3e): coding-plug set A/B/C → main maps 2/4/6, alternates 3/5/7 under the tester flag"
               if mt == "ign" else "")
        if fam == "0E13":
            cav = (cav + "; " if cav else "") + "0x0E13 layout is byte-identical to the ADU's maps, so the ADU roles are assumed"
        src_chips = (["ADU 8A0907551C", "ABY 895907551A", "AAN 4A0907551AA"] if fam == "2E17"
                     else ["RS2 D02 8A0907551B", "AAN 4A0907551A (provisional, blank sample)"])
        return Provenance(
            address_source="firmware descriptor tables (551 pointer table 0xA800/0x8800, index tables at 0xA034…) on the split-bank chip",
            confirmed_on=src_chips, decode=dec, decode_source=src,
            axes="Bosch descriptor: rpm × 40, load 12…180 (fuel 177/206)",
            doc="docs/551_calibration_descriptors_RE.md §3, §3e", confidence=conf, caveat=cav)

    if fam == "0202":
        dec, src = ((_IGN_DECODE if mt == "ign" else _FUEL_DECODE) if mt in ("ign", "fuel")
                    else ("per PRJ XDF", "prj's m232 XDF"))
        return Provenance(
            address_source="prj's m232 XDF for the 0x0202 prjmod firmware; descriptors verified present in front of each 16×16 map",
            confirmed_on=["prj stock_AANABY (base tune)", "034EFI Rip Chip family (CRC catalogue)"],
            decode=dec, decode_source=src,
            axes="Bosch descriptor read from the file (get_axes 2026-09-09; the XDF's axis addresses do not hold axes)",
            doc="docs/AAN_3B_port_notes.md", confidence=conf,
            caveat="PRJ's 'overrun / no knock / knock level' names describe PRJMOD's use of the slots, not stock")

    if fam in ("404V8", "557"):
        return Provenance(
            address_source="PRJ MapFinder / community XDF; not yet decoded from firmware descriptors",
            confirmed_on=[], decode=(_IGN_DECODE[0] if mt == "ign" else "raw"),
            decode_source="assumed Bosch standard", axes="static or XDF axes",
            doc="roms/README.md 'Map Addresses — V8 (preliminary)'", confidence=conf,
            caveat="preliminary: verify against a known-good chip before editing")

    return Provenance(address_source="unknown", confirmed_on=[], decode="raw",
                      decode_source="", confidence=conf, caveat="variant not recognised")
