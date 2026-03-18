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
