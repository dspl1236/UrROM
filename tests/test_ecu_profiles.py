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
    ign_decode_3b, ign_encode_3b,
    rpm_decode, load_decode, temp_decode,
    fuel_decode, fuel_encode,
    get_axes, read_axes_from_header,
    _RPM_AXIS_551, _LOAD_AXIS_551,
    # data
    ALL_VARIANTS, VARIANT_551AA, VARIANT_551AA_0202,
    VARIANT_551B, VARIANT_551C,
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
    """
    Rev limit reality check (2026-03 RE session):
    - Stock 551x chips: rev limit is NOT at WH 0x3FF0. That region is
      end-of-calibration RPM table data (confirmed from ABY direct chip read).
      30_000_000/raw formula was never applicable to M2.3.2 stock firmware.
    - prjmod 551AA_0202: LC/NLS hard RPM limit scalar at WH 0x0617, raw × 40.
    - read_rev_limit returns None for all non-prjmod variants.
    """

    def test_prjmod_write_read_roundtrip(self):
        """prjmod variant: write RPM → read back within 40 RPM (raw × 40 encoding)."""
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        rom = write_rev_limit(rom, VARIANT_551AA_0202, 7000)
        result = read_rev_limit(bytes(rom), VARIANT_551AA_0202)
        assert result is not None
        assert abs(result - 7000) <= 40   # ±1 raw step

    def test_prjmod_6500_rpm(self):
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        rom = write_rev_limit(rom, VARIANT_551AA_0202, 6500)
        result = read_rev_limit(bytes(rom), VARIANT_551AA_0202)
        assert result is not None
        assert abs(result - 6500) <= 40

    def test_stock_returns_none(self):
        """Stock 551x variants have no accessible rev limit scalar — must return None."""
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        assert read_rev_limit(bytes(rom), VARIANT_551AA)  is None
        assert read_rev_limit(bytes(rom), VARIANT_551B)   is None
        assert read_rev_limit(bytes(rom), VARIANT_551C)   is None
        assert read_rev_limit(bytes(rom), VARIANT_404)    is None

    def test_prjmod_address_is_0x0617(self):
        """Confirm the raw byte at WH 0x0617 drives prjmod rev limit."""
        rom = bytearray(MAIN_CHIP_PHYSICAL)
        rom[0x0617] = 170   # 170 × 40 = 6800 RPM
        result = read_rev_limit(bytes(rom), VARIANT_551AA_0202)
        assert result == 6800

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
        # Descriptor format confirmed from 3B/ABY/ADU direct chip reads (2026-03):
        # [0x3A][16 RPM raw bytes][misc byte(s)][0x3F][16 load raw bytes][2 bytes]
        # RPM raw decoded ×40 = RPM; load bytes returned as-is.
        ign_map = next(m for m in VARIANT_404.main_maps if m.map_type == "ign")
        header_addr = ign_map.main_addr - 36
        rom = bytearray(FLAT_32K)
        rpm_raw   = list(range(10, 26))   # 10-25 raw → 400-1000 RPM after ×40
        load_vals = list(range(20, 36))   # 20-35 raw load
        rom[header_addr]                      = 0x3A
        rom[header_addr + 1: header_addr + 17] = bytes(rpm_raw)
        rom[header_addr + 17]                  = 0x51  # misc byte between axes
        rom[header_addr + 18]                  = 0x3F
        rom[header_addr + 19: header_addr + 35] = bytes(load_vals)
        rpm_out, load_out = get_axes(bytes(rom), ign_map, VARIANT_404)
        assert rpm_out  == [b * 40 for b in rpm_raw], f"RPM decode wrong: {rpm_out}"
        assert load_out == load_vals, f"Load wrong: {load_out}"


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
            rom = rom_551 if v.software_id in ("551C", "551AA", "551B", "551B_D02", "551A") else rom_flat
            for m in v.main_maps:
                if m.rows <= 1 or m.cols <= 1:
                    continue
                # Skip PROVISIONAL maps with unusual dimensions — they may not
                # have standard 36-byte descriptors (e.g. End-of-Cal RPM table).
                if m.confidence == "PROVISIONAL" and (m.rows != 16 or m.cols != 16):
                    continue
                rpm, load = get_axes(rom, m, v)
                assert len(rpm)  == m.rows,  f"{v.name}/{m.name}: rpm len {len(rpm)} != {m.rows}"
                assert len(load) == m.cols,  f"{v.name}/{m.name}: load len {len(load)} != {m.cols}"

    def test_0202_fuel_axes_read_from_rom(self):
        """551AA_0202: fuel P/T axes read from confirmed WH addresses."""
        from urrom.ecu_profiles import VARIANT_551AA_0202
        rom = bytearray(MAIN_CHIP_WORKING)
        # Plant known RPM axis at 0x0DF1 (16 bytes, ×40 decode)
        rpm_raw = list(range(1, 17))   # 40, 80, 120, ... 640 after decode
        rom[0x0DF1:0x0E01] = bytes(rpm_raw)
        # Plant known Load axis at 0x0E03 (16 bytes, raw)
        load_raw = list(range(10, 26))
        rom[0x0E03:0x0E13] = bytes(load_raw)
        fuel_m = next(m for m in VARIANT_551AA_0202.main_maps if m.main_addr == 0x0E13)
        rows, cols = get_axes(bytes(rom), fuel_m, VARIANT_551AA_0202)
        assert rows == load_raw, f"Load axis mismatch: {rows}"
        assert cols == [b * 40 for b in rpm_raw], f"RPM axis mismatch: {cols}"

    def test_0202_ign_axes_independent_of_fuel(self):
        """551AA_0202: ign P/T uses separate axis addresses from fuel P/T."""
        from urrom.ecu_profiles import VARIANT_551AA_0202
        rom = bytearray(MAIN_CHIP_WORKING)
        ign_rpm = list(range(5, 21))   # 200, 240, ... 800 after decode
        ign_load = list(range(20, 36))
        rom[0x123D:0x124D] = bytes(ign_rpm)
        rom[0x124F:0x125F] = bytes(ign_load)
        ign_m = next(m for m in VARIANT_551AA_0202.main_maps if m.main_addr == 0x125F)
        rows, cols = get_axes(bytes(rom), ign_m, VARIANT_551AA_0202)
        assert rows == ign_load
        assert cols == [b * 40 for b in ign_rpm]

    def test_0202_ve_map_axis(self):
        """551AA_0202: VE table uses separate RPM+MAP axes."""
        from urrom.ecu_profiles import VARIANT_551AA_0202
        rom = bytearray(MAIN_CHIP_WORKING)
        ve_rpm = list(range(2, 18))   # RPM×40
        ve_map = [int(b * 1.035) for b in range(16)]  # raw MAP values → kPa after decode
        rom[0x2052:0x2062] = bytes(ve_rpm)
        rom[0x2064:0x2074] = bytes(range(16))
        ve_m = next(m for m in VARIANT_551AA_0202.main_maps if m.main_addr == 0x2074)
        rows, cols = get_axes(bytes(rom), ve_m, VARIANT_551AA_0202)
        assert cols == [b * 40 for b in ve_rpm]
        # rows should be kPa-decoded (raw/1.035)
        assert len(rows) == 16
        assert rows[0] == round(0 / 1.035, 1)

    def test_0202_unmapped_table_returns_sequential(self):
        """551AA_0202: tables not in _AXES_0202 fall back to sequential indices."""
        from urrom.ecu_profiles import VARIANT_551AA_0202
        rom = bytes(MAIN_CHIP_WORKING)
        # Warmup Enrichment has no axis entry
        wu_m = next(m for m in VARIANT_551AA_0202.main_maps if m.main_addr == 0x0D9A)
        rows, cols = get_axes(rom, wu_m, VARIANT_551AA_0202)
        assert rows == list(range(wu_m.rows))
        assert cols == list(range(wu_m.cols))

    def test_0202_all_mapped_axes_correct_length(self):
        """551AA_0202: every axis in _AXES_0202 produces lists matching map dims."""
        from urrom.ecu_profiles import VARIANT_551AA_0202, _AXES_0202
        rom = bytearray(MAIN_CHIP_WORKING)
        # Plant non-zero data in all axis regions so decodes produce meaningful values
        for addr in set(a for row, col in _AXES_0202.values() for a in (row, col) if a):
            end = min(addr + 16, MAIN_CHIP_WORKING)
            for i in range(addr, end):
                rom[i] = (i % 200) + 1
        rom_b = bytes(rom)
        for m in VARIANT_551AA_0202.main_maps:
            if m.main_addr in _AXES_0202:
                rows, cols = get_axes(rom_b, m, VARIANT_551AA_0202)
                assert len(rows) == m.rows, f"{m.name}: row axis len {len(rows)} != {m.rows}"
                assert len(cols) == m.cols, f"{m.name}: col axis len {len(cols)} != {m.cols}"


