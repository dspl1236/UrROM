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
    MAIN_CHIP_WORKING, MAIN_CHIP_PHYSICAL, MIRROR_OFFSET, FLAT_32K,
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
    get_axes, read_axes_from_header,
    _RPM_AXIS_551, _LOAD_AXIS_551,
    # data
    ALL_VARIANTS, VARIANT_551AA, VARIANT_551C,
    VARIANT_404, VARIANT_V8_ABH, VARIANT_V8_PT,
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
        working = bytes(rom[:MAIN_CHIP_WORKING])
        mirror  = bytes(rom[MIRROR_OFFSET:MIRROR_OFFSET + MAIN_CHIP_WORKING])
        assert working == mirror

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

    def test_64kb_upper_half_selected_for_551x(self):
        """551x: 64KB file layout. normalize_rom extracts bytes 0x8000-0xFFFF."""
        # Build a 64KB file: 0x8000 bytes lower half (fill FF) + 0x8000 bytes working half
        lower_pad   = bytes(_make_rom(size=0x8000, fill=0xFF))
        valid_upper = bytes(_make_valid_rom())         # 0x8000 bytes (MAIN_CHIP_WORKING)
        full_64k    = lower_pad + valid_upper
        assert len(full_64k) == MAIN_CHIP_PHYSICAL
        result, notes = normalize_rom(full_64k, VARIANT_551C)
        assert len(result) == MAIN_CHIP_WORKING
        assert result == valid_upper

    def test_64kb_upper_selected_when_lower_invalid(self):
        lower_pad = bytes(_make_rom(size=0x8000, fill=0xAA))
        valid     = bytes(_make_valid_rom())           # 0x8000 bytes
        full_64k  = lower_pad + valid
        assert len(full_64k) == MAIN_CHIP_PHYSICAL
        result, notes = normalize_rom(full_64k, VARIANT_551C)
        assert result == valid

    def test_64kb_identical_halves_uses_upper(self):
        working  = bytes(_make_valid_rom())            # 0x8000 bytes
        lower    = bytes(_make_rom(size=0x8000, fill=0x00))
        full_64k = lower + working
        assert len(full_64k) == MAIN_CHIP_PHYSICAL
        result, notes = normalize_rom(full_64k, VARIANT_551C)
        assert result == working

    def test_unexpected_size_returns_with_note(self):
        weird = bytes(0x6000)  # 24KB — not a valid ROM size
        result, notes = normalize_rom(weird)
        assert any("unexpected" in n.lower() for n in notes)


# ── Detection tests ───────────────────────────────────────────────────────────

class TestDetect:

    def test_unknown_rom_returns_unknown(self):
        # Use a build number outside all defined ranges (0xD000 is in the gap between 404V8 and 404)
        rom = bytes(_make_valid_rom(build_number=0xD000))
        result = detect_rom(rom)
        assert result.confidence == "UNKNOWN"
        assert result.variant is None

    def test_551aa_build_range_detected(self):
        # 551AA build range covers 0x0000-0x6FFF (AAN + ABY builds)
        rom = bytes(_make_valid_rom(build_number=0x5000))
        result = detect_rom(rom)
        assert result.confidence in ("MEDIUM", "HIGH")

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
        rom = bytes(_make_valid_rom(build_number=0xD000))
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
        # raw=49 → 49*0.6491 − 8.2186 = 23.59°BTDC (verified against real RS2 ROM)
        rom[map_def.main_addr] = 49
        data = read_map_decoded(bytes(rom), map_def)
        assert abs(data[0][0] - 23.59) < 0.1


# ── Ignition encode/decode ────────────────────────────────────────────────────

class TestIgnCoder:

    def test_positive_advance(self):
        # 18°BTDC — typical idle advance
        raw = ign_encode(18.0)
        back = ign_decode(raw)
        assert abs(back - 18.0) < 0.8  # within one step

    def test_negative_retard(self):
        # −5° retard (knock retard scenario)
        # New formula: raw = round((-5 + 8.2186) / 0.6491) = 5
        # Range check: minimum representable = 0*0.6491-8.2186 ≈ -8.2° BTDC
        raw = ign_encode(-5.0)
        assert 0 <= raw <= 255    # unsigned byte, no two's complement
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
        assert len(VARIANT_551C.boost_maps) > 0

    def test_all_variants_have_main_maps(self):
        for v in ALL_VARIANTS:
            assert len(v.main_maps) > 0, f"{v.name} has no main maps"

    def test_all_maps_have_valid_addresses(self):
        for v in ALL_VARIANTS:
            rom_size = FLAT_32K if v.working_half_offset == 0 else MAIN_CHIP_WORKING
            for m in v.main_maps:
                assert 0 <= m.main_addr < rom_size, \
                    f"{v.name}/{m.name} addr 0x{m.main_addr:04X} out of range (rom_size=0x{rom_size:04X})"
            for m in v.boost_maps:
                assert 0 <= m.main_addr < BOOST_CHIP_WORKING, \
                    f"{v.name}/{m.name} boost addr 0x{m.main_addr:04X} out of range"

    def test_map_size_within_rom(self):
        for v in ALL_VARIANTS:
            # 3B/V8 use flat 32KB files, others use 32KB working half
            rom_size = FLAT_32K if v.working_half_offset == 0 else MAIN_CHIP_WORKING
            for m in v.main_maps:
                end = m.main_addr + m.size
                assert end <= rom_size, \
                    f"{v.name}/{m.name} extends past ROM end: 0x{end:04X}"

    def test_engine_codes_not_empty(self):
        for v in ALL_VARIANTS:
            assert len(v.engine_codes) > 0

    def test_ecu_pns_not_empty(self):
        for v in ALL_VARIANTS:
            assert len(v.ecu_pns) > 0


