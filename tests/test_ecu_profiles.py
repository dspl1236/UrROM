"""
UrROM — unit tests for ecu_profiles.py

Tests the ROM normalisation, checksum logic, detection heuristics,
map read/write and rev limit encoding without needing any real ROMs.
"""

import sys
import struct
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from urrom.ecu_profiles import (
    # constants
    MAIN_CHIP_WORKING, MAIN_CHIP_PHYSICAL, MIRROR_OFFSET,
    CHECKSUM_ADDR, COMPLEMENT_ADDR, BUILD_NUMBER_ADDR,
    CHECKSUM_RANGE_END,
    BOOST_CHIP_WORKING,
    # functions
    compute_checksum, verify_checksum, apply_checksum,
    read_stored_checksum, read_build_number,
    normalize_rom,
    detect_rom,
    read_map, read_map_decoded, write_map,
    read_rev_limit, write_rev_limit,
    ign_decode, ign_encode,
    # data
    ALL_VARIANTS, VARIANT_551AA, VARIANT_551B,
    VARIANT_551A, VARIANT_404, VARIANT_V8_ABH,
    DetectionResult,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_rom(size=MAIN_CHIP_WORKING, fill=0x00) -> bytearray:
    """Create a blank ROM of given size."""
    return bytearray([fill] * size)


def _make_valid_rom(build_number=0xA245) -> bytearray:
    """Create a 32KB ROM with a valid checksum and given build number."""
    rom = _make_rom()
    # Write build number
    rom[BUILD_NUMBER_ADDR]     = (build_number >> 8) & 0xFF
    rom[BUILD_NUMBER_ADDR + 1] = build_number & 0xFF
    # Apply checksum
    rom = apply_checksum(rom)
    return rom


def _make_doubled_rom(build_number=0xA245) -> bytearray:
    """Create a 64KB doubled ROM (working half mirrored)."""
    working = _make_valid_rom(build_number)
    return bytearray(working) + bytearray(working)


# ── Checksum tests ────────────────────────────────────────────────────────────

class TestChecksum:

    def test_blank_rom_checksum_is_zero(self):
        rom = _make_rom()
        cs = compute_checksum(bytes(rom))
        assert cs == 0

    def test_apply_checksum_produces_valid_rom(self):
        rom = _make_rom()
        rom = apply_checksum(rom)
        assert verify_checksum(bytes(rom))

    def test_complement_is_correct(self):
        rom = _make_valid_rom()
        cs, cp = read_stored_checksum(bytes(rom))
        assert cs + cp == 0xFFFF

    def test_modified_rom_fails_checksum(self):
        rom = _make_valid_rom()
        # Flip a byte in the middle of the data area
        rom[0x1000] ^= 0xFF
        assert not verify_checksum(bytes(rom))

    def test_apply_checksum_mirrors_to_upper_half(self):
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        rom = apply_checksum(rom)
        working = rom[:MAIN_CHIP_WORKING]
        mirror  = rom[MIRROR_OFFSET:]
        assert bytes(working) == bytes(mirror)

    def test_checksum_stored_at_correct_address(self):
        rom = _make_rom()
        # Put a known byte pattern so checksum is predictable
        rom[0x0000] = 0x42
        rom = apply_checksum(rom)
        expected_cs = 0x42  # sum of one 0x42 byte
        stored_cs, _ = read_stored_checksum(bytes(rom))
        assert stored_cs == expected_cs

    def test_read_build_number(self):
        build = 0xA253
        rom = _make_valid_rom(build_number=build)
        assert read_build_number(bytes(rom)) == build


# ── Normalisation tests ───────────────────────────────────────────────────────

class TestNormalize:

    def test_32kb_passthrough(self):
        rom = bytes(_make_valid_rom())
        result, notes = normalize_rom(rom)
        assert len(result) == MAIN_CHIP_WORKING
        assert result == rom

    def test_64kb_lower_half_selected_when_valid(self):
        working = bytes(_make_valid_rom(0xA245))
        other   = bytes(_make_rom(fill=0xFF))
        doubled = working + other
        result, notes = normalize_rom(doubled)
        assert len(result) == MAIN_CHIP_WORKING
        assert result == working
        assert any("lower" in n.lower() for n in notes)

    def test_64kb_upper_half_selected_when_lower_invalid(self):
        bad    = bytes(_make_rom(fill=0xAA))  # no valid checksum
        valid  = bytes(_make_valid_rom(0xA245))
        doubled = bad + valid
        result, notes = normalize_rom(doubled)
        assert result == valid
        assert any("upper" in n.lower() for n in notes)

    def test_64kb_identical_halves_uses_lower(self):
        working = bytes(_make_valid_rom())
        doubled = working + working
        result, notes = normalize_rom(doubled)
        assert result == working

    def test_unexpected_size_returns_with_note(self):
        weird = bytes(0x6000)  # 24KB — not a valid ROM size
        result, notes = normalize_rom(weird)
        assert any("unexpected" in n.lower() for n in notes)


# ── Detection tests ───────────────────────────────────────────────────────────

class TestDetect:

    def test_unknown_rom_returns_unknown(self):
        rom = bytes(_make_valid_rom(build_number=0x1234))
        result = detect_rom(rom)
        assert result.confidence == "UNKNOWN"
        assert result.variant is None

    def test_551aa_build_range_detected(self):
        # Build number in AAN range
        rom = bytes(_make_valid_rom(build_number=0xA248))
        result = detect_rom(rom)
        assert result.confidence in ("MEDIUM", "HIGH")
        if result.variant:
            assert result.variant.software_id == "551AA"

    def test_551b_build_range_detected(self):
        rom = bytes(_make_valid_rom(build_number=0xA253))
        result = detect_rom(rom)
        assert result.confidence in ("MEDIUM", "HIGH")

    def test_checksum_ok_reported(self):
        rom = bytes(_make_valid_rom())
        result = detect_rom(rom)
        assert result.checksum_ok is True

    def test_bad_checksum_reported(self):
        rom = bytearray(_make_valid_rom())
        rom[0x0100] ^= 0xFF   # corrupt a data byte
        result = detect_rom(bytes(rom))
        assert result.checksum_ok is False

    def test_crc32_populated(self):
        rom = bytes(_make_valid_rom())
        result = detect_rom(rom)
        assert result.crc32 != 0

    def test_build_number_populated(self):
        build = 0xA247
        rom = bytes(_make_valid_rom(build_number=build))
        result = detect_rom(rom)
        assert result.build_number == build

    def test_label_when_unknown(self):
        rom = bytes(_make_valid_rom(build_number=0x1234))
        result = detect_rom(rom)
        assert "Unknown" in result.label


# ── Map read / write tests ────────────────────────────────────────────────────

class TestMapReadWrite:

    def _rom_with_map(self, addr, rows, cols, fill=0x80):
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        for r in range(rows):
            for c in range(cols):
                rom[addr + r * cols + c] = fill
        return bytes(rom)

    def test_read_map_correct_size(self):
        from urrom.ecu_profiles import VARIANT_551AA
        map_def = VARIANT_551AA.main_maps[0]  # Part throttle fuel
        rom = self._rom_with_map(map_def.main_addr, map_def.rows, map_def.cols)
        data = read_map(rom, map_def)
        assert len(data) == map_def.rows
        assert len(data[0]) == map_def.cols

    def test_read_map_values_correct(self):
        map_def = VARIANT_551AA.main_maps[0]
        fill = 0x7F
        rom = self._rom_with_map(map_def.main_addr, map_def.rows, map_def.cols, fill)
        data = read_map(rom, map_def)
        assert data[0][0] == fill
        assert data[-1][-1] == fill

    def test_write_map_roundtrip(self):
        map_def = VARIANT_551AA.main_maps[0]
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        # Build a 16×16 grid with known values
        grid = [[r * 16 + c for c in range(map_def.cols)]
                for r in range(map_def.rows)]
        rom = write_map(rom, map_def, grid)
        read_back = read_map(bytes(rom), map_def)
        assert read_back == grid

    def test_write_map_mirrors_to_upper_half(self):
        map_def = VARIANT_551AA.main_maps[0]
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        grid = [[0xAB] * map_def.cols for _ in range(map_def.rows)]
        rom = write_map(rom, map_def, grid)
        # Check mirror
        addr = map_def.main_addr
        assert rom[MIRROR_OFFSET + addr] == rom[addr]

    def test_write_map_clamps_to_byte(self):
        map_def = VARIANT_551AA.main_maps[0]
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        grid = [[999] * map_def.cols for _ in range(map_def.rows)]
        rom = write_map(rom, map_def, grid)
        data = read_map(bytes(rom), map_def)
        assert data[0][0] == 255

    def test_read_map_decoded_ignition(self):
        map_def = next(m for m in VARIANT_551AA.main_maps
                       if m.map_type == "ign" and m.rows == 16)
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        # Write raw 14 (= 14×0.75−22.5 = −12° ... wait let me recalc)
        # ign_decode: signed × 0.75, where signed = raw if <128 else raw-256
        # raw=14 → signed=14 → 14×0.75=10.5°BTDC
        rom[map_def.main_addr] = 14
        data = read_map_decoded(bytes(rom), map_def)
        assert abs(data[0][0] - 10.5) < 0.01


# ── Ignition encode/decode ────────────────────────────────────────────────────

class TestIgnCoder:

    def test_positive_advance(self):
        # 18°BTDC — typical idle advance
        raw = ign_encode(18.0)
        back = ign_decode(raw)
        assert abs(back - 18.0) < 0.8  # within one step

    def test_negative_retard(self):
        # −5° retard (knock retard scenario)
        raw = ign_encode(-5.0)
        assert raw > 128  # negative = two's complement high byte
        back = ign_decode(raw)
        assert abs(back - (-5.0)) < 0.8

    def test_zero_degrees(self):
        raw = ign_encode(0.0)
        back = ign_decode(raw)
        assert abs(back) < 0.8

    def test_max_advance(self):
        raw = ign_encode(40.0)
        back = ign_decode(raw)
        assert abs(back - 40.0) < 0.8


# ── Rev limit tests ───────────────────────────────────────────────────────────

class TestRevLimit:

    def _rom_with_rev(self, variant, rpm):
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        return write_rev_limit(rom, variant, rpm)

    def test_write_read_roundtrip(self):
        rom = self._rom_with_rev(VARIANT_551AA, 7000)
        result = read_rev_limit(bytes(rom), VARIANT_551AA)
        # Formula: 30,000,000 / round(30,000,000/7000) — small rounding
        assert result is not None
        assert abs(result - 7000) < 50

    def test_6500_rpm(self):
        rom = self._rom_with_rev(VARIANT_551AA, 6500)
        result = read_rev_limit(bytes(rom), VARIANT_551AA)
        assert result is not None
        assert abs(result - 6500) < 50

    def test_formula_correct(self):
        # Stock AAN rev limit is ~6800 RPM
        # 30,000,000 / 6800 = 4411.76 → round = 4412
        # 30,000,000 / 4412 = 6799.6 → 6800 RPM
        import struct
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        raw = round(30_000_000 / 6800)
        rev_map = next(m for m in VARIANT_551AA.main_maps if m.name == "Rev Limit")
        addr = rev_map.main_addr
        rom[addr]     = (raw >> 8) & 0xFF
        rom[addr + 1] = raw & 0xFF
        result = read_rev_limit(bytes(rom), VARIANT_551AA)
        assert abs(result - 6800) < 50


# ── Variant registry tests ────────────────────────────────────────────────────

class TestVariantRegistry:

    def test_all_variants_present(self):
        assert len(ALL_VARIANTS) >= 5

    def test_551aa_has_dual_eprom(self):
        assert VARIANT_551AA.dual_eprom is True

    def test_v8_has_no_dual_eprom(self):
        assert VARIANT_V8_ABH.dual_eprom is False

    def test_551b_has_boost_maps(self):
        assert len(VARIANT_551B.boost_maps) > 0

    def test_all_variants_have_main_maps(self):
        for v in ALL_VARIANTS:
            assert len(v.main_maps) > 0, f"{v.name} has no main maps"

    def test_all_maps_have_valid_addresses(self):
        for v in ALL_VARIANTS:
            for m in v.main_maps:
                assert 0 <= m.main_addr < MAIN_CHIP_WORKING, \
                    f"{v.name}/{m.name} addr 0x{m.main_addr:04X} out of range"
            for m in v.boost_maps:
                assert 0 <= m.main_addr < BOOST_CHIP_WORKING, \
                    f"{v.name}/{m.name} boost addr 0x{m.main_addr:04X} out of range"

    def test_map_size_within_rom(self):
        for v in ALL_VARIANTS:
            for m in v.main_maps:
                end = m.main_addr + m.size
                assert end <= MAIN_CHIP_WORKING, \
                    f"{v.name}/{m.name} extends past ROM end: 0x{end:04X}"

    def test_engine_codes_not_empty(self):
        for v in ALL_VARIANTS:
            assert len(v.engine_codes) > 0

    def test_ecu_pns_not_empty(self):
        for v in ALL_VARIANTS:
            assert len(v.ecu_pns) > 0