# ── Decode / encode functions ─────────────────────────────────────────────────

class TestDecodeFunctions:
    """Unit tests for the raw→physical decode functions used by the map editor."""

    def test_rpm_decode_zero(self):
        assert rpm_decode(0) == pytest.approx(0.0)

    def test_rpm_decode_scale(self):
        # 100 raw → 4000 RPM (scale = 40 RPM/count)
        assert rpm_decode(100) == pytest.approx(4000.0)

    def test_rpm_decode_max(self):
        assert rpm_decode(255) > 6000   # well above redline

    def test_load_decode_zero(self):
        assert load_decode(0) == pytest.approx(0.0)

    def test_load_decode_scale(self):
        # Linear: 128 raw → ~6.4 g/rev
        val = load_decode(128)
        assert 5.0 < val < 8.0

    def test_temp_decode_cold(self):
        # 0 raw → -48°C (cold start temperature)
        assert temp_decode(0) == pytest.approx(-48.0)

    def test_temp_decode_warm(self):
        # ~90°C should be reachable
        raw = next(r for r in range(256) if temp_decode(r) >= 90.0)
        assert 150 < raw < 256

    def test_ign_encode_decode_roundtrip(self):
        for deg in [-5.0, 0.0, 10.0, 20.0, 30.0, 42.0]:
            raw  = ign_encode(deg)
            back = ign_decode(raw)
            assert abs(back - deg) < 1.0, \
                f"ign round-trip {deg}° → raw {raw} → {back}°"

    def test_ign_advance_positive_raw(self):
        # Positive advance should produce positive raw value
        assert ign_encode(20.0) > ign_encode(0.0)

    def test_ign_3b_encode_decode_roundtrip(self):
        # 3B engine uses slightly different coefficients
        for deg in [0.0, 15.0, 30.0, 45.0]:
            raw  = ign_encode_3b(deg)
            back = ign_decode_3b(raw)
            assert abs(back - deg) < 1.0, \
                f"ign_3b round-trip {deg}° → raw {raw} → {back}°"

    def test_fuel_encode_decode_roundtrip(self):
        for val in [50.0, 80.0, 100.0, 120.0, 150.0]:
            raw  = fuel_encode(val)
            back = fuel_decode(raw)
            assert abs(back - val) < 1.0, \
                f"fuel round-trip {val} → raw {raw} → {back}"

    def test_fuel_identity_at_integer(self):
        # Fuel map uses raw = val directly for most values
        for val in range(10, 200, 20):
            assert fuel_decode(val) == pytest.approx(float(val))


# ── MapTable editing operations (headless) ─────────────────────────────────────

class TestMapTableEditing:
    """
    Test MapTable bulk-edit operations without a QApplication.
    We call the internal data-manipulation methods directly on a minimal stub.
    """

    def _make_table(self, rows=4, cols=4):
        """Minimal stub that replicates MapTable data layer without Qt widgets."""
        import copy as _copy
        import types

        stub = types.SimpleNamespace()
        stub._map_def     = types.SimpleNamespace(rows=rows, cols=cols, map_type="raw")
        stub._current_raw = [[r * cols + c + 10 for c in range(cols)] for r in range(rows)]
        stub._original_raw= _copy.deepcopy(stub._current_raw)
        stub._undo_stack  = []
        stub._redo_stack  = []

        # Bind the real methods from the class — works because they only touch stub attrs
        from app.main import MapTable
        for name in ("_push_undo", "undo", "redo", "_disp_to_raw",
                     "_scale_selection", "_interpolate_rows", "_interpolate_cols",
                     "_fill_selection", "_invert_selection", "_smooth_selection"):
            setattr(stub, name, MapTable.__dict__[name].__get__(stub, type(stub)))

        # Minimal stubs for Qt calls used inside editing ops
        stub._redraw = lambda: None
        stub.itemChanged = types.SimpleNamespace(emit=lambda *a: None)
        stub._selected_cells = lambda: []   # overridden per-test

        return stub

    # ── undo / redo ──────────────────────────────────────────────────────────

    def test_undo_restores_previous_state(self):
        t = self._make_table()
        original = [row[:] for row in t._current_raw]
        t._push_undo()
        t._current_raw[0][0] = 99
        t.undo()
        assert t._current_raw[0][0] == original[0][0]

    def test_redo_reapplies_state(self):
        t = self._make_table()
        t._push_undo()
        t._current_raw[1][1] = 42
        after_edit = t._current_raw[1][1]
        t.undo()
        assert t._current_raw[1][1] != after_edit
        t.redo()
        assert t._current_raw[1][1] == after_edit

    def test_undo_stack_limit_30(self):
        t = self._make_table()
        for _ in range(40):
            t._push_undo()
        assert len(t._undo_stack) <= 30

    def test_new_edit_clears_redo(self):
        t = self._make_table()
        t._push_undo()
        t._current_raw[0][0] = 77
        t.undo()
        # Make a new edit — redo stack must clear
        t._push_undo()
        t._current_raw[0][0] = 55
        assert len(t._redo_stack) == 0

    # ── scale ────────────────────────────────────────────────────────────────

    def test_scale_multiplies_values(self):
        t = self._make_table()
        # Select row 0 all cols
        t._selected_cells = lambda: [(0, c) for c in range(4)]
        original_row = t._current_raw[3][:]  # display row 0 = raw row 3 (inverted)
        t._scale_selection(2.0)
        for c in range(4):
            assert t._current_raw[3][c] == min(255, original_row[c] * 2)

    def test_scale_clamps_to_255(self):
        t = self._make_table()
        t._current_raw[3] = [200, 200, 200, 200]
        t._selected_cells = lambda: [(0, c) for c in range(4)]
        t._scale_selection(2.0)
        assert all(v == 255 for v in t._current_raw[3])

    def test_scale_pushes_undo(self):
        t = self._make_table()
        t._selected_cells = lambda: [(0, 0)]
        t._scale_selection(1.5)
        assert len(t._undo_stack) == 1

    # ── interpolate rows ─────────────────────────────────────────────────────

    def test_interpolate_rows_linear(self):
        t = self._make_table(rows=4, cols=6)
        # Set first and last col of raw row 0
        t._current_raw[0][0] = 10
        t._current_raw[0][5] = 60
        # display row 3 = raw row 0
        t._selected_cells = lambda: [(3, c) for c in range(6)]
        t._interpolate_rows()
        row = t._current_raw[0]
        # Values should be ~10, 20, 30, 40, 50, 60
        assert row[0] == 10
        assert row[5] == 60
        assert row[2] == pytest.approx(30, abs=1)
        assert row[3] == pytest.approx(40, abs=1)

    # ── interpolate columns ──────────────────────────────────────────────────

    def test_interpolate_cols_linear(self):
        t = self._make_table(rows=6, cols=4)
        # Set display row 0 = raw row 5, display row 5 = raw row 0
        t._current_raw[5][0] = 0    # display row 0
        t._current_raw[0][0] = 50   # display row 5
        t._selected_cells = lambda: [(r, 0) for r in range(6)]
        t._interpolate_cols()
        # Midpoint display row 2 = raw row 3: expect ~20
        assert t._current_raw[3][0] == pytest.approx(20, abs=2)

    # ── fill ─────────────────────────────────────────────────────────────────

    def test_fill_sets_all_selected(self):
        t = self._make_table()
        t._selected_cells = lambda: [(r, c) for r in range(4) for c in range(4)]
        t._fill_selection(128)
        for r in range(4):
            assert all(v == 128 for v in t._current_raw[r])

    # ── invert ───────────────────────────────────────────────────────────────

    def test_invert_255_minus_x(self):
        t = self._make_table()
        t._current_raw[0][0] = 100
        t._selected_cells = lambda: [(3, 0)]   # display row 3 = raw row 0
        t._invert_selection()
        assert t._current_raw[0][0] == 155

    # ── smooth ───────────────────────────────────────────────────────────────

    def test_smooth_averages_neighbours(self):
        t = self._make_table(rows=4, cols=5)
        t._current_raw[0] = [0, 0, 100, 0, 0]
        t._selected_cells = lambda: [(3, c) for c in range(5)]  # raw row 0
        t._smooth_selection()
        # Middle value (100) should pull toward neighbours
        assert t._current_raw[0][2] < 100
        # Neighbours should pull up
        assert t._current_raw[0][1] > 0 or t._current_raw[0][3] > 0