# ── Axis helper tests ─────────────────────────────────────────────────────────

class TestGetAxes:

    def test_551c_returns_known_constants(self):
        rom = bytes(MAIN_CHIP_WORKING)
        m = VARIANT_551C.main_maps[0]
        rpm, load = get_axes(rom, m, VARIANT_551C)
        assert rpm  == _RPM_AXIS_551
        assert load == _LOAD_AXIS_551

    def test_551aa_returns_known_constants(self):
        rom = bytes(MAIN_CHIP_WORKING)
        m = VARIANT_551AA.main_maps[0]
        rpm, load = get_axes(rom, m, VARIANT_551AA)
        assert rpm  == _RPM_AXIS_551
        assert load == _LOAD_AXIS_551

    def test_404_reads_from_header(self):
        # Build a synthetic 32KB ROM with a Bosch descriptor at the expected address.
        # Ign Map 1 has main_addr = 0x7052 + 36 = 0x7076.
        # Header is at 0x7076 - 36 = 0x7052.
        ign_map = next(m for m in VARIANT_404.main_maps if m.map_type == "ign")
        header_addr = ign_map.main_addr - 36
        rom = bytearray(FLAT_32K)
        # Write a minimal descriptor: [type 0x3A][count 16][16 rpm bytes][type 0x3F][count 16][16 load bytes]
        rpm_vals  = list(range(10, 26))   # 10-25
        load_vals = list(range(20, 36))   # 20-35
        rom[header_addr]     = 0x3A
        rom[header_addr + 1] = 16
        rom[header_addr + 2: header_addr + 18] = bytes(rpm_vals)
        rom[header_addr + 18] = 0x3F
        rom[header_addr + 19] = 16
        rom[header_addr + 20: header_addr + 36] = bytes(load_vals)
        rpm_out, load_out = get_axes(bytes(rom), ign_map, VARIANT_404)
        assert rpm_out  == rpm_vals
        assert load_out == load_vals

    def test_unknown_variant_returns_indices(self):
        # Build a minimal unknown variant
        from urrom.ecu_profiles import ROMVariant, MapDef
        dummy_v = ROMVariant(
            name="dummy", software_id="???",
            engine_codes=["X"], ecu_pns=["0"], bosch_pns=["0"],
            working_half_offset=0,
        )
        dummy_m = MapDef("test", "", 0x100, 8, 8)
        rom = bytes(FLAT_32K)
        rpm, load = get_axes(rom, dummy_m, dummy_v)
        assert rpm  == list(range(8))
        assert load == list(range(8))

    def test_read_axes_from_header_out_of_bounds_returns_indices(self):
        rom = bytes(10)  # tiny ROM
        rpm, load = read_axes_from_header(rom, header_addr=5, rows=16, cols=16)
        assert rpm  == list(range(16))
        assert load == list(range(16))

    def test_axes_length_matches_map_dims(self):
        # For 551x: blank ROM is fine — we return the fixed constant arrays.
        # For 404/V8: build a ROM with valid descriptor headers at each map's header address.
        rom_551 = bytes(MAIN_CHIP_WORKING)

        # Write headers for all non-551 variants into one shared 32KB buffer
        rom_flat = bytearray(FLAT_32K)
        for v in ALL_VARIANTS:
            if v.software_id in ("551C", "551AA"):
                continue
            for m in v.main_maps:
                if m.rows > 1 and m.cols > 1:
                    ha = m.main_addr - 36
                    if 0 <= ha and ha + 4 + m.rows + m.cols <= FLAT_32K:
                        rom_flat[ha]      = 0x3A
                        rom_flat[ha + 1]  = m.rows
                        rom_flat[ha + 2: ha + 2 + m.rows] = bytes(range(m.rows))
                        rom_flat[ha + 2 + m.rows]     = 0x3F
                        rom_flat[ha + 3 + m.rows]     = m.cols
                        rom_flat[ha + 4 + m.rows: ha + 4 + m.rows + m.cols] = bytes(range(m.cols))
        rom_flat = bytes(rom_flat)

        for v in ALL_VARIANTS:
            rom = rom_551 if v.software_id in ("551C", "551AA") else rom_flat
            for m in v.main_maps:
                if m.rows > 1 and m.cols > 1:
                    rpm, load = get_axes(rom, m, v)
                    assert len(rpm)  == m.rows, f"{v.name}/{m.name}: rpm len {len(rpm)} != {m.rows}"
                    assert len(load) == m.cols, f"{v.name}/{m.name}: load len {len(load)} != {m.cols}"