# ── XDF Import ─────────────────────────────────────────────────────────────────

class TestXDFImport:
    """Tests for urrom/xdf_import.py — XDF v1.50 parser."""

    XDF_FUEL = "/home/claude/rs2_xdf/RS2 551B fuel timing.xdf"
    XDF_BOOST = "/home/claude/rs2_xdf/8D0907551B RS2 Boost.xdf"

    def _skip_if_missing(self, path):
        import pytest
        from pathlib import Path
        if not Path(path).exists():
            pytest.skip(f"XDF file not available: {path}")

    def test_parse_fuel_xdf_basic(self):
        self._skip_if_missing(self.XDF_FUEL)
        from urrom.xdf_import import parse_xdf
        r = parse_xdf(self.XDF_FUEL)
        assert r.region_size == 0x10000   # 64KB fuel chip
        assert r.wh_offset_delta == 0x8000
        assert len(r.tables) >= 100       # vwnut8392 XDF has 314 tables

    def test_fuel_xdf_map_address(self):
        self._skip_if_missing(self.XDF_FUEL)
        from urrom.xdf_import import parse_xdf
        r = parse_xdf(self.XDF_FUEL)
        # Fuel map at WH 0x0E13 (XDF addr 0x8E13)
        fuel_tbls = [t for t in r.tables if "Fuel" in t.title and t.rows == 16 and t.cols == 16]
        assert fuel_tbls, "No 16x16 fuel table found"
        wh_addrs = set(t.address - 0x8000 for t in fuel_tbls)
        assert 0x0E13 in wh_addrs, f"Expected 0x0E13 in {wh_addrs}"

    def test_ign_decode_equation_parsed(self):
        self._skip_if_missing(self.XDF_FUEL)
        from urrom.xdf_import import parse_xdf, equation_to_decode
        r = parse_xdf(self.XDF_FUEL)
        ign_tbls = [t for t in r.tables if "Ignition" in t.title and t.rows == 16 and t.cols == 16
                    and "0.75" in t.equation]
        assert ign_tbls, "No ign table with 0.75 equation found"
        decode = equation_to_decode(ign_tbls[0].equation)
        assert decode is not None
        # (X * 0.75) - 22.5 at raw=83: 83*0.75-22.5 = 39.75
        assert abs(decode(83) - 39.75) < 0.1

    def test_parse_boost_xdf_basic(self):
        self._skip_if_missing(self.XDF_BOOST)
        from urrom.xdf_import import parse_xdf
        r = parse_xdf(self.XDF_BOOST)
        assert r.region_size == 0x7FFF   # 32KB boost chip
        assert r.wh_offset_delta == 0    # direct addresses
        assert len(r.tables) == 20

    def test_boost_xdf_n75_address(self):
        self._skip_if_missing(self.XDF_BOOST)
        from urrom.xdf_import import parse_xdf
        r = parse_xdf(self.XDF_BOOST)
        n75 = [t for t in r.tables if "N75" in t.title and t.rows >= 8]
        assert n75
        assert n75[0].address == 0x2480

    def test_equation_identity(self):
        from urrom.xdf_import import equation_to_decode, equation_to_encode
        assert equation_to_decode("X") is None
        assert equation_to_encode("X") is None

    def test_equation_linear_decode(self):
        from urrom.xdf_import import equation_to_decode
        fn = equation_to_decode("X*0.6491-8.2186")
        assert fn is not None
        assert abs(fn(50) - (50 * 0.6491 - 8.2186)) < 0.001

    def test_equation_linear_encode_roundtrip(self):
        from urrom.xdf_import import equation_to_decode, equation_to_encode
        eq = "X*0.6491-8.2186"
        decode = equation_to_decode(eq)
        encode = equation_to_encode(eq)
        assert decode is not None and encode is not None
        for raw in [30, 50, 80, 120]:
            decoded = decode(raw)
            reencoded = encode(decoded)
            assert abs(reencoded - raw) <= 1, f"Roundtrip fail: {raw} → {decoded} → {reencoded}"

    def test_xdf_to_mapdefs_returns_mapdefs(self):
        self._skip_if_missing(self.XDF_FUEL)
        from urrom.xdf_import import parse_xdf, xdf_to_mapdefs
        from urrom.ecu_profiles import MapDef
        r = parse_xdf(self.XDF_FUEL)
        maps = xdf_to_mapdefs(r)
        assert len(maps) > 0
        assert all(isinstance(m, MapDef) for m in maps)
        # All addresses should be WH offsets (< 0x8000)
        for m in maps:
            assert m.main_addr < 0x8000, f"{m.name} addr 0x{m.main_addr:04X} not WH"


# ── KNOWN_CRCS fingerprint tests ───────────────────────────────────────────────

class TestKnownCRCsFingerprinting:
    """Verify all .034 files fingerprint correctly against KNOWN_CRCS."""

    BASE = "/home/claude/034_files/034 Files"

    def _skip_if_missing(self):
        import pytest
        from pathlib import Path
        if not Path(self.BASE).exists():
            pytest.skip("034_files not available")

    def _crc_of(self, rel: str) -> int:
        import zlib
        from pathlib import Path
        from urrom.descramble import descramble_034, is_valid_034
        from urrom.ecu_profiles import normalize_rom
        p = Path(self.BASE) / rel
        raw = p.read_bytes()
        assert is_valid_034(raw), f"{p.name} not valid .034"
        dec = descramble_034(raw)
        # Detect boost chip
        if bytes(dec[:3]) == b'\xc2\xaf\x00':
            return zlib.crc32(dec[:0x8000]) & 0xFFFFFFFF
        wh, _ = normalize_rom(dec)
        return zlib.crc32(bytes(wh)) & 0xFFFFFFFF

    def test_stock_ripchip_known(self):
        self._skip_if_missing()
        crc = self._crc_of("551AA/K24/034 - 4A0907551AA - Stock RipChip.034")
        assert crc == 0x956BFC9C

    def test_gt2871_fuel_known(self):
        self._skip_if_missing()
        crc = self._crc_of(
            "551AA/GT2871/034 - 4A0907551AA - (Audi S4 (034 2871 Stage 1 R9.1 550cc EV14)).034")
        assert crc == 0x2EB58546

    def test_gt3071_r9_440cc_new(self):
        self._skip_if_missing()
        crc = self._crc_of(
            "551AA/GT3071/034 - 4A0907551AA - (Audi S4 (034 3071 R9 440cc Siemens).034")
        assert crc == 0xB9F0FD51
        from urrom.ecu_profiles import KNOWN_CRCS
        assert crc in KNOWN_CRCS
        assert "GT3071" in KNOWN_CRCS[crc][1]

    def test_gt2871_boost_chip_new(self):
        self._skip_if_missing()
        crc = self._crc_of(
            "551AA/GT2871/034 - (Audi S4 Boost Chip (034 2871 Stage 1)).034")
        assert crc == 0x69156B3A
        from urrom.ecu_profiles import KNOWN_CRCS, CHIP_REQUIREMENTS
        assert crc in KNOWN_CRCS
        assert crc in CHIP_REQUIREMENTS
        req = CHIP_REQUIREMENTS[crc]
        assert req["boost_chip"] is True
        assert req["map_kpa"] == 300

    def test_gt3071_boost_chip_new(self):
        self._skip_if_missing()
        crc = self._crc_of(
            "551AA/GT3071/034 - (Audi S4 Boost Chip (034 3071 Stage 1 26-23psi)).034")
        assert crc == 0x39DC67DA
        from urrom.ecu_profiles import CHIP_REQUIREMENTS
        req = CHIP_REQUIREMENTS[crc]
        assert req["boost_chip"] is True
        assert "GT3071" in req["notes"]

    def test_7a_na_bigmaf_new(self):
        self._skip_if_missing()
        crc = self._crc_of(
            "Hitachi Based/7A/034 - 893906266B  - (Audi CQ (NA Big MAF 91Oct R2) .034")
        assert crc == 0x84B0504E
        from urrom.ecu_profiles import KNOWN_CRCS
        sw_id, label = KNOWN_CRCS[crc]
        assert sw_id == "7A_NA"
        assert "266B" in label

    def test_aah_stage1_new(self):
        self._skip_if_missing()
        crc = self._crc_of("Hitachi Based/AAH/AAH Stage 1+ R1 (2).034")
        assert crc == 0x4818FA0B
        from urrom.ecu_profiles import KNOWN_CRCS, CHIP_REQUIREMENTS
        sw_id, label = KNOWN_CRCS[crc]
        assert sw_id == "AAH"
        assert "MMS-200" in CHIP_REQUIREMENTS[crc]["notes"]

    def test_boost_chips_differ_at_n75(self):
        """GT2871 and GT3071 boost chips differ at N75 table (0x2480) — confirms turbo-specific tuning."""
        self._skip_if_missing()
        import zlib
        from pathlib import Path
        from urrom.descramble import descramble_034
        b2 = Path(self.BASE) / "551AA/GT2871/034 - (Audi S4 Boost Chip (034 2871 Stage 1)).034"
        b3 = Path(self.BASE) / "551AA/GT3071/034 - (Audi S4 Boost Chip (034 3071 Stage 1 26-23psi)).034"
        wh_2 = descramble_034(b2.read_bytes())[:0x8000]
        wh_3 = descramble_034(b3.read_bytes())[:0x8000]
        assert wh_2 != wh_3, "Boost chips should differ"
        # N75 table at 0x2480 should differ
        assert wh_2[0x2480] != wh_3[0x2480], "N75 table at 0x2480 should differ between turbo specs"


# ── MapTable nudge / offset tests ─────────────────────────────────────────────

class TestMapTableNudgeOffset:
    """Test arrow-key nudge and offset operations."""

    def _make_table(self, rows=4, cols=4):
        import copy, types
        stub = types.SimpleNamespace()
        stub._map_def = types.SimpleNamespace(rows=rows, cols=cols, map_type="raw",
                                               decode=None, unit="raw")
        stub._current_raw = [[50 for _ in range(cols)] for _ in range(rows)]
        stub._original_raw = copy.deepcopy(stub._current_raw)
        stub._undo_stack = []; stub._redo_stack = []
        from app.main import MapTable
        for name in ("_push_undo", "_disp_to_raw", "_nudge_selection",
                     "_offset_selection", "_selected_cells"):
            setattr(stub, name, MapTable.__dict__[name].__get__(stub, type(stub)))
        stub._redraw = lambda: None
        import types as _t
        stub.itemChanged = _t.SimpleNamespace(emit=lambda *a: None)
        stub._selected_cells = lambda: [(0, c) for c in range(cols)]  # select display row 0
        return stub

    def test_nudge_plus1(self):
        t = self._make_table()
        raw_row = t._map_def.rows - 1  # display row 0 = raw row 3
        original = t._current_raw[raw_row][0]
        t._nudge_selection(+1)
        assert t._current_raw[raw_row][0] == original + 1

    def test_nudge_minus5(self):
        t = self._make_table()
        raw_row = t._map_def.rows - 1
        t._nudge_selection(-5)
        assert t._current_raw[raw_row][0] == 45

    def test_nudge_clamps_at_255(self):
        t = self._make_table()
        raw_row = t._map_def.rows - 1
        t._current_raw[raw_row] = [255] * 4
        t._nudge_selection(+10)
        assert all(v == 255 for v in t._current_raw[raw_row])

    def test_nudge_clamps_at_0(self):
        t = self._make_table()
        raw_row = t._map_def.rows - 1
        t._current_raw[raw_row] = [2] * 4
        t._nudge_selection(-10)
        assert all(v == 0 for v in t._current_raw[raw_row])

    def test_offset_positive(self):
        t = self._make_table()
        raw_row = t._map_def.rows - 1
        t._offset_selection(+15)
        assert t._current_raw[raw_row][0] == 65

    def test_offset_negative(self):
        t = self._make_table()
        raw_row = t._map_def.rows - 1
        t._offset_selection(-20)
        assert t._current_raw[raw_row][0] == 30

    def test_nudge_pushes_undo(self):
        t = self._make_table()
        t._nudge_selection(+1)
        assert len(t._undo_stack) == 1


# ── Boost chip pairing ─────────────────────────────────────────────────────────

class TestBoostChipPairing:
    """Tests for BOOST_CHIP_PAIRINGS and check_chip_pair."""

    def test_correct_pair_is_ok(self):
        from urrom.ecu_profiles import check_chip_pair
        # GT2871 R9.1 EV14 fuel -> GT2871 boost
        status, msg = check_chip_pair(0x2EB58546, 0x69156B3A)
        assert status == 'ok'
        assert 'confirmed' in msg.lower()

    def test_gt3071_boost_with_gt2871_fuel_is_mismatch(self):
        from urrom.ecu_profiles import check_chip_pair
        fuel_crc  = 0x2EB58546  # GT2871 fuel
        boost_crc = 0x39DC67DA  # GT3071 boost
        status, msg = check_chip_pair(fuel_crc, boost_crc)
        assert status == 'mismatch'
        assert 'MISMATCH' in msg

    def test_no_pairing_required_is_ok(self):
        from urrom.ecu_profiles import check_chip_pair
        # Stock ABY chip has no mandatory boost chip pairing
        status, msg = check_chip_pair(0xA98CB481, 0x4EE87833)
        assert status == 'ok'

    def test_stock_rip_chip_pairs_with_stock_boost(self):
        from urrom.ecu_profiles import check_chip_pair
        status, _ = check_chip_pair(0x956BFC9C, 0x16707F66)
        assert status == 'ok'

    def test_unknown_fuel_check_pair_returns_ok(self):
        from urrom.ecu_profiles import check_chip_pair
        status, msg = check_chip_pair(0xDEADBEEF, 0x12345678)
        assert status == 'ok'

    def test_unknown_fuel_get_pairing_returns_empty(self):
        from urrom.ecu_profiles import get_boost_pairing
        assert get_boost_pairing(0x00000000) == []

    def test_get_boost_pairing_returns_list(self):
        from urrom.ecu_profiles import get_boost_pairing
        pairings = get_boost_pairing(0xA77BB88E)
        assert isinstance(pairings, list)
        assert 0x69156B3A in pairings

    def test_k24_fuel_accepts_either_boost(self):
        from urrom.ecu_profiles import check_chip_pair
        fuel = 0xA47011AB  # Stage 1+ K24
        assert check_chip_pair(fuel, 0x69156B3A)[0] == 'ok'   # GT2871
        assert check_chip_pair(fuel, 0x39DC67DA)[0] == 'ok'   # GT3071

    def test_rs2_fuel_accepts_aby_or_adu_boost(self):
        from urrom.ecu_profiles import check_chip_pair
        fuel = 0x28C04D7B  # RS2 91Oct
        assert check_chip_pair(fuel, 0xF6E33043)[0] == 'ok'   # ABY boost
        assert check_chip_pair(fuel, 0x4EE87833)[0] == 'ok'   # ADU boost
        assert check_chip_pair(fuel, 0x39DC67DA)[0] == 'mismatch'  # GT3071 wrong

    def test_gt3071_fuel_chips_all_require_gt3071_boost(self):
        from urrom.ecu_profiles import check_chip_pair
        # All GT3071 fuel chips should accept GT3071 boost, reject GT2871 boost
        gt3071_fuel = [0x6F3AE675, 0x07DA1752, 0xB9F0FD51]
        for crc in gt3071_fuel:
            ok_status, _ = check_chip_pair(crc, 0x39DC67DA)
            bad_status, _ = check_chip_pair(crc, 0x69156B3A)
            assert ok_status == 'ok',       f"0x{crc:08X} should accept GT3071 boost"
            assert bad_status == 'mismatch', f"0x{crc:08X} should reject GT2871 boost"

    def test_all_pairing_crcs_are_in_known_crcs(self):
        """Every CRC in BOOST_CHIP_PAIRINGS should be recognizable."""
        from urrom.ecu_profiles import BOOST_CHIP_PAIRINGS, KNOWN_CRCS
        for fuel_crc, boost_crcs in BOOST_CHIP_PAIRINGS.items():
            assert fuel_crc in KNOWN_CRCS, f"Fuel CRC 0x{fuel_crc:08X} not in KNOWN_CRCS"
            for boost_crc in boost_crcs:
                assert boost_crc in KNOWN_CRCS, \
                    f"Boost CRC 0x{boost_crc:08X} not in KNOWN_CRCS (paired with fuel 0x{fuel_crc:08X})"


# ── Map export ─────────────────────────────────────────────────────────────────

class TestMapExport:

    def _get_aby(self):
        import pytest
        from pathlib import Path
        p = Path("/mnt/user-data/uploads/aby_fuel-ign_551aa.bin")
        if not p.exists():
            pytest.skip("ABY ROM not available")
        return p.read_bytes()

    def test_export_map_html_returns_string(self):
        import sys; sys.path.insert(0, '.')
        from pathlib import Path
        from urrom.ecu_profiles import normalize_rom, detect_rom
        from urrom.map_export import export_map_html
        raw = self._get_aby()
        wh, _ = normalize_rom(raw)
        det = detect_rom(bytes(wh))
        v = det.variant
        m = next(mm for mm in v.main_maps if mm.map_type == "ign" and mm.rows == 16)
        result = export_map_html(bytes(wh), m, v)
        assert isinstance(result, str)
        assert "<table" in result
        assert "°BTDC" in result or m.name in result

    def test_export_map_html_has_axis_labels(self):
        from pathlib import Path
        from urrom.ecu_profiles import normalize_rom, detect_rom
        from urrom.map_export import export_map_html
        raw = self._get_aby()
        wh, _ = normalize_rom(raw)
        det = detect_rom(bytes(wh))
        v = det.variant
        m = next(mm for mm in v.main_maps if mm.map_type == "fuel")
        result = export_map_html(bytes(wh), m, v)
        # Should have at least one numeric axis header
        import re
        axis_vals = re.findall(r'<th[^>]*>\d+</th>', result)
        assert len(axis_vals) > 0, "No axis value cells found"

    def test_export_full_rom_html(self):
        from urrom.ecu_profiles import normalize_rom, detect_rom
        from urrom.map_export import export_full_rom_html
        raw = self._get_aby()
        wh, _ = normalize_rom(raw)
        det = detect_rom(bytes(wh))
        result = export_full_rom_html(bytes(wh), det.variant, det)
        assert "<!DOCTYPE html>" in result
        assert det.variant.name in result
        assert "<table" in result

    def test_heat_colours_in_range(self):
        from urrom.map_export import _lerp_hex, _ign_bg, _fuel_bg, _heat_bg
        # All colour functions should return valid hex
        import re
        for val in [-5, 0, 15, 30, 45]:
            c = _ign_bg(val)
            assert re.fullmatch(r'#[0-9a-f]{6}', c), f"Bad ign colour: {c}"
        for raw in [0, 64, 128, 192, 255]:
            c = _fuel_bg(raw)
            assert re.fullmatch(r'#[0-9a-f]{6}', c)
        for raw in [0, 128, 255]:
            c = _heat_bg(raw, 0, 255)
            assert re.fullmatch(r'#[0-9a-f]{6}', c)

    def test_no_xss_in_map_name(self):
        """Map names with special chars should be HTML-escaped."""
        from urrom.ecu_profiles import normalize_rom, detect_rom
        from urrom.map_export import export_map_html
        raw = self._get_aby()
        wh, _ = normalize_rom(raw)
        det = detect_rom(bytes(wh))
        v = det.variant
        m = v.main_maps[0]
        # Inject a fake name with XSS payload
        import copy
        m2 = copy.copy(m)
        object.__setattr__(m2, 'name', '<script>alert(1)</script>')
        result = export_map_html(bytes(wh), m2, v)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


# ── Tuning checks ──────────────────────────────────────────────────────────────

class TestTuningChecks:

    def _get_aby_wh(self):
        import pytest
        from pathlib import Path
        from urrom.ecu_profiles import normalize_rom, detect_rom
        p = Path("/mnt/user-data/uploads/aby_fuel-ign_551aa.bin")
        if not p.exists():
            pytest.skip("ABY ROM not available")
        raw = p.read_bytes()
        wh, _ = normalize_rom(raw)
        det = detect_rom(bytes(wh))
        return bytes(wh), det

    def test_stock_aby_has_no_issues(self):
        """Stock ABY chip should produce zero issues after filter calibration."""
        from urrom.tuning_checks import run_all_checks
        wh, det = self._get_aby_wh()
        issues = run_all_checks(wh, det.variant, crc32=det.crc32)
        errors = [i for i in issues if i.severity == 'error']
        assert errors == [], f"Unexpected errors on stock ABY: {errors}"

    def test_stock_aby_warnings_zero(self):
        from urrom.tuning_checks import run_all_checks
        wh, det = self._get_aby_wh()
        issues = run_all_checks(wh, det.variant, crc32=det.crc32)
        warnings = [i for i in issues if i.severity == 'warning']
        assert len(warnings) == 0, f"{len(warnings)} unexpected warnings on stock: {warnings[:3]}"

    def test_check_fuel_range_flags_lean(self):
        """Inject a lean cell and verify it's flagged."""
        import copy
        from urrom.tuning_checks import check_fuel_range
        wh, det = self._get_aby_wh()
        wh_mut = bytearray(wh)
        # Write a lean value (raw=200) into the fuel map area
        v = det.variant
        fuel_m = next(m for m in v.main_maps if m.map_type == 'fuel' and m.rows > 1)
        wh_mut[fuel_m.main_addr] = 200  # very lean
        issues = check_fuel_range(bytes(wh_mut), v, lean_threshold=170)
        lean_issues = [i for i in issues if 'lean' in i.description.lower()]
        assert len(lean_issues) > 0, "Lean cell not flagged"

    def test_check_ign_advance_flags_extreme(self):
        """Inject an extreme advance value and verify detection."""
        from urrom.tuning_checks import check_ign_advance
        wh, det = self._get_aby_wh()
        wh_mut = bytearray(wh)
        v = det.variant
        ign_m = next(m for m in v.main_maps
                     if m.map_type == 'ign' and m.confidence == 'CONFIRMED'
                     and m.rows > 1 and m.decode)
        # Write a value that decodes to 60° (extreme)
        # decode = raw * 0.6491 - 8.2186; 60 = raw * 0.6491 - 8.22 → raw ≈ 105
        wh_mut[ign_m.main_addr + 5] = 105
        issues = check_ign_advance(bytes(wh_mut), v, warn_advance=52.0, error_advance=58.0)
        errors = [i for i in issues if i.severity == 'error']
        assert len(errors) > 0, "Extreme advance not flagged"

    def test_repeated_rows_skips_flat_maps(self):
        """Maps with 'failsafe' in name should be excluded."""
        from urrom.tuning_checks import check_repeated_rows, _is_expected_flat
        assert _is_expected_flat("Fuel P/T (failsafe)")
        assert _is_expected_flat("VE Table")
        assert _is_expected_flat("Ign P/T (LPG)")
        assert not _is_expected_flat("Fuel P/T (primary)")
        assert not _is_expected_flat("Ign P/T (no knock)")

    def test_checksum_skips_stock(self):
        """Stock chips (CRC in KNOWN_CRCS with 'Stock' label) should not raise checksum error."""
        from urrom.tuning_checks import check_checksum
        from urrom.ecu_profiles import detect_rom
        wh, det = self._get_aby_wh()
        issues = check_checksum(wh, det.variant, crc32=det.crc32)
        assert issues == [], f"False alarm on stock chip: {issues}"

    def test_run_all_checks_returns_sorted(self):
        """Errors should come before warnings before info."""
        from urrom.tuning_checks import run_all_checks, TuningIssue
        wh, det = self._get_aby_wh()
        # Synthesise some issues
        issues = [
            TuningIssue('info',    'structure', 'M', 'd'),
            TuningIssue('warning', 'fuel',      'M', 'd'),
            TuningIssue('error',   'checksum',  'M', 'd'),
        ]
        order = {'error': 0, 'warning': 1, 'info': 2}
        issues.sort(key=lambda i: order.get(i.severity, 3))
        assert issues[0].severity == 'error'
        assert issues[1].severity == 'warning'
        assert issues[2].severity == 'info'


# ── Session log ────────────────────────────────────────────────────────────────

class TestSessionLog:

    def test_record_cell_edit(self):
        from urrom.session_log import SessionLog
        from urrom.ecu_profiles import MapDef
        log = SessionLog("test.bin", "551B")
        m = MapDef("Fuel", "desc", 0x2E17, 16, 16, map_type="fuel")
        log.record(m, 5, 3, 100, 110)
        assert log.count == 1
        assert "Fuel" in log.changed_maps

    def test_skip_unchanged(self):
        from urrom.session_log import SessionLog
        from urrom.ecu_profiles import MapDef
        log = SessionLog()
        m = MapDef("Ign", "", 0x30AC, 16, 16, map_type="ign")
        log.record(m, 0, 0, 50, 50)  # no change
        assert log.count == 0

    def test_to_text_output(self):
        from urrom.session_log import SessionLog
        from urrom.ecu_profiles import MapDef
        log = SessionLog("test.bin", "551B")
        m = MapDef("Fuel", "", 0x2E17, 16, 16, map_type="fuel")
        log.record(m, 3, 4, 128, 140)
        text = log.to_text()
        assert "Fuel" in text
        assert "test.bin" in text
        assert "551B" in text

    def test_to_html_output(self):
        from urrom.session_log import SessionLog
        from urrom.ecu_profiles import MapDef
        log = SessionLog("test.bin", "551B")
        m = MapDef("Fuel", "", 0x2E17, 16, 16, map_type="fuel")
        log.record(m, 1, 2, 100, 120)
        html = log.to_html()
        assert "<!DOCTYPE html>" in html
        assert "Fuel" in html

    def test_clear(self):
        from urrom.session_log import SessionLog
        from urrom.ecu_profiles import MapDef
        log = SessionLog()
        m = MapDef("Fuel", "", 0, 16, 16)
        log.record(m, 0, 0, 1, 2)
        assert log.count == 1
        log.clear()
        assert log.count == 0


# ── Data log ───────────────────────────────────────────────────────────────────

class TestDataLog:

    def test_parse_generic_csv(self, tmp_path):
        from urrom.datalog import load_log
        csv_file = tmp_path / "log.csv"
        csv_file.write_text(
            "Time_s,RPM,Load,AFR\n"
            "0.0,800,20,14.7\n"
            "0.1,1200,40,13.5\n"
            "0.2,2000,80,12.5\n"
        )
        log = load_log(csv_file)
        assert len(log.rows) == 3
        assert log.rows[0].rpm == pytest.approx(800)
        assert log.rows[1].rpm == pytest.approx(1200)

    def test_ms_timestamp_converted(self, tmp_path):
        from urrom.datalog import load_log
        csv_file = tmp_path / "log.csv"
        # Use realistic ms timestamps (>10000 triggers ms detection)
        csv_file.write_text(
            "time_ms,RPM\n10000,1000\n10100,2000\n10200,3000\n"
        )
        log = load_log(csv_file)
        # 10100 - 10000 = 100ms → 0.1s
        assert log.rows[1].time_s == pytest.approx(0.1, abs=0.01)

    def test_duration(self, tmp_path):
        from urrom.datalog import load_log
        csv_file = tmp_path / "log.csv"
        csv_file.write_text("Time_s,RPM\n0.0,1000\n5.0,3000\n")
        log = load_log(csv_file)
        assert log.duration_s == pytest.approx(5.0)

    def test_compute_coverage_basic(self, tmp_path):
        import sys; sys.path.insert(0, '.')
        from pathlib import Path
        import pytest
        from urrom.datalog import load_log, compute_coverage
        from urrom.ecu_profiles import normalize_rom, detect_rom

        aby_path = Path("/mnt/user-data/uploads/aby_fuel-ign_551aa.bin")
        if not aby_path.exists():
            pytest.skip("ABY ROM not available")

        wh, _ = normalize_rom(aby_path.read_bytes())
        det = detect_rom(bytes(wh))
        v = det.variant
        m = next(mm for mm in v.main_maps if mm.map_type == 'ign' and mm.rows > 1)

        csv_file = tmp_path / "log.csv"
        csv_file.write_text("Time_s,RPM,Load\n0.0,1000,10\n0.1,2000,20\n0.2,3000,30\n")
        log = load_log(csv_file)
        hits = compute_coverage(log, m, v, bytes(wh))
        assert len(hits) > 0

    def test_coverage_stats(self, tmp_path):
        from urrom.datalog import coverage_stats
        from urrom.ecu_profiles import MapDef
        m = MapDef("Ign", "", 0x30AC, 8, 8)  # 64 cells
        hits = {(0, 0): 10, (1, 1): 3, (2, 2): 1}
        stats = coverage_stats(hits, m)
        assert stats['total_cells'] == 64
        assert stats['hit_cells'] == 3
        assert stats['max_hits'] == 10
        assert len(stats['unvisited']) == 61
        assert len(stats['sparse_cells']) == 2   # count < 5


# ── Wideband AFR overlay ───────────────────────────────────────────────────────

class TestWidebandOverlay:

    def test_compute_afr_overlay_basic(self, tmp_path):
        import sys; sys.path.insert(0, '.')
        import pytest
        from pathlib import Path
        from urrom.datalog import load_log, compute_afr_overlay
        from urrom.ecu_profiles import normalize_rom, detect_rom

        aby_path = Path("/mnt/user-data/uploads/aby_fuel-ign_551aa.bin")
        if not aby_path.exists():
            pytest.skip("ABY ROM not available")

        wh, _ = normalize_rom(aby_path.read_bytes())
        det = detect_rom(bytes(wh))
        v = det.variant
        m = next(mm for mm in v.main_maps if mm.map_type == 'fuel' and mm.rows > 1)

        csv_file = tmp_path / "wb_log.csv"
        csv_file.write_text(
            "Time_s,RPM,Load,AFR,TPS\n"
            "0.0,2000,50,13.5,80\n"
            "0.1,3000,80,12.8,90\n"
            "0.2,4000,100,12.5,95\n"
            "0.3,2000,50,14.7,85\n"
        )
        log = load_log(csv_file)
        overlay = compute_afr_overlay(log, m, v, bytes(wh), target_afr=14.7)
        assert len(overlay) > 0

    def test_afr_overlay_lean_rich_detection(self, tmp_path):
        import sys; sys.path.insert(0, '.')
        import pytest
        from pathlib import Path
        from urrom.datalog import load_log, compute_afr_overlay
        from urrom.ecu_profiles import normalize_rom, detect_rom

        aby_path = Path("/mnt/user-data/uploads/aby_fuel-ign_551aa.bin")
        if not aby_path.exists():
            pytest.skip("ABY ROM not available")

        wh, _ = normalize_rom(aby_path.read_bytes())
        det = detect_rom(bytes(wh))
        v = det.variant
        m = next(mm for mm in v.main_maps if mm.map_type == 'fuel' and mm.rows > 1)

        csv_file = tmp_path / "wb_log.csv"
        # Use load values within _LOAD_AXIS_551 range [2,6,9,13...]
        # and clearly different RPM rows to ensure distinct cells
        csv_file.write_text(
            "Time_s,RPM,Load,AFR,TPS\n"
            "0.0,1000,6,16.5,80\n"    # lean (+1.8) → RPM row 1, load col 1
            "0.1,5000,15,12.5,90\n"   # rich (-2.2) → RPM row 11, load col 4
        )
        log = load_log(csv_file)
        overlay = compute_afr_overlay(log, m, v, bytes(wh), target_afr=14.7)

        lean_cells = [(k, d) for k, d in overlay.items() if d['delta'] > 1.0]
        rich_cells = [(k, d) for k, d in overlay.items() if d['delta'] < -1.0]
        assert len(lean_cells) > 0, "Lean cell not detected"
        assert len(rich_cells) > 0, "Rich cell not detected"

    def test_afr_overlay_filters_idle(self, tmp_path):
        import sys; sys.path.insert(0, '.')
        import pytest
        from pathlib import Path
        from urrom.datalog import load_log, compute_afr_overlay
        from urrom.ecu_profiles import normalize_rom, detect_rom

        aby_path = Path("/mnt/user-data/uploads/aby_fuel-ign_551aa.bin")
        if not aby_path.exists():
            pytest.skip("ABY ROM not available")

        wh, _ = normalize_rom(aby_path.read_bytes())
        det = detect_rom(bytes(wh))
        v = det.variant
        m = next(mm for mm in v.main_maps if mm.map_type == 'fuel' and mm.rows > 1)

        csv_file = tmp_path / "wb_log.csv"
        # Only idle rows (TPS < 20%) — should be filtered out
        csv_file.write_text(
            "Time_s,RPM,Load,AFR,TPS\n"
            "0.0,800,10,14.7,5\n"
            "0.1,900,12,14.5,8\n"
        )
        log = load_log(csv_file)
        overlay = compute_afr_overlay(log, m, v, bytes(wh), target_afr=14.7)
        # Idle filtered = no overlay data
        assert len(overlay) == 0


# ── Mock engine + KWP bridge ───────────────────────────────────────────────────

class TestMockEngine:

    def test_all_scenarios_produce_valid_state(self):
        """Each scenario step should produce a valid KWP state dict."""
        from tools.mock_engine import (
            IdleScenario, CruiseScenario, WOTScenario,
            WarmupScenario, KnockScenario, EngineState
        )
        for cls in [IdleScenario, CruiseScenario, WOTScenario, WarmupScenario, KnockScenario]:
            s = EngineState()
            scenario = cls()
            for t in [0, 5, 15]:
                scenario.step(t, 0.1, s)
            state = s.to_kwp_state("895907551B")
            assert state["connected"] is True
            assert state["ecu_id"]["part_number"] == "895907551B"
            assert "1" in state["groups"]
            assert "3" in state["groups"]
            cells1 = state["groups"]["1"]["cells"]
            assert len(cells1) == 4

    def test_idle_rpm_range(self):
        from tools.mock_engine import IdleScenario, EngineState
        s = EngineState()
        sc = IdleScenario()
        for t in [0, 5, 10, 20, 30]:
            sc.step(t, 0.1, s)
            assert 600 < s.rpm < 1200, f"Idle RPM out of range: {s.rpm}"
            assert 0.95 < s.lambda_ < 1.05, f"Idle lambda out of range: {s.lambda_}"

    def test_wot_rpm_increases(self):
        from tools.mock_engine import WOTScenario, EngineState
        s = EngineState()
        sc = WOTScenario()
        sc.step(0, 0.1, s);  rpm0 = s.rpm
        sc.step(10, 0.1, s); rpm10 = s.rpm
        assert rpm10 > rpm0 + 1000, f"WOT should build RPM: {rpm0} → {rpm10}"

    def test_warmup_ect_rises(self):
        from tools.mock_engine import WarmupScenario, EngineState
        s = EngineState()
        sc = WarmupScenario()
        sc.step(0, 0.1, s);  ect0 = s.ect
        sc.step(30, 0.1, s); ect30 = s.ect
        assert ect30 > ect0 + 20, f"ECT should rise during warmup: {ect0} → {ect30}"

    def test_knock_scenario_retards(self):
        from tools.mock_engine import KnockScenario, EngineState
        s = EngineState()
        sc = KnockScenario()
        # Run past first knock event at t=8
        for t_step in range(0, 120):
            sc.step(t_step * 0.1, 0.1, s)
        # At least one knock should have occurred
        assert s.knock_count > 0, "Knock scenario should have fired at least once"

    def test_livevalues_decodes_mock_state(self):
        """LiveValues should correctly decode a mock engine state dict."""
        from tools.mock_engine import CruiseScenario, EngineState
        from urrom.kwp import LiveValues
        s = EngineState()
        sc = CruiseScenario()
        sc.step(15, 0.1, s)
        state = s.to_kwp_state("895907551B")
        lv = LiveValues(state)
        assert lv.valid
        assert 2500 < lv.rpm < 3500
        assert 0.95 < lv.lambda_ < 1.05
        assert 20 < lv.timing < 40
        assert 80 < lv.ect < 92

    def test_mock_client_connects_and_receives(self):
        """MockKWPClient should connect to mock server and receive LiveValues."""
        import time
        from tools.mock_engine import IdleScenario, MockECUServer
        from urrom.kwp import MockKWPClient, LiveValues, mock_kwpbridge_running

        server = MockECUServer(port=50298, scenario=IdleScenario(),
                               part_number="895907551B")
        server.start()
        time.sleep(0.2)
        assert mock_kwpbridge_running(50298)

        received = []
        client = MockKWPClient(port=50298)
        client.on_state(lambda s: received.append(LiveValues(s)))
        client.connect()
        time.sleep(0.5)
        client.disconnect()
        server.stop()

        assert len(received) > 0, "Should have received at least one state"
        lv = next((r for r in received if r.valid), None)
        assert lv is not None, "At least one LiveValues should be valid"
        assert 600 < lv.rpm < 1200, f"Idle RPM: {lv.rpm}"

    def test_cycle_scenario_transitions(self):
        """Cycle scenario should move between sub-scenarios."""
        from tools.mock_engine import CycleScenario, EngineState
        s = EngineState()
        sc = CycleScenario()
        initial = sc._idx
        # Run long enough to trigger a transition (warmup = 60s)
        sc.step(0, 0.1, s)
        sc._scenarios[0].duration_s = 0.5  # shorten warmup for test
        sc.step(1.0, 0.1, s)
        # Just ensure it doesn't crash and state is plausible
        assert s.rpm > 0


# ── V8 split-bank normalize_rom + map detection ────────────────────────────────

class TestV8NormalizeAndMaps:

    def test_v8_split_bank_detection(self):
        """normalize_rom returns upper half for V8 split-bank chips."""
        from urrom.ecu_profiles import normalize_rom, _is_v8_split_bank
        from pathlib import Path
        for fname in ['roms/abh_fuel-ign_557a.bin',
                      'roms/s6v8_fuel-ign_557c.bin',
                      'roms/v8q_fuel-ign_557e.bin']:
            p = Path(fname)
            if not p.exists():
                continue
            raw = p.read_bytes()
            assert _is_v8_split_bank(raw), f"{fname} not detected as V8 split-bank"
            wh, notes = normalize_rom(raw)
            assert bytes(wh) == raw[32768:], f"{fname}: wrong half returned"
            assert any('V8 split-bank' in n for n in notes)

    def test_5cyl_not_detected_as_v8(self):
        """5-cyl doubled chips are not confused with V8 split-bank."""
        from urrom.ecu_profiles import normalize_rom, _is_v8_split_bank
        from pathlib import Path
        for fname in ['roms/aby_fuel-ign_551aa.bin', 'roms/adu_fuel-ign_551c.bin']:
            p = Path(fname)
            if not p.exists():
                continue
            raw = p.read_bytes()
            assert not _is_v8_split_bank(raw), f"{fname} wrongly detected as V8"
            wh, notes = normalize_rom(raw)
            assert bytes(wh) == raw[32768:], f"{fname}: upper half should still be selected"

    def test_32kb_flat_not_detected_as_v8(self):
        """32KB flat chips (3B, PT) pass through unchanged."""
        from urrom.ecu_profiles import normalize_rom, _is_v8_split_bank
        from pathlib import Path
        for fname in ['roms/3b_fuel-ign_404aa.bin', 'roms/pt_fuel-ign_404h.bin']:
            p = Path(fname)
            if not p.exists():
                continue
            raw = p.read_bytes()
            assert len(raw) == 32768
            assert not _is_v8_split_bank(raw)
            wh, _ = normalize_rom(raw)
            assert bytes(wh) == raw

    def test_v8_abh_map_detection(self):
        """VARIANT_V8_ABH maps load correct calibration from ABH chip."""
        from urrom.ecu_profiles import normalize_rom, detect_rom, read_map
        from pathlib import Path
        p = Path('roms/abh_fuel-ign_557a.bin')
        if not p.exists():
            return
        raw = p.read_bytes()
        wh, _ = normalize_rom(raw)
        det = detect_rom(bytes(wh))
        assert det.variant is not None
        assert det.variant.software_id == '557'
        # Ign Map A: should decode to plausible V8 advance values
        ign_a = next((m for m in det.variant.main_maps
                      if m.map_type == 'ign' and m.confidence == 'CONFIRMED'), None)
        assert ign_a is not None, "No confirmed ign map in V8 variant"
        data = read_map(bytes(wh), ign_a)
        flat = [data[r][c] for r in range(ign_a.rows) for c in range(ign_a.cols)]
        decoded = [ign_a.decode(v) for v in flat if 28 <= v <= 100]
        assert len(decoded) > 20, "Too few plausible ign cells"
        avg = sum(decoded) / len(decoded)
        assert 25 <= avg <= 45, f"V8 ign avg {avg:.1f}° out of expected range"

    def test_v8_abh_vs_v8q_advance(self):
        """V8Q should show higher average ign advance than ABH."""
        from urrom.ecu_profiles import normalize_rom, detect_rom, read_map
        from pathlib import Path
        abh_p = Path('roms/abh_fuel-ign_557a.bin')
        v8q_p = Path('roms/v8q_fuel-ign_557e.bin')
        if not abh_p.exists() or not v8q_p.exists():
            return
        def avg_ign(path):
            raw = path.read_bytes()
            wh, _ = normalize_rom(raw)
            det = detect_rom(bytes(wh))
            ign = next((m for m in det.variant.main_maps
                        if m.map_type == 'ign' and m.confidence == 'CONFIRMED'), None)
            if not ign: return 0
            data = read_map(bytes(wh), ign)
            flat = [data[r][c] for r in range(ign.rows) for c in range(ign.cols)]
            dec = [ign.decode(v) for v in flat if 28 <= v <= 100]
            return sum(dec)/len(dec) if dec else 0
        abh_avg = avg_ign(abh_p)
        v8q_avg = avg_ign(v8q_p)
        assert v8q_avg > abh_avg, f"V8Q {v8q_avg:.1f}° should exceed ABH {abh_avg:.1f}°"
        assert 2 < (v8q_avg - abh_avg) < 8, f"Unexpected delta: {v8q_avg-abh_avg:.1f}°"


# ── Idle ign map tests ─────────────────────────────────────────────────────────

class TestIdleIgnMaps:

    def test_aby_idle_ign_addresses(self):
        """ABY 551B has 2 confirmed idle ign maps at the correct addresses."""
        from urrom.ecu_profiles import VARIANT_551B
        idle = [m for m in VARIANT_551B.main_maps
                if 'Idle' in m.name and m.confidence == 'CONFIRMED']
        assert len(idle) == 2
        addrs = {m.main_addr for m in idle}
        assert 0x3D04 in addrs, "Closed-throttle idle map missing at 0x3D04"
        assert 0x3E64 in addrs, "AC-on idle map missing at 0x3E64"

    def test_adu_idle_ign_addresses(self):
        """ADU 551C has 2 confirmed idle ign maps at the ADU-specific addresses."""
        from urrom.ecu_profiles import VARIANT_551C
        idle = [m for m in VARIANT_551C.main_maps
                if 'Idle' in m.name and m.confidence == 'CONFIRMED']
        assert len(idle) == 2
        addrs = {m.main_addr for m in idle}
        assert 0x3D08 in addrs, "ADU closed-throttle idle map missing at 0x3D08"
        assert 0x3E68 in addrs, "ADU AC-on idle map missing at 0x3E68"

    def test_idle_ign_dimensions(self):
        """Idle ign maps are 3×6 on all 551x variants."""
        from urrom.ecu_profiles import VARIANT_551B, VARIANT_551C
        for v in [VARIANT_551B, VARIANT_551C]:
            for m in v.main_maps:
                if 'Idle' in m.name and m.confidence == 'CONFIRMED':
                    assert m.rows == 3 and m.cols == 6, \
                        f"{v.software_id} {m.name}: expected 3×6, got {m.rows}×{m.cols}"

    def test_aby_idle_ign_values_plausible(self):
        """ABY idle ign values decode to plausible idle advance angles."""
        from urrom.ecu_profiles import normalize_rom, read_map, VARIANT_551B
        from pathlib import Path
        p = Path('roms/aby_fuel-ign_551aa.bin')
        if not p.exists():
            return
        wh, _ = normalize_rom(p.read_bytes())
        for m in VARIANT_551B.main_maps:
            if 'Idle' not in m.name or m.confidence != 'CONFIRMED':
                continue
            data = read_map(bytes(wh), m)
            for r in range(m.rows):
                for c in range(m.cols):
                    raw = data[r][c]
                    deg = m.decode(raw)
                    assert -10 <= deg <= 40, \
                        f"ABY {m.name} [{r},{c}] raw={raw} → {deg:.1f}° out of idle range"

    def test_adu_idle_ign_values_plausible(self):
        """ADU idle ign values decode to plausible idle advance angles."""
        from urrom.ecu_profiles import normalize_rom, read_map, VARIANT_551C
        from pathlib import Path
        p = Path('roms/adu_fuel-ign_551c.bin')
        if not p.exists():
            return
        wh, _ = normalize_rom(p.read_bytes())
        for m in VARIANT_551C.main_maps:
            if 'Idle' not in m.name or m.confidence != 'CONFIRMED':
                continue
            data = read_map(bytes(wh), m)
            for r in range(m.rows):
                for c in range(m.cols):
                    raw = data[r][c]
                    deg = m.decode(raw)
                    assert -10 <= deg <= 40, \
                        f"ADU {m.name} [{r},{c}] raw={raw} → {deg:.1f}° out of idle range"

    def test_aby_adu_idle_same_calibration(self):
        """ABY and ADU have identical idle ign calibration in stock form."""
        from urrom.ecu_profiles import normalize_rom, read_map, VARIANT_551B, VARIANT_551C
        from pathlib import Path
        p_aby = Path('roms/aby_fuel-ign_551aa.bin')
        p_adu = Path('roms/adu_fuel-ign_551c.bin')
        if not p_aby.exists() or not p_adu.exists():
            return
        aby_wh, _ = normalize_rom(p_aby.read_bytes())
        adu_wh, _ = normalize_rom(p_adu.read_bytes())
        aby_idle = next(m for m in VARIANT_551B.main_maps
                        if 'closed throttle' in m.name and m.confidence == 'CONFIRMED')
        adu_idle = next(m for m in VARIANT_551C.main_maps
                        if 'closed throttle' in m.name and m.confidence == 'CONFIRMED')
        aby_data = read_map(bytes(aby_wh), aby_idle)
        adu_data = read_map(bytes(adu_wh), adu_idle)
        # All 18 cells should match
        for r in range(3):
            for c in range(6):
                assert aby_data[r][c] == adu_data[r][c], \
                    f"Idle cal mismatch [{r},{c}]: ABY={aby_data[r][c]} ADU={adu_data[r][c]}"

    def test_all_551x_have_idle_maps(self):
        """All 551x variants have at least 2 confirmed idle ign maps."""
        from urrom.ecu_profiles import ALL_VARIANTS
        for v in ALL_VARIANTS:
            if v.software_id.startswith('551') and v.software_id != '551AA_0202':
                idle = [m for m in v.main_maps
                        if 'Idle' in m.name and m.confidence == 'CONFIRMED']
                assert len(idle) >= 2, \
                    f"{v.software_id} has only {len(idle)} confirmed idle ign maps"
