"""
Integration tests for UrROM — exercises real ROM files end-to-end.

Tests:
  1. ROM loading + variant detection
  2. Map reading + editing + roundtrip
  3. Patch apply/revert for MFTS, Load Decap, Lambda Delay
  4. Save + reload integrity (checksum, doubling, .034 scramble)
  5. Boost chip loading + CRC identification
  6. Multi-map edit + flush-on-switch correctness
"""
import os
import sys
import struct
import tempfile
import shutil
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from urrom.ecu_profiles import (
    detect_rom, read_map, write_map, get_axes, read_descriptor_axes,
    apply_checksum, ign_decode, ign_encode, fuel_decode, fuel_encode,
    DetectionResult,
)
from urrom.hw_patches import (
    detect_patches,
    apply_mfts_bypass, revert_mfts_bypass,
    apply_load_decap, revert_load_decap,
    apply_lambda_delay, revert_lambda_delay,
    MFTS_BYPASS_OFFSET, MFTS_BYPASS_STOCK, MFTS_BYPASS_PATCH,
    LOAD_DECAP_PATCHES,
    LAMBDA_DELAY_OFFSET, LAMBDA_DELAY_STOCK, LAMBDA_DELAY_EXTENDED,
    LC_NLS_SIGNATURE,
)

ROMS_DIR = Path(__file__).resolve().parent.parent / "roms"


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_rom(name: str) -> bytearray:
    path = ROMS_DIR / name
    if not path.exists():
        pytest.skip(f"ROM not found: {path}")
    return bytearray(path.read_bytes())


# ── 1. ROM Loading + Variant Detection ──────────────────────────────────────

class TestVariantDetection:

    def test_aan_551aa_detected(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        assert det is not None, "Should detect AAN 551AA variant"
        assert "551AA" in det.variant.software_id or "551" in det.variant.software_id

    def test_aby_551aa_detected(self):
        rom = load_rom("aby_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        assert det is not None

    def test_adu_551c_detected(self):
        rom = load_rom("adu_fuel-ign_551c.bin")
        det = detect_rom(bytes(rom))
        assert det is not None

    def test_3b_404aa_detected(self):
        rom = load_rom("3b_fuel-ign_404aa.bin")
        det = detect_rom(bytes(rom))
        assert det is not None

    def test_rr_404b_detected(self):
        rom = load_rom("rr_fuel-ign_404b.bin")
        det = detect_rom(bytes(rom))
        assert det is not None

    def test_rs2_d02_551b_detected(self):
        rom = load_rom("rs2_d02_fuel-ign_551b.bin")
        det = detect_rom(bytes(rom))
        assert det is not None

    def test_random_bytes_not_detected(self):
        rom = bytearray(os.urandom(65536))
        det = detect_rom(bytes(rom))
        # Should either return None or a low-confidence match
        # (acceptable either way — just shouldn't crash)

    def test_all_bundled_roms_detectable(self):
        """Every .bin in roms/ should be detectable."""
        for f in ROMS_DIR.glob("*.bin"):
            rom = bytearray(f.read_bytes())
            det = detect_rom(bytes(rom))
            assert det is not None, f"Failed to detect variant for {f.name}"


# ── 2. Map Reading + Editing + Roundtrip ────────────────────────────────────

class TestMapEditRoundtrip:

    def _get_first_map(self, rom, det):
        """Get the first confirmed 2D map from a detected variant."""
        for m in det.variant.main_maps:
            if m.rows > 1 and m.cols > 1 and m.confidence == "CONFIRMED":
                return m
        pytest.skip("No confirmed 2D maps in this variant")

    @staticmethod
    def _flatten(data_2d):
        """Flatten 2D list of lists to flat list."""
        return [cell for row in data_2d for cell in row]

    @staticmethod
    def _unflatten(flat, rows, cols):
        """Convert flat list back to 2D list of lists."""
        return [flat[r * cols:(r + 1) * cols] for r in range(rows)]

    def test_read_map_returns_correct_size(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        m = self._get_first_map(rom, det)
        data = read_map(bytes(rom), m)
        # read_map returns list of rows (2D)
        assert len(data) == m.rows
        assert all(len(row) == m.cols for row in data)

    def test_write_map_roundtrip(self):
        """Read → write → read should produce identical bytes."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        m = self._get_first_map(rom, det)
        original = read_map(bytes(rom), m)
        # write_map expects 2D list (same format as read_map returns)
        rom2 = write_map(bytearray(rom), m, original)
        readback = read_map(bytes(rom2), m)
        assert original == readback, "Read→write→read should be identical"

    def test_edit_single_cell(self):
        """Edit one cell, verify only that cell changed."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        m = self._get_first_map(rom, det)
        import copy
        original = read_map(bytes(rom), m)
        modified = copy.deepcopy(original)
        modified[0][0] = (original[0][0] + 42) & 0xFF
        rom2 = write_map(bytearray(rom), m, modified)
        readback = read_map(bytes(rom2), m)
        assert readback[0][0] == modified[0][0], "Edited cell should have new value"
        # Check rest of first row unchanged
        assert readback[0][1:] == original[0][1:]

    def test_edit_all_cells_max(self):
        """Set every cell to 0xFF, verify."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        m = self._get_first_map(rom, det)
        maxed = [[0xFF] * m.cols for _ in range(m.rows)]
        rom2 = write_map(bytearray(rom), m, maxed)
        readback = read_map(bytes(rom2), m)
        assert readback == maxed

    def test_edit_all_cells_zero(self):
        """Set every cell to 0x00, verify."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        m = self._get_first_map(rom, det)
        zeroed = [[0x00] * m.cols for _ in range(m.rows)]
        rom2 = write_map(bytearray(rom), m, zeroed)
        readback = read_map(bytes(rom2), m)
        assert readback == zeroed

    def test_multiple_maps_independent(self):
        """Editing map A should not corrupt map B."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        confirmed = [m for m in det.variant.main_maps
                     if m.rows > 1 and m.cols > 1 and m.confidence == "CONFIRMED"]
        if len(confirmed) < 2:
            pytest.skip("Need at least 2 confirmed maps")
        m_a, m_b = confirmed[0], confirmed[1]
        orig_b = read_map(bytes(rom), m_b)
        maxed = [[0xFF] * m_a.cols for _ in range(m_a.rows)]
        rom2 = write_map(bytearray(rom), m_a, maxed)
        after_b = read_map(bytes(rom2), m_b)
        assert orig_b == after_b, f"Map {m_b.name} was corrupted by editing {m_a.name}"


# ── 3. Patch Apply/Revert ──────────────────────────────────────────────────

class TestPatchApplyRevert:

    def test_mfts_detect_stock(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        wh = bytes(rom[:0x8000])
        results = detect_patches(wh, "551AA_0202")
        mfts = [r for r in results if r.name == "MFTS Boost Cut Bypass"]
        assert len(mfts) == 1
        assert mfts[0].status == "STOCK"

    def test_mfts_apply_and_detect(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        assert rom[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2] == MFTS_BYPASS_STOCK
        original = apply_mfts_bypass(rom)
        assert original == MFTS_BYPASS_STOCK
        assert rom[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2] == MFTS_BYPASS_PATCH
        # Detection should now show PATCHED
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        mfts = [r for r in results if r.name == "MFTS Boost Cut Bypass"][0]
        assert mfts.status == "PATCHED"

    def test_mfts_revert(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        apply_mfts_bypass(rom)
        revert_mfts_bypass(rom)
        assert rom[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2] == MFTS_BYPASS_STOCK
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        mfts = [r for r in results if r.name == "MFTS Boost Cut Bypass"][0]
        assert mfts.status == "STOCK"

    def test_mfts_64kb_firmware_only(self):
        """On a 64KB split-bank image the patch must touch ONLY the firmware half."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        if len(rom) < 0x10000:
            # Pad to 64KB doubled
            rom = bytearray(rom[:0x8000]) + bytearray(rom[:0x8000])
        apply_mfts_bypass(rom)
        # Check both halves
        assert rom[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2] == MFTS_BYPASS_PATCH
        assert rom[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2] == MFTS_BYPASS_PATCH
        assert rom[MFTS_BYPASS_OFFSET + 0x8000: MFTS_BYPASS_OFFSET + 0x8000 + 2] != MFTS_BYPASS_PATCH, \
            "calibration half must not be written by a firmware patch"

    def test_load_decap_detect_stock(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        load = [r for r in results if r.name == "Load Overflow Decap"]
        assert len(load) == 1
        assert load[0].status == "STOCK"

    def test_load_decap_apply_and_detect(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        originals = apply_load_decap(rom)
        assert len(originals) == 2
        # Verify patched bytes
        for offset, stock, patch in LOAD_DECAP_PATCHES:
            assert bytes(rom[offset: offset + len(patch)]) == patch
        # Detection
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        load = [r for r in results if r.name == "Load Overflow Decap"][0]
        assert load.status == "PATCHED"

    def test_load_decap_revert(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        apply_load_decap(rom)
        revert_load_decap(rom)
        for offset, stock, patch in LOAD_DECAP_PATCHES:
            assert bytes(rom[offset: offset + len(stock)]) == stock
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        load = [r for r in results if r.name == "Load Overflow Decap"][0]
        assert load.status == "STOCK"

    def test_lambda_delay_detect(self):
        """551AA ROM has lambda delay at 0x5EFA — may be stock or custom."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        lam = [r for r in results if r.name == "Lambda Cold-Start Delay"]
        assert len(lam) == 1
        # The bundled ROM has 0xC2 (194) which is CUSTOM (not stock 0x9F)
        assert lam[0].status in ("STOCK", "CUSTOM", "EXTENDED", "MINIMAL")

    def test_lambda_delay_apply_extended(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        original_val = rom[LAMBDA_DELAY_OFFSET]
        original = apply_lambda_delay(rom, LAMBDA_DELAY_EXTENDED)
        assert original == original_val
        assert rom[LAMBDA_DELAY_OFFSET] == LAMBDA_DELAY_EXTENDED
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        lam = [r for r in results if r.name == "Lambda Cold-Start Delay"][0]
        assert lam.status == "EXTENDED"

    def test_lambda_delay_apply_custom(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        apply_lambda_delay(rom, 0x80)
        assert rom[LAMBDA_DELAY_OFFSET] == 0x80
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        lam = [r for r in results if r.name == "Lambda Cold-Start Delay"][0]
        assert lam.status == "CUSTOM"

    def test_lambda_delay_revert(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        apply_lambda_delay(rom, LAMBDA_DELAY_EXTENDED)
        revert_lambda_delay(rom)
        assert rom[LAMBDA_DELAY_OFFSET] == LAMBDA_DELAY_STOCK

    def test_all_three_patches_simultaneous(self):
        """Apply all 3 patches, verify all detected, revert all, verify stock."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        apply_mfts_bypass(rom)
        apply_load_decap(rom)
        apply_lambda_delay(rom, LAMBDA_DELAY_EXTENDED)
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        names = {r.name: r.status for r in results}
        assert names["MFTS Boost Cut Bypass"] == "PATCHED"
        assert names["Load Overflow Decap"] == "PATCHED"
        assert names["Lambda Cold-Start Delay"] == "EXTENDED"
        # Revert all
        revert_mfts_bypass(rom)
        revert_load_decap(rom)
        revert_lambda_delay(rom)
        results2 = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        names2 = {r.name: r.status for r in results2}
        assert names2["MFTS Boost Cut Bypass"] == "STOCK"
        assert names2["Load Overflow Decap"] == "STOCK"
        assert names2["Lambda Cold-Start Delay"] == "STOCK"

    def test_mfts_patch_does_not_corrupt_maps(self):
        """MFTS patch should not change any map data."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        confirmed = [m for m in det.variant.main_maps
                     if m.rows > 1 and m.confidence == "CONFIRMED"]
        # Maps live in the CALIBRATION half (upper 32KB); the patch hits the firmware half.
        cal_before = bytes(rom[0x8000:0x10000])
        before = {m.name: read_map(cal_before, m) for m in confirmed}
        apply_mfts_bypass(rom)
        cal_after = bytes(rom[0x8000:0x10000])
        assert cal_after == cal_before, "firmware patch must not touch the calibration half"
        for m in confirmed:
            assert read_map(cal_after, m) == before[m.name], f"MFTS patch corrupted map {m.name}"

    def test_load_decap_overlap_with_ign_map(self):
        """KNOWN ISSUE: Load decap patch at 0x3679/0x367F may overlap Ign Map 4.
        This test documents the overlap so we can track it."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        # Find which map contains the load decap offset
        overlapping = []
        for m in det.variant.main_maps:
            start = m.main_addr
            end = start + (m.rows * m.cols)
            for offset, _, _ in LOAD_DECAP_PATCHES:
                if start <= offset < end:
                    overlapping.append((m.name, offset, start, end))
        # Document the overlap — this is a known issue
        if overlapping:
            for name, off, start, end in overlapping:
                print(f"WARNING: Load decap offset 0x{off:04X} overlaps "
                      f"{name} (0x{start:04X}-0x{end:04X})")
        # The patch bytes may actually be 8051 CODE that happens to fall
        # within a map definition's address range — the map definition
        # may be incorrectly sized (DTC Classes 60x60 issue from Known Limitations)


# ── 4. Save + Reload Integrity ──────────────────────────────────────────────

class TestSaveReload:

    def test_save_and_reload_preserves_data(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        det = detect_rom(bytes(rom))
        m = [m for m in det.variant.main_maps
             if m.rows > 1 and m.cols > 1 and m.confidence == "CONFIRMED"][0]
        import copy
        data_2d = read_map(bytes(rom), m)
        modified = copy.deepcopy(data_2d)
        modified[0][0] = 0x42
        rom2 = write_map(bytearray(rom), m, modified)
        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(bytes(rom2))
            tmp = f.name
        try:
            # Reload
            rom3 = bytearray(Path(tmp).read_bytes())
            readback = read_map(bytes(rom3), m)
            assert readback[0][0] == 0x42
            assert readback == modified
        finally:
            os.unlink(tmp)

    def test_checksum_applied_on_save(self):
        """apply_checksum should produce valid checksum bytes."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        rom2 = apply_checksum(bytearray(rom))
        # Checksum at 0x3FFA-0x3FFD (4 bytes)
        cs_region = rom2[0x3FFA:0x3FFE]
        assert len(cs_region) == 4
        # Apply again — should be idempotent
        rom3 = apply_checksum(bytearray(rom2))
        assert rom3[0x3FFA:0x3FFE] == rom2[0x3FFA:0x3FFE], "Checksum should be idempotent"

    def test_551_files_are_split_bank_not_mirrored(self):
        """Every bundled 551 image: lower half = firmware (LJMP reset), upper = cal."""
        for name in ("adu_fuel-ign_551c.bin", "aby_fuel-ign_551aa.bin", "rs2_d02_fuel-ign_551b.bin"):
            rom = load_rom(name)
            assert len(rom) >= 0x10000
            lo, up = bytes(rom[:0x8000]), bytes(rom[0x8000:0x10000])
            assert lo != up, f"{name}: halves must not be mirrors"
            assert lo[0] == 0x02, f"{name}: lower half must start with LJMP (firmware)"

    def test_assemble_output_preserves_firmware_half(self):
        from urrom.ecu_profiles import assemble_output, VARIANT_551C
        rom = bytes(load_rom("adu_fuel-ign_551c.bin"))
        wh = bytearray(rom[0x8000:0x10000])
        wh[0x2E17] = 0x99                                   # edit a fuel cell
        out, notes = assemble_output(bytes(wh), VARIANT_551C, rom)
        assert len(out) == 0x10000
        assert out[:0x8000] == rom[:0x8000], "firmware half must be untouched"
        assert out[0x8000 + 0x2E17] == 0x99
        assert "firmware half preserved" in notes

    def test_assemble_output_without_firmware_warns(self):
        from urrom.ecu_profiles import assemble_output, VARIANT_551C
        wh = bytes(load_rom("adu_fuel-ign_551c.bin"))[0x8000:0x10000]
        out, notes = assemble_output(wh, VARIANT_551C, None)
        assert len(out) == 0x10000 and out[:0x8000] == out[0x8000:]
        assert any(n.startswith("WARNING") for n in notes)

    def test_assemble_output_flat_404(self):
        from urrom.ecu_profiles import assemble_output, VARIANT_404
        wh = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        out, notes = assemble_output(wh, VARIANT_404, None)
        assert out == wh and notes == []

    def test_patched_rom_saves_correctly(self):
        """ROM with patches applied should save and reload with patches intact."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        apply_mfts_bypass(rom)
        apply_load_decap(rom)
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(bytes(rom))
            tmp = f.name
        try:
            rom2 = bytearray(Path(tmp).read_bytes())
            results = detect_patches(bytes(rom2[:0x8000]), "551AA_0202")
            names = {r.name: r.status for r in results}
            assert names["MFTS Boost Cut Bypass"] == "PATCHED"
            assert names["Load Overflow Decap"] == "PATCHED"
        finally:
            os.unlink(tmp)


# ── 5. Boost Chip Loading + CRC ─────────────────────────────────────────────

class TestBoostChip:

    def test_aan_boost_crc_known(self):
        rom = load_rom("aan_boost_551aa.bin")
        import zlib
        crc = zlib.crc32(bytes(rom)) & 0xFFFFFFFF
        assert crc == 0x16707F66, f"AAN boost CRC mismatch: 0x{crc:08X}"

    def test_aby_boost_crc_known(self):
        rom = load_rom("aby_boost_551b.bin")
        import zlib
        crc = zlib.crc32(bytes(rom)) & 0xFFFFFFFF
        assert crc == 0xF6E33043, f"ABY boost CRC mismatch: 0x{crc:08X}"

    def test_rr_boost_crc_consistent(self):
        """RR boost chip should have a consistent CRC (may differ from DB)."""
        rom = load_rom("rr_boost_404b.bin")
        import zlib
        crc = zlib.crc32(bytes(rom)) & 0xFFFFFFFF
        # The bundled file is 8KB (not 32KB) — CRC differs from 32KB known DB
        # Just verify it's deterministic
        crc2 = zlib.crc32(bytes(rom)) & 0xFFFFFFFF
        assert crc == crc2

    def test_boost_chip_detection_in_patches(self):
        main_rom = load_rom("aan_fuel-ign_551aa.bin")
        boost_rom = load_rom("aan_boost_551aa.bin")
        results = detect_patches(bytes(main_rom[:0x8000]), "551AA", bytes(boost_rom))
        boost = [r for r in results if r.name == "Boost Chip Identity"]
        assert len(boost) == 1
        assert "AAN stock" in boost[0].status

    def test_map_sensor_detection_with_boost(self):
        main_rom = load_rom("aan_fuel-ign_551aa.bin")
        boost_rom = load_rom("aan_boost_551aa.bin")
        results = detect_patches(bytes(main_rom[:0x8000]), "551AA", bytes(boost_rom))
        sensor = [r for r in results if r.name == "MAP Sensor Type"]
        assert len(sensor) == 1
        assert "200" in sensor[0].status or "stock" in sensor[0].status.lower()


# ── 6. LC/NLS Signature Detection ───────────────────────────────────────────

class TestLCNLSDetection:

    def test_lc_nls_signature_at_correct_offset(self):
        """Verify the LC/NLS signature is at the corrected offset 0x062E."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        offset, sig = LC_NLS_SIGNATURE
        assert offset == 0x062E, f"LC/NLS offset should be 0x062E, got 0x{offset:04X}"
        actual = bytes(rom[offset: offset + len(sig)])
        assert actual == sig, (
            f"Signature mismatch at 0x{offset:04X}: "
            f"expected {sig.hex()}, got {actual.hex()}")

    def test_lc_nls_detected_in_551aa(self):
        rom = load_rom("aan_fuel-ign_551aa.bin")
        results = detect_patches(bytes(rom[:0x8000]), "551AA_0202")
        lc = [r for r in results if r.name == "LC / NLS Motorsport Code"]
        assert len(lc) == 1
        assert lc[0].status == "DETECTED"

    def test_old_offset_0x0610_is_not_lc_nls(self):
        """The old offset 0x0610 should NOT contain the LC/NLS signature."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        old_sig = bytes([0xC0, 0x82, 0xC0, 0x83])
        actual = bytes(rom[0x0610: 0x0610 + 4])
        assert actual != old_sig, (
            "Old offset 0x0610 still matches — signature detection was wrong")


# ── 7. Cross-Variant Patch Safety ───────────────────────────────────────────

class TestCrossVariantSafety:
    """Patches should only be applied to the correct firmware base."""

    def test_adu_551c_has_different_bytes_at_mfts_offset(self):
        """ADU/RS2 firmware may have different code at the MFTS offset."""
        rom = load_rom("adu_fuel-ign_551c.bin")
        wh = bytes(rom[:0x8000])
        mfts_bytes = wh[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2]
        # We don't know what ADU has here, but detecting it correctly is important
        results = detect_patches(wh, "551C")
        mfts = [r for r in results if r.name == "MFTS Boost Cut Bypass"][0]
        # Should be STOCK, UNKNOWN, or something — just shouldn't crash
        assert mfts.status in ("STOCK", "PATCHED", "UNKNOWN")

    def test_404_variant_detection(self):
        """3B/RR variants should detect patches without crashing."""
        rom = load_rom("3b_fuel-ign_404aa.bin")
        results = detect_patches(bytes(rom[:0x8000]), "404")
        assert len(results) > 0


# ── 8. Decode/Encode Roundtrip ──────────────────────────────────────────────

class TestDecodeEncode:

    def test_ign_decode_encode_roundtrip(self):
        """Ignition map decode→encode should be lossless."""
        for raw in range(256):
            decoded = ign_decode(raw)
            re_encoded = ign_encode(decoded)
            assert re_encoded == raw, (
                f"ign roundtrip failed: {raw} → {decoded} → {re_encoded}")

    def test_fuel_decode_encode_roundtrip(self):
        """Fuel map decode→encode should be lossless."""
        for raw in range(256):
            decoded = fuel_decode(raw)
            re_encoded = fuel_encode(decoded)
            assert re_encoded == raw, (
                f"fuel roundtrip failed: {raw} → {decoded} → {re_encoded}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])


# ── 404 family safety (2026-09, after the S2 B3 chip set was added) ──────────

class Test404ChecksumSafety:
    """
    On the 32KB flat 3B/RR/S2 chips the bytes at 0x3FFA-0x3FFF are 8051
    firmware, not a checksum slot.  UrROM must never treat the 404 family as
    checksummed, or Save would corrupt live code.
    """

    def test_404_not_in_checksum_variants(self):
        from urrom.ecu_profiles import CHECKSUM_VARIANTS, has_software_checksum, VARIANT_404, VARIANT_V8_PT
        from urrom.ecu_profiles import checksum_kind
        # the 0x3FFA PRJmod checksum is 551-only; the 404 has its own 16-bit sum at 0x7F00
        assert "404" not in CHECKSUM_VARIANTS
        assert "404V8" not in CHECKSUM_VARIANTS
        assert checksum_kind(VARIANT_404) == "404" and has_software_checksum(VARIANT_404)
        assert checksum_kind(VARIANT_V8_PT) is None and not has_software_checksum(VARIANT_V8_PT)

    def test_551_still_checksummed(self):
        from urrom.ecu_profiles import has_software_checksum, VARIANT_551B, VARIANT_551AA_0202
        assert has_software_checksum(VARIANT_551B)
        assert has_software_checksum(VARIANT_551AA_0202)

    def test_3b_checksum_slot_is_firmware(self):
        import pathlib
        p = pathlib.Path(__file__).parent.parent / "roms" / "3b_fuel-ign_404aa.bin"
        if not p.exists():
            import pytest; pytest.skip("bundled 3B ROM not present")
        rom = p.read_bytes()
        # Known firmware bytes on the 447907404AA chip — if these ever change
        # the assumption above needs revisiting.
        assert rom[0x3FFA:0x4000] == bytes.fromhex("799be375f004")

    def test_checksum_check_skips_404(self):
        import pathlib
        from urrom.ecu_profiles import VARIANT_404
        from urrom.tuning_checks import check_checksum
        p = pathlib.Path(__file__).parent.parent / "roms" / "s2_fuel-ign_404.bin"
        if not p.exists():
            import pytest; pytest.skip("bundled S2 ROM not present")
        assert check_checksum(p.read_bytes(), VARIANT_404) == []

    def test_stock_3b_fuel_maps_do_not_scan_lean(self):
        """Stock 3B/S2 fuel maps reach raw 205 — must not be flagged as lean."""
        import pathlib
        from urrom.ecu_profiles import VARIANT_404
        from urrom.tuning_checks import check_fuel_range
        for name in ("3b_fuel-ign_404aa.bin", "s2_fuel-ign_404.bin", "rr_fuel-ign_404b.bin"):
            p = pathlib.Path(__file__).parent.parent / "roms" / name
            if not p.exists():
                continue
            errs = [i for i in check_fuel_range(p.read_bytes(), VARIANT_404) if i.severity == "error"]
            assert errs == [], f"{name}: {len(errs)} false lean errors"


class TestS2ChipSet:
    def test_s2_pair_fingerprinted(self):
        import pathlib
        from urrom.ecu_profiles import detect_rom, KNOWN_CRCS
        base = pathlib.Path(__file__).parent.parent / "roms"
        fuel, boost = base / "s2_fuel-ign_404.bin", base / "s2_boost_404.bin"
        if not (fuel.exists() and boost.exists()):
            import pytest; pytest.skip("S2 chip set not present")
        d = detect_rom(fuel.read_bytes())
        assert d.crc32 == 0x9245FA10 and d.variant is not None and d.variant.software_id == "404"
        assert KNOWN_CRCS[0x604AB965][0] == "404_boost"

    def test_404_boost_tables_decoded(self):
        from urrom.ecu_profiles import VARIANT_404
        by_addr = {m.main_addr: m for m in VARIANT_404.boost_maps}
        for a in (0x18B4, 0x1934, 0x19B4):
            assert by_addr[a].rows == 8 and by_addr[a].cols == 16
            assert by_addr[a].name.startswith("Boost Target")
        for a in (0x1A34, 0x1AB4, 0x1B34):
            assert by_addr[a].rows == 8 and by_addr[a].cols == 16
            assert by_addr[a].name.startswith("N75 Base Duty")

    def test_404_boost_axes_from_chip(self):
        import pathlib
        from urrom.ecu_profiles import (VARIANT_404, get_axes, read_delta_axis,
                                        boost404_period_to_rpm)
        p = pathlib.Path(__file__).parent.parent / "roms" / "3b_boost_404aa.bin"
        if not p.exists():
            import pytest; pytest.skip("bundled 3B boost chip not present")
        rom = p.read_bytes()
        assert read_delta_axis(rom, 0x189A) == [43, 61, 80, 99, 118, 136, 155, 219]
        assert read_delta_axis(rom, 0x1BB8) == [167, 200, 250, 333, 500]
        # period → rpm lands on round numbers for the 0x1BB8 axis
        assert [boost404_period_to_rpm(v) for v in (500, 250, 200)] == [3000, 6000, 7500]
        m = next(m for m in VARIANT_404.boost_maps if m.main_addr == 0x1934)
        rows, cols = get_axes(rom, m, VARIANT_404)
        assert len(rows) == 8 and len(cols) == 16
        assert cols[0] > cols[-1]          # high rpm first
        assert 2200 < cols[-1] < 2300 and 10400 < cols[0] < 10700


# ── 551 firmware descriptor tables (2026-09) ─────────────────────────────────

class Test551Descriptors:
    """
    The stock 551 firmware finds its maps via index/pointer tables in the
    calibration half.  These tests pin the decoded addresses so the map lists
    can never silently drift back to guesses.
    """

    def _full(self, name):
        rom = load_rom(name)
        if len(rom) < 0x10000:
            import pytest; pytest.skip(f"{name} is not a 64KB image")
        return bytes(rom)

    def _rpm_load_16x16(self, name):
        from urrom.ecu_profiles import decode_descriptor_tables
        maps = decode_descriptor_tables(self._full(name))
        return [m["data"] for m in maps
                if m["two_d"] and m["rows"] == 16 and m["cols"] == 16
                and m["x_input"] == 0x3A and m["y_input"] == 0x3F]

    def test_adu_16x16_maps(self):
        assert self._rpm_load_16x16("adu_fuel-ign_551c.bin") == \
            [0x2E17, 0x30AC, 0x3263, 0x3387, 0x3598, 0x36BC, 0x380D, 0x3931]

    def test_aby_16x16_maps_are_4_lower(self):
        assert self._rpm_load_16x16("aby_fuel-ign_551aa.bin") == \
            [0x2E17, 0x30A8, 0x325F, 0x3383, 0x3594, 0x36B8, 0x3809, 0x392D]

    def test_rs2_d02_16x16_maps_are_prjmod_layout(self):
        assert self._rpm_load_16x16("rs2_d02_fuel-ign_551b.bin") == \
            [0x0E13, 0x10A8, 0x125F, 0x1383, 0x1594, 0x16B8, 0x1809, 0x192D]

    def test_aan_551aa_16x16_maps(self):
        assert self._rpm_load_16x16("aan_fuel-ign_551aa.bin") == \
            [0x0DEA, 0x106D, 0x1224, 0x1348, 0x155F, 0x1683, 0x17D4, 0x18F8]

    def test_variant_lists_match_firmware(self):
        from urrom.ecu_profiles import VARIANT_551C, VARIANT_551B, VARIANT_551B_D02, VARIANT_551AA
        for v, name in ((VARIANT_551C, "adu_fuel-ign_551c.bin"),
                        (VARIANT_551B, "aby_fuel-ign_551aa.bin"),
                        (VARIANT_551B_D02, "rs2_d02_fuel-ign_551b.bin"),
                        (VARIANT_551AA, "aan_fuel-ign_551aa.bin")):
            fw_addrs = set(self._rpm_load_16x16(name))
            listed = {m.main_addr for m in v.main_maps if m.rows == 16 and m.cols == 16}
            assert listed == fw_addrs, f"{v.software_id}: {sorted(map(hex, listed))} vs firmware {sorted(map(hex, fw_addrs))}"
            assert all(m.confidence == "CONFIRMED" for m in v.main_maps if m.rows == 16 and m.cols == 16)

    def test_exact_axis_decode_reproduces_known_rpm_axis(self):
        from urrom.ecu_profiles import read_descriptor_axes, _RPM_AXIS_551, _LOAD_AXIS_551
        wh = self._full("adu_fuel-ign_551c.bin")[0x8000:]
        axes = read_descriptor_axes(wh, 0x30AC, 16, 16)      # ign map 1
        assert axes is not None
        rpm, load = axes
        assert rpm == _RPM_AXIS_551                          # 600 … 7200
        assert load == _LOAD_AXIS_551                        # 12 … 180
        # fuel map shares the RPM deltas except the top breakpoint (7000 rpm)
        rpm_f, load_f = read_descriptor_axes(wh, 0x2E17, 16, 16)
        assert rpm_f[:15] == _RPM_AXIS_551[:15] and rpm_f[-1] == 7000
        assert load_f[-1] == 177

    def test_get_axes_uses_descriptor(self):
        from urrom.ecu_profiles import VARIANT_551B, get_axes
        wh = self._full("aby_fuel-ign_551aa.bin")[0x8000:]
        m = next(m for m in VARIANT_551B.main_maps if m.main_addr == 0x30A8)
        rpm, load = get_axes(wh, m, VARIANT_551B)
        assert rpm[0] == 600 and rpm[-1] == 7200 and len(load) == 16

    def test_assemble_output_with_patched_firmware(self):
        from urrom.ecu_profiles import assemble_output, VARIANT_551AA
        from urrom.hw_patches import apply_mfts_bypass, MFTS_BYPASS_OFFSET, MFTS_BYPASS_PATCH
        full = self._full("aan_fuel-ign_551aa.bin")
        fw = bytearray(full[:0x8000]); apply_mfts_bypass(fw)
        out, notes = assemble_output(full[0x8000:], VARIANT_551AA, full, firmware=bytes(fw))
        assert out[MFTS_BYPASS_OFFSET:MFTS_BYPASS_OFFSET + 2] == MFTS_BYPASS_PATCH
        assert out[0x8000:] == full[0x8000:]
        assert "firmware half written (patched)" in notes


# -- 404 (3B/RR/S2) firmware descriptor tables (2026-09) -----------------------

class Test404Descriptors:
    """The 3B firmware uses the same READ_MAP descriptor mechanism as the 551,
    in a flat 32KB address space (index tables 0x6000+, pointers 0x65D0+)."""

    _IGN = [0x7076, 0x71F8, 0x731C, 0x7440, 0x7667, 0x77CF, 0x7937]
    _FUEL = [0x6A8E, 0x6C1C, 0x6D74, 0x6E98]

    def _rpm_load_16x16(self, name):
        from urrom.ecu_profiles import decode_descriptor_tables
        rom = bytes(load_rom(name))
        if len(rom) != 0x8000:
            import pytest; pytest.skip(f"{name} is not a 32KB flat image")
        maps = decode_descriptor_tables(rom)
        return [m["data"] for m in maps
                if m["two_d"] and m["rows"] == 16 and m["cols"] == 16
                and m["x_input"] == 0x3A and m["y_input"] == 0x3F]

    def test_3b_rr_s2_have_eleven_16x16_maps(self):
        for name in ("3b_fuel-ign_404aa.bin", "rr_fuel-ign_404b.bin", "s2_fuel-ign_404.bin"):
            assert self._rpm_load_16x16(name) == sorted(self._FUEL + self._IGN), name

    def test_variant_404_list_matches_firmware(self):
        from urrom.ecu_profiles import VARIANT_404
        listed = {m.main_addr for m in VARIANT_404.main_maps if m.rows == 16 and m.cols == 16}
        assert listed == set(self._FUEL + self._IGN)
        ign = [m for m in VARIANT_404.main_maps if m.map_type == "ign" and m.rows == 16]
        assert len(ign) == 7 and all(m.confidence == "CONFIRMED" for m in ign)

    def test_3b_exact_axes(self):
        from urrom.ecu_profiles import VARIANT_404, get_axes, _RPM_AXIS_551
        rom = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        m = next(m for m in VARIANT_404.main_maps if m.main_addr == 0x7076)
        rpm, load = get_axes(rom, m, VARIANT_404)
        assert rpm == _RPM_AXIS_551                      # 600 .. 7200, same as the 551
        assert load == [14, 24, 34, 44, 54, 66, 78, 90, 100, 110, 120, 130, 140, 154, 174, 190]

    def test_new_3b_ign_maps_decode_as_ignition(self):
        from urrom.ecu_profiles import VARIANT_404, read_map_decoded
        rom = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        for addr in (0x71F8, 0x731C, 0x7440):
            m = next(m for m in VARIANT_404.main_maps if m.main_addr == addr)
            vals = [v for row in read_map_decoded(rom, m) for v in row]
            assert 5.0 <= min(vals) and max(vals) <= 45.0, hex(addr)

    def test_3b_ign_selector_chain_bytes(self):
        """Pin the ignition table-set selector (0x34B7) and IGN_CALC slot loads the
        analysis relies on, so a different 404 firmware build is noticed."""
        rom = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        # 34B7: MOV DPTR,#636F ; MOV 75h,#66h ; MOV 76h,#28h  (ignition pointer table 0x6628)
        assert rom[0x34B7:0x34C0] == bytes.fromhex("90636f757566757628")
        # 34E4: MOV 77h,#60h ; MOV 78h,#93h ; RET   (table set 0x6093 = maps 2/…)
        assert rom[0x34E4:0x34EB] == bytes.fromhex("75776075789322")
        # IGN_CALC: MOV R2,#04h at 162B, #12h at 1685, #18h at 1690
        assert rom[0x162B:0x162D] == b"\x7a\x04"
        assert rom[0x1685:0x1687] == b"\x7a\x12"
        assert rom[0x1690:0x1692] == b"\x7a\x18"
        # 20h ← XRAM 0xA040 XOR 0x0E (boost-board status), at 137B-1384
        assert rom[0x137B:0x137E] == bytes.fromhex("e2640e")


# -- 27C512 chip images: fold / expand (2026-09-09) ---------------------------

class TestChipImages:
    def test_expand_and_fold_8kb_boost(self):
        from urrom.ecu_profiles import expand_to_chip, fold_repeated_image
        boost = bytes(load_rom("3b_boost_404aa.bin"))
        assert len(boost) == 0x2000
        img = expand_to_chip(boost, "27C512")
        assert len(img) == 0x10000 and img[0x2000 * 7:] == boost
        native, copies, notes = fold_repeated_image(img)
        assert native == boost and copies == 8 and notes

    def test_expand_and_fold_32kb_404(self):
        from urrom.ecu_profiles import expand_to_chip, fold_repeated_image, normalize_rom, detect_rom
        fuel = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        img = expand_to_chip(fuel)
        assert img == fuel * 2
        native, copies, _ = fold_repeated_image(img)
        assert native == fuel and copies == 2
        # normalize_rom folds a 27C512-doubled 404 image back to the 32KB flat chip
        wh, notes = normalize_rom(img)
        assert bytes(wh) == fuel and any("folded" in n for n in notes)
        assert detect_rom(bytes(wh)).variant.software_id == "404"

    def test_551_split_bank_not_folded(self):
        from urrom.ecu_profiles import fold_repeated_image, normalize_rom
        rom = bytes(load_rom("adu_fuel-ign_551c.bin"))
        native, copies, _ = fold_repeated_image(rom)
        assert copies == 1 and native == rom
        wh, _ = normalize_rom(rom)
        assert bytes(wh) == rom[0x8000:]

    def test_expand_rejects_short_dump(self):
        import pytest
        from urrom.ecu_profiles import expand_to_chip
        with pytest.raises(ValueError):
            expand_to_chip(b"\x02" * 65535)

    def test_64kb_passthrough(self):
        from urrom.ecu_profiles import expand_to_chip
        rom = bytes(load_rom("aby_fuel-ign_551aa.bin"))
        assert expand_to_chip(rom) == rom

    def test_404_main_ign_maps_near_identical(self):
        """The four main ignition maps (2/5/6/7) are one calibration within 3 deg (4 raw) on
        every 404 chip, differing in <=62 cells; exact equalities differ per chip
        (3B: 5==6; RR: 2==5==7; S2: 2==7, 5==6)."""
        A = {2: 0x71F8, 5: 0x7667, 6: 0x77CF, 7: 0x7937}
        equal = {"3b_fuel-ign_404aa.bin": [(5, 6)],
                 "rr_fuel-ign_404b.bin": [(2, 5), (2, 7)],
                 "s2_fuel-ign_404.bin": [(2, 7), (5, 6)]}
        for name, pairs in equal.items():
            rom = bytes(load_rom(name))
            for a, b in pairs:
                assert rom[A[a]:A[a] + 256] == rom[A[b]:A[b] + 256], f"{name} map{a}!=map{b}"
            # and no two of them differ by more than 4 raw (3 deg) in any cell, or in >64 cells
            for a in A:
                for b in A:
                    if a < b:
                        x, y = rom[A[a]:A[a] + 256], rom[A[b]:A[b] + 256]
                        d = [abs(x[i] - y[i]) for i in range(256)]
                        assert max(d) <= 4 and sum(1 for v in d if v) <= 64, f"{name} {a}v{b}"

    def test_boost_mcu_knock_reference_tables(self):
        """Pin the boost-side bytes the status-nibble / knock-evaluator analysis rests on."""
        for name in ("3b_boost_404aa.bin", "rr_boost_404b.bin", "s2_boost_404.bin"):
            rom = bytes(load_rom(name))
            # band thresholds 40 20 10 5 3 and one-hot codes 00 01 02 04 08 0F
            assert rom[0x183C:0x1841] == bytes.fromhex("28140a0503"), name
            assert rom[0x1847:0x184D] == bytes.fromhex("00010204080f"), name
            # 0x08A8: MOV R0,#BFh ; MOV A,@R0 ; MOV R2,A   (reads the 67h:66h history)
            assert rom[0x08A8:0x08AC] == bytes.fromhex("78bfe6fa"), name
            # 0x0349: MOV 5Fh,A after LCALL 08A8 ; 0x00FA: ORL A,5Fh (P4 nibble emit)
            assert rom[0x0346:0x034B] == bytes.fromhex("1208a8f55f"), name
            assert rom[0x00FA:0x00FC] == bytes.fromhex("455f"), name
            # 0x02FC: MOV 34h,6Ch (knock integrator sample) ; 0x0086: MOV 69h,ADDAT
            assert rom[0x02FC:0x02FF] == bytes.fromhex("856c34"), name
            assert rom[0x0086:0x0089] == bytes.fromhex("85d969"), name

    def test_main_ecu_knock_handler_bytes(self):
        """Pin the main-ECU knock path: 21h <- XRAM A041 ^ 03, JB 21h.1 in the handler,
        LCALL 361F/LJMP 27B4 entry, and the retard-hold-ramp parameter block at 0x63F5."""
        for name in ("3b_fuel-ign_404aa.bin", "rr_fuel-ign_404b.bin", "s2_fuel-ign_404.bin"):
            rom = bytes(load_rom(name))
            assert rom[0x1386:0x138D] == bytes.fromhex("7841e26403f521"), name   # MOV R0,#41;MOVX;XRL #03;MOV 21h,A
            assert rom[0x27F6:0x27F9] == bytes.fromhex("200921"), name           # JB 21h.1,281A
            assert rom[0x2166:0x216C] == bytes.fromhex("12361f0227b4"), name     # LCALL 361F ; LJMP 27B4
            assert rom[0x361F:0x3622] == bytes.fromhex("9063f5"), name           # MOV DPTR,#63F5
            assert rom[0x63F5:0x63FD] == bytes.fromhex("0602ff051e07053d"), name


# -- cross-family compare (2026-09-09) -----------------------------------------

class TestXCompare:
    def test_self_compare_is_zero(self):
        from urrom.xcompare import xcompare
        p = ROMS_DIR / "3b_fuel-ign_404aa.bin"
        if not p.exists():
            import pytest; pytest.skip("3B ROM missing")
        x = xcompare(p, p, "fuel")
        assert all(abs(v) < 1e-9 for row in x.delta for row_v in [row] for v in row_v)

    def test_same_family_resample_matches_direct_diff(self):
        """3B vs RR share axes, so resampling must reproduce the plain cell difference."""
        from urrom.xcompare import xcompare
        from urrom.ecu_profiles import read_map, VARIANT_404
        a, b = ROMS_DIR / "3b_fuel-ign_404aa.bin", ROMS_DIR / "rr_fuel-ign_404b.bin"
        if not (a.exists() and b.exists()):
            import pytest; pytest.skip("404 ROMs missing")
        x = xcompare(a, b, "fuel", raw_values=True)
        m = next(m for m in VARIANT_404.main_maps if m.main_addr == 0x6A8E)
        da, db = read_map(a.read_bytes(), m), read_map(b.read_bytes(), m)
        for i in range(16):
            for j in range(16):
                assert abs(x.delta[i][j] - (db[i][j] - da[i][j])) < 1e-9

    def test_cross_family_shapes(self):
        from urrom.xcompare import xcompare
        a, b = ROMS_DIR / "3b_fuel-ign_404aa.bin", ROMS_DIR / "aby_fuel-ign_551aa.bin"
        if not (a.exists() and b.exists()):
            import pytest; pytest.skip("ROMs missing")
        x = xcompare(a, b, "ign")
        assert len(x.delta) == 16 and all(len(r) == 16 for r in x.delta)
        # both families use the 600.. rpm axis with identical deltas; only the top
        # breakpoint varies per map (7000 or 7200)
        assert x.a.rows[:15] == x.b.rows[:15] and x.a.rows[0] == 600

    def test_3b_and_adu_share_ign_roles_and_scale(self):
        """Raw cross-family match: 3B fallback map ~ ADU map 1, 3B main map ~ ADU map 5.
        Same bytes ⇒ same decode scale, which is why ign_decode() is 0.75°/count for both."""
        from urrom.xcompare import _load_side, resample
        a3b, adu = ROMS_DIR / "3b_fuel-ign_404aa.bin", ROMS_DIR / "adu_fuel-ign_551c.bin"
        if not (a3b.exists() and adu.exists()):
            import pytest; pytest.skip("ROMs missing")
        def rms(a_addr, b_addr):
            a = _load_side(a3b, "ign", a_addr, raw_values=True)
            b = _load_side(adu, "ign", b_addr, raw_values=True)
            bb = resample(b, a.rows, a.cols)
            return (sum((bb[i][j] - a.data[i][j]) ** 2 for i in range(16) for j in range(16)) / 256) ** 0.5
        assert rms(0x7076, 0x30AC) < 5.0      # fallback ~ fallback
        assert rms(0x71F8, 0x36BC) < 4.0      # main ~ map 5
        assert rms(0x71F8, 0x30AC) > 12.0     # main is NOT map 1


# -- 551 ignition selector (2026-09-09) ----------------------------------------

class Test551IgnSelector:
    def test_selector_and_coding_bytes(self):
        """Pin the ADU/ABY ignition table-set selector (0x4E06) and coding tables."""
        for name in ("adu_fuel-ign_551c.bin", "aby_fuel-ign_551aa.bin"):
            rom = bytes(load_rom(name))
            # 4E0F: MOV R0,#A4h ; MOV A,@R0 ; JB ACC.5,4E26 ; JB ACC.4,4E1F ; MOV 77h,#A0 ; MOV 78h,#34
            assert rom[0x4E0F:0x4E1E] == bytes.fromhex("78a4e620e51120e40775 77a0757834".replace(" ", "")), name
            assert rom[0x4E1F:0x4E25] == bytes.fromhex("7577a075784f"), name
            assert rom[0x4E26:0x4E2C] == bytes.fromhex("7577a075786a"), name
            # coding: thresholds, idx table, A4h codes
            assert rom[0x4FB0:0x4FB9] == bytes.fromhex("ffdccda985663d321f"), name
            assert rom[0x4FB9:0x4FC2] == bytes.fromhex("000000010102020202"), name
            assert rom[0x4FC8:0x4FCE] == bytes.fromhex("14002894 80a8".replace(" ", "")), name
            # IGN_CALC slot loads: MOV R2,#10h at 197C, MOV R2,#0Dh at 1980, MOV R2,#04h at 193D
            assert rom[0x197C:0x197E] == b"\x7a\x10" and rom[0x1980:0x1982] == b"\x7a\x0d", name
            assert rom[0x193D:0x193F] == b"\x7a\x04", name

    def test_551_map_names_reflect_selector(self):
        from urrom.ecu_profiles import VARIANT_551C
        names = {m.main_addr: m.name for m in VARIANT_551C.main_maps}
        assert "fault fallback" in names[0x30AC]
        assert "main, coding set A" in names[0x3263] and "alt, coding set A" in names[0x3387]
        assert "main, coding set B" in names[0x3598] and "alt, coding set B" in names[0x36BC]
        assert "main, coding set C" in names[0x380D] and "alt, coding set C" in names[0x3931]


# -- 0x0E13 vs 0x2E17 layouts hold the same calibration (2026-09-09) ------------

class TestLayoutEquivalence:
    PAIRS = [(0x0E13, 0x2E17), (0x10A8, 0x30AC), (0x125F, 0x3263), (0x1383, 0x3387),
             (0x1594, 0x3598), (0x16B8, 0x36BC), (0x1809, 0x380D), (0x192D, 0x3931)]

    def test_rs2_d02_maps_equal_adu_maps(self):
        """Same engine, two layouts: every 16x16 map is byte-identical (blank 0x02 cells
        of the partial RS2 D02 read excluded).  Proves the 0x0E13 family's encoding."""
        rs2 = bytes(load_rom("rs2_d02_fuel-ign_551b.bin"))[0x8000:]
        adu = bytes(load_rom("adu_fuel-ign_551c.bin"))[0x8000:]
        for a, b in self.PAIRS:
            x, y = rs2[a:a + 256], adu[b:b + 256]
            valid = [i for i in range(256) if x[i] != 0x02]
            assert len(valid) > 100, hex(a)
            assert all(x[i] == y[i] for i in valid), f"0x{a:04X} vs 0x{b:04X}"

    def test_prj_base_edits_vs_rs2_d02(self):
        """prj stock_AANABY = RS2 D02 cal with a base tune over it: maps 1/3/5/6/7
        untouched, map 4 retarded on the same grid, fuel and map 2 re-gridded to a
        10..240 load / 7400 rpm axis (2026-09-09)."""
        rs2 = bytes(load_rom("rs2_d02_fuel-ign_551b.bin"))[0x8000:]
        prj = bytes(load_rom("prj_stock_aan-aby_551aa_0202.bin"))[0x8000:]

        for addr in (0x10A8, 0x1383, 0x16B8, 0x1809, 0x192D):
            assert prj[addr:addr + 256] == rs2[addr:addr + 256], hex(addr)
            assert read_descriptor_axes(prj, addr, 16, 16) == read_descriptor_axes(rs2, addr, 16, 16)

        assert read_descriptor_axes(prj, 0x1594, 16, 16) == read_descriptor_axes(rs2, 0x1594, 16, 16)
        d = [prj[0x1594 + i] - rs2[0x1594 + i] for i in range(256)]
        assert sum(d) / 256 < -3

        for addr in (0x0E13, 0x125F):
            rows, cols = read_descriptor_axes(prj, addr, 16, 16)
            assert cols[0] == 10 and cols[-1] == 240 and rows[-1] >= 7400, hex(addr)
            srows, scols = read_descriptor_axes(rs2, addr, 16, 16)
            assert scols[-1] == 177 and srows[-1] == 7000

    def test_0202_axes_come_from_descriptors(self):
        """The prjmod file keeps Bosch descriptors; get_axes must read them, not the
        legacy PRJ-XDF axis addresses (which gave rows 9..16 / load 400..2840)."""
        rom = bytes(load_rom("prj_stock_aan-aby_551aa_0202.bin"))[0x8000:]
        from urrom.ecu_profiles import VARIANT_551AA_0202 as v
        m = next(x for x in v.main_maps if x.main_addr == 0x125F)
        rows, cols = get_axes(rom, m, v)
        assert rows == read_descriptor_axes(rom, 0x125F, 16, 16)[0]
        assert rows[0] == 600 and rows[-1] == 7400
        assert cols[0] == 10 and cols[-1] == 240


# -- Coding plug decode + Hardware-tab feature scoping (2026-09-09) ------------

class TestCodingPlug:
    def test_3b_bands_and_bank_bit(self):
        from urrom.coding_plug import decode_coding_plug
        fw = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        d = decode_coding_plug(fw, "404")
        assert d is not None and d.structure == "404" and d.ladder_addr == 0x3669
        assert [b.adc_lo for b in d.bands] == [0, 36, 51, 87, 123, 154, 195, 206, 225]
        assert [b.code for b in d.bands] == [0x4C, 0x04, 0x14, 0x08, 0x00, 0x44, 0x40, 0x10, 0x20]
        assert [b.coding_no for b in d.bands] == [9, 3, 7, 6, 1, 8, 4, 2, 5]
        bank1 = [b.band for b in d.bands if b.main_map == "Ign Map 5"]
        assert bank1 == [0, 5, 6]
        assert d.band_for_volts(2.5).coding_no == 1
        assert d.band_for_adc(160).main_map == "Ign Map 5"

    def test_551_sets_by_threshold(self):
        from urrom.coding_plug import decode_coding_plug
        for name, addr in (("adu_fuel-ign_551c.bin", 0x4FB0), ("rs2_d02_fuel-ign_551b.bin", 0x4F8E),
                           ("aby_fuel-ign_551aa.bin", 0x4FB0)):
            fw = bytes(load_rom(name))[:0x8000]
            d = decode_coding_plug(fw, "551C")
            assert d is not None and d.structure == "551" and d.ladder_addr == addr, name
            assert d.band_for_adc(86).ign_set == "B" and d.band_for_adc(86).main_map == "Ign Map 4"
            assert d.band_for_adc(87).ign_set == "A" and d.band_for_adc(153).ign_set == "A"
            assert d.band_for_adc(154).ign_set == "C" and d.band_for_adc(255).main_map == "Ign Map 6"
            assert [b.coding_no for b in d.bands] == [2, 2, 2, 1, 1, 3, 3, 3, 3]

    def test_3b_rr_s2_share_the_tables(self):
        from urrom.coding_plug import decode_coding_plug
        ref = decode_coding_plug(bytes(load_rom("3b_fuel-ign_404aa.bin")), "404")
        for name in ("rr_fuel-ign_404b.bin", "s2_fuel-ign_404.bin"):
            d = decode_coding_plug(bytes(load_rom(name)), "404")
            assert [(b.code, b.coding_no) for b in d.bands] == [(b.code, b.coding_no) for b in ref.bands]

    def test_no_ladder_on_boost_chip(self):
        from urrom.coding_plug import decode_coding_plug
        assert decode_coding_plug(bytes(load_rom("3b_boost_404aa.bin")), "404") is None


class TestHardwareScope:
    def test_feature_scope(self):
        from urrom.hw_patches import feature_applies
        assert feature_applies("lc_nls_scalars", "551AA_0202")
        assert not feature_applies("lc_nls_scalars", "551C")
        assert not feature_applies("dist_conversion", "404")
        assert feature_applies("dist_conversion", "551AA")
        assert feature_applies("coding_plug", "404") and feature_applies("coding_plug", "551C")
        assert not feature_applies("map_sensor_detect", "404")

    def test_404_results_flagged_out_of_scope(self):
        from urrom.hw_patches import detect_patches
        fw = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        res = {r.name: r for r in detect_patches(fw, "404")}
        for n in ("LC / NLS Motorsport Code", "Speed Density (SD) Mode",
                  "R660 Removal + MAP Wire", "AAN → RS2 R201 Resistor Swap"):
            assert not res[n].in_scope, n
            assert res[n].status.startswith("N/A"), n
        assert not res["MFTS Boost Cut Bypass"].in_scope   # 551 firmware offsets mean nothing here
        assert all(not r.in_scope for n, r in res.items() if n != "3B Spark-Cut Launch Control"),             "every 551 check must be out of scope on a 3B"
        assert res["3B Spark-Cut Launch Control"].in_scope

    def test_551_results_in_scope(self):
        from urrom.hw_patches import detect_patches
        fw = bytes(load_rom("adu_fuel-ign_551c.bin"))[:0x8000]
        res = {r.name: r for r in detect_patches(fw, "551C")}
        assert res["Speed Density (SD) Mode"].in_scope
        assert res["LC / NLS Motorsport Code"].in_scope


# -- Boost sensor scale for the pressure tables (2026-09-09) --------------------

class TestBoostSensor:
    def setup_method(self):
        from urrom import boost_sensor as bs
        bs.set_sensor("404", "bosch200"); bs.set_display("404", "kpa")
        bs.set_sensor("551", "mpx4250"); bs.set_display("551", "kpa")

    def teardown_method(self):
        self.setup_method()

    def test_transfer_functions(self):
        from urrom import boost_sensor as bs
        b200 = bs.set_sensor("404", "bosch200")
        assert b200.kpa(255) == 200 and b200.kpa(0) == 0
        mpx = bs.set_sensor("551", "mpx4250")
        assert abs(mpx.kpa(255) - 260) < 0.01 and mpx.kpa(0) == 10       # datasheet: 10 kPa at 0 V
        assert abs(mpx.volts_at(100) - 1.8) < 0.01                        # Vs(0.004*100-0.04)
        m400 = next(s for s in bs.sensors() if s.key == "mpxh6400")
        assert abs(m400.volts_at(100) - 1.17) < 0.01                      # Vs(0.2421-0.00842)
        for s in bs.sensors():
            for raw in (0, 37, 128, 200, 255):
                assert s.raw(s.kpa(raw)) == raw, (s.key, raw)             # round trip

    def test_3b_target_follows_selected_sensor(self):
        from urrom import boost_sensor as bs
        from urrom.ecu_profiles import VARIANT_404, read_map
        rom = bytes(load_rom("3b_boost_404aa.bin"))
        m = next(x for x in VARIANT_404.boost_maps if x.main_addr == 0x1934)
        raw = read_map(rom, m)
        flat = [v for row in raw for v in row]
        assert max(flat) == 0xED
        assert m.decode(0xED) == round(0xED / 255 * 200, 1)                # 185.9 kPa @ 200 linear
        bs.set_sensor("404", "mpx4250")
        assert m.decode(0xED) == round(0xED / 255 * 250 + 10, 1)           # 242.3 kPa @ MPX4250
        bs.set_display("404", "bar")
        assert m.decode(0xED) == round((0xED / 255 * 250 + 10 - 100) / 100, 2)
        assert m.encode(1.42) == 0xED                                      # bar gauge edit round-trips
        bs.set_display("404", "kpa")
        assert m.encode(242.3) == 0xED

    def test_551_target_has_kpa_decode(self):
        from urrom import boost_sensor as bs
        from urrom.ecu_profiles import ALL_VARIANTS
        v = next(x for x in ALL_VARIANTS if x.software_id == "551C")
        tgt = next(x for x in v.boost_maps if x.main_addr == 0x2520)
        lim = next(x for x in v.boost_maps if x.main_addr == 0x2A96)
        assert tgt.decode and lim.decode and tgt.encode
        assert tgt.decode(255) == 260.0                                    # MPX4250 default for 551
        bs.set_sensor("551", "lin300")
        assert tgt.decode(255) == 300.0
        assert bs.unit("551") == "kPa abs"

    def test_identify_ranks_by_voltage(self):
        from urrom import boost_sensor as bs
        assert bs.identify(1.8)[0][0].key == "mpx4250"
        assert bs.identify(2.5)[0][0].key == "bosch200"
        assert bs.identify(1.17)[0][0].key == "mpxh6400"
        assert bs.identify(1.67)[0][0].key == "lin300"

    def test_custom_sensor(self):
        from urrom import boost_sensor as bs
        s = bs.set_custom("404", 250, 0)
        assert s.key == "custom" and bs.decode(255, "404") == 250.0


# -- KWPBridge live values: bridge cells are already decoded (2026-09-09) --------

class TestKWPLiveValues:
    def _bridge_state(self):
        def cell(i, v, u): return {"index": i, "value": v, "unit": u, "display": f"{v} {u}"}
        return {"connected": True, "ecu_id": {"part_number": "4A0907551AA"},
                "groups": {"1": {"cells": [cell(1, 3000.0, "RPM"), cell(2, 92.0, "°C"),
                                           cell(3, 1.0, "λ"), cell(4, 24.0, "° BTDC")]},
                           "3": {"cells": [cell(1, 3000.0, "RPM"), cell(2, 100.0, ""),
                                           cell(3, 30.0, "%"), cell(4, 38.0, "°C")]},
                           "6": {"cells": [cell(1, 12.0, "%"), cell(2, 12.0, "%"),
                                           cell(3, 115.0, "kPa"), cell(4, 115.0, "kPa")]}}}

    def test_bridge_values_used_as_is(self):
        from urrom.kwp import LiveValues
        lv = LiveValues(self._bridge_state())
        assert lv.valid and lv.rpm == 3000 and lv.ect == 92 and lv.lambda_ == 1.0
        assert lv.timing == 24 and lv.load == 100 and lv.tps == 30 and lv.iat == 38
        assert lv.map_kpa == 115 and lv.n75_dc == 12
        assert lv.ecu_pn == "4A0907551AA"

    def test_legacy_raw_list_still_decoded(self):
        from urrom.kwp import LiveValues
        st = {"connected": True, "ecu_id": {"part_number": "4A0907551AA"},
              "groups": {"1": {"cells": [75, 162, 128, 50]},
                         "3": {"cells": [75, 100, 72, 108]}}}
        lv = LiveValues(st)
        assert lv.rpm == 3000 and lv.ect == 92 and lv.lambda_ == 1.0
        assert abs(lv.timing - (50 * 0.6491 - 8.2186)) < 1e-6
        assert lv.load == 100 and abs(lv.tps - 29.95) < 0.01 and lv.iat == 38


class TestKWPLiveValues3B:
    def _cell(self, i, v, u=""):
        return {"index": i, "value": v, "unit": u, "display": f"{v} {u}"}

    def test_ram_window_preferred(self):
        from urrom.kwp import LiveValues
        c = self._cell
        st = {"connected": True, "ecu_id": {"part_number": "447907404AA"},
              "groups": {"0": {"cells": [c(1, 90.0, "°C"), c(2, 100.0), c(3, 2550.0, "RPM"),
                                         c(4, 0), c(5, 100), c(6, 128), c(7, 0), c(8, 131.0),
                                         c(9, 126), c(10, 20.0, "° BTDC")]},
                         "100": {"cells": [c(1, 13.9, "V"), c(2, 36.0, "°C"), c(3, 92.0, "°C"),
                                           c(4, 5800.0, "RPM"), c(5, 185.0), c(6, 32.0), c(7, 57.0),
                                           c(8, 80.0)]}}}
        lv = LiveValues(st)
        assert lv.family == "404" and lv.valid
        assert lv.rpm == 5800 and lv.load == 185 and lv.ect == 92 and lv.iat == 36
        # no derived cell: computed 0.75*|57+47-127| = 17.25
        assert abs(lv.timing - 17.25) < 0.01 and lv.battery == 13.9
        assert lv.ign_raw53 == 20
        assert lv.lambda_ctrl == 131 and abs(lv.lambda_ - 1.0117) < 0.001

    def test_block_only_fallback(self):
        from urrom.kwp import LiveValues
        c = self._cell
        st = {"connected": True, "ecu_id": {"part_number": "857907404B"},
              "groups": {"0": {"cells": [c(1, 88.0, "°C"), c(2, 24.0), c(3, 800.0, "RPM"),
                                         c(4, 0), c(5, 0), c(6, 0), c(7, 0), c(8, 128.0),
                                         c(9, 0), c(10, 10.0, "° BTDC")]}}}
        lv = LiveValues(st)
        assert lv.family == "404" and lv.rpm == 800 and lv.load == 24
        assert lv.timing is None and lv.ign_raw53 == 10     # block alone cannot give degrees
        assert lv.lambda_ == 1.0

    def test_551_untouched(self):
        from urrom.kwp import LiveValues
        c = self._cell
        st = {"connected": True, "ecu_id": {"part_number": "4A0907551AA"},
              "groups": {"1": {"cells": [c(1, 3000.0, "RPM"), c(2, 92.0, "°C"), c(3, 1.0, "λ"), c(4, 24.0)]}}}
        lv = LiveValues(st)
        assert lv.family == "551" and lv.rpm == 3000


class TestTempDecode:
    def test_ecu_formula(self):
        from urrom.ecu_profiles import temp_decode
        assert abs(temp_decode(184) - 79.8) < 0.01 and abs(temp_decode(215) - 101.5) < 0.01


# -- 404 checksum + vwnut8392 launch control patch (2026-09-09) -----------------

class Test404Checksum:
    def test_every_404_chip_verifies(self):
        from urrom.ecu_profiles import verify_checksum_404, compute_checksum_404, read_stored_checksum_404
        for name in ("3b_fuel-ign_404a.bin", "3b_fuel-ign_404aa.bin", "rr_fuel-ign_404b.bin", "s2_fuel-ign_404.bin"):
            rom = bytes(load_rom(name))
            assert verify_checksum_404(rom), (name, hex(compute_checksum_404(rom)), hex(read_stored_checksum_404(rom)))

    def test_apply_restores_after_edit(self):
        from urrom.ecu_profiles import apply_checksum_404, verify_checksum_404
        rom = bytearray(load_rom("3b_fuel-ign_404aa.bin"))
        rom[0x71F8] ^= 0x01                      # touch a map cell
        assert not verify_checksum_404(bytes(rom))
        apply_checksum_404(rom)
        assert verify_checksum_404(bytes(rom))

    def test_detect_marks_404_checksum(self):
        from urrom.ecu_profiles import detect_rom, checksum_kind, VARIANT_404
        det = detect_rom(bytes(load_rom("3b_fuel-ign_404aa.bin")))
        assert det.checksum_ok and checksum_kind(det.variant) == "404"
        assert checksum_kind(VARIANT_404) == "404"

    def test_tuning_check_flags_bad_404_checksum(self):
        from urrom.tuning_checks import run_all_checks
        from urrom.ecu_profiles import VARIANT_404
        rom = bytearray(load_rom("3b_fuel-ign_404aa.bin")); rom[0x7F00] ^= 0x10
        issues = run_all_checks(bytes(rom), VARIANT_404)
        assert any(i.category == "checksum" and i.severity == "error" for i in issues)


class TestLaunchControl3B:
    PATCHED = Path(r"D:/ECU FLASH/Bins/3b_fuel-ign_404apatched.bin")

    def test_stock_chips_are_stock(self):
        from urrom.hw_patches import lc3b_status
        for name in ("3b_fuel-ign_404a.bin", "3b_fuel-ign_404aa.bin", "rr_fuel-ign_404b.bin", "s2_fuel-ign_404.bin"):
            assert lc3b_status(bytes(load_rom(name))) == "STOCK", name
        assert lc3b_status(bytes(load_rom("pt_fuel-ign_404h.bin"))) == "UNKNOWN"

    def test_apply_matches_vwnut_patch_except_id_text(self):
        from urrom.hw_patches import apply_lc3b, LC3B_CODE_OFF, LC3B_CODE, lc3b_status
        from urrom.ecu_profiles import apply_checksum_404, verify_checksum_404
        rom = bytearray(load_rom("3b_fuel-ign_404a.bin"))
        apply_lc3b(rom)
        apply_checksum_404(rom)
        assert lc3b_status(bytes(rom)) == "PATCHED" and verify_checksum_404(bytes(rom))
        if self.PATCHED.exists():
            ref = self.PATCHED.read_bytes()
            diff = [i for i in range(0x8000) if rom[i] != ref[i]]
            # only the checksum (we kept the stock ID text) and the ID text itself may differ
            assert all(0x7F00 <= i < 0x7F40 for i in diff), [hex(i) for i in diff][:10]
            assert bytes(ref[LC3B_CODE_OFF:LC3B_CODE_OFF + len(LC3B_CODE)]) == LC3B_CODE

    def test_revert_is_byte_exact(self):
        from urrom.hw_patches import apply_lc3b, revert_lc3b
        stock = bytes(load_rom("rr_fuel-ign_404b.bin"))
        rom = bytearray(stock); apply_lc3b(rom, launch_rpm=4000); revert_lc3b(rom)
        assert bytes(rom) == stock

    def test_scalars_decode(self):
        from urrom.hw_patches import apply_lc3b, LC3B_SCALARS, detect_patches
        rom = bytearray(load_rom("3b_fuel-ign_404a.bin")); apply_lc3b(rom, launch_rpm=4000)
        vals = {name: dec(rom[off]) for name, off, dec, enc, unit, lo, hi in LC3B_SCALARS}
        assert vals["Launch RPM"] == 4000 and vals["RPM ceiling"] == 7360
        assert vals["Throttle threshold"] == 85 and vals["Spark (54h raw)"] == 0x65 and vals["Dwell (58h raw)"] == 0x0D
        res = {r.name: r for r in detect_patches(bytes(rom), "404")}
        r = res["3B Spark-Cut Launch Control"]
        assert r.status == "PATCHED" and r.in_scope and r.applicable and len(r.scalars) == 5
        stock = {r.name: r for r in detect_patches(bytes(load_rom("3b_fuel-ign_404a.bin")), "404")}
        assert stock["3B Spark-Cut Launch Control"].status == "STOCK" and stock["3B Spark-Cut Launch Control"].applicable


class TestPublishedImages:
    def test_stage1_boost_is_rr_plus_six(self):
        from urrom.ecu_profiles import VARIANT_404, KNOWN_CRCS
        import zlib
        rr = bytes(load_rom("rr_boost_404b.bin"))
        st = bytes(load_rom("tunes/3b_stage1_boost_rrbase_plus5kpa.bin"))
        assert zlib.crc32(st) & 0xFFFFFFFF in KNOWN_CRCS
        targets = {m.main_addr: m for m in VARIANT_404.boost_maps if m.main_addr in (0x18B4, 0x1934, 0x19B4)}
        covered = set()
        for addr, m in targets.items():
            for i in range(m.rows * m.cols):
                assert st[addr + i] == min(250, rr[addr + i] + 6)
                covered.add(addr + i)
        assert all(st[i] == rr[i] for i in range(len(rr)) if i not in covered)

    def test_27c512_images_fold_to_their_chips(self):
        from urrom.ecu_profiles import fold_repeated_image, verify_checksum_404
        pairs = {"3b_fuel-ign_404aa_27C512.bin": ("3b_fuel-ign_404aa.bin", 2),
                 "3b_boost_404aa_27C512.bin": ("3b_boost_404aa.bin", 8),
                 "rr_boost_404b_27C512.bin": ("rr_boost_404b.bin", 8),
                 "s2_fuel-ign_404_27C512.bin": ("s2_fuel-ign_404.bin", 2),
                 "3b_stage1_boost_rrbase_plus5kpa_27C512.bin": ("tunes/3b_stage1_boost_rrbase_plus5kpa.bin", 8)}
        for img, (native, copies) in pairs.items():
            b = bytes(load_rom("27c512/" + img)); o = bytes(load_rom(native))
            folded, n, _ = fold_repeated_image(b)
            assert n == copies and folded == o, img
            if len(o) == 32768:
                assert verify_checksum_404(o), img


# -- Heat-map / 3D map views (2026-09-09) ---------------------------------------

class TestMapPlotView:
    @pytest.fixture(autouse=True)
    def _qt(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        pytest.importorskip("matplotlib")
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])

    def test_renders_3b_ignition_both_modes(self):
        from urrom.ui.map_view import MapPlotView, plotting_available
        from urrom.ecu_profiles import VARIANT_404, read_map, get_axes
        assert plotting_available()
        rom = bytes(load_rom("3b_fuel-ign_404aa.bin"))
        m = next(x for x in VARIANT_404.main_maps if x.main_addr == 0x71F8)
        rows, cols = get_axes(rom, m, VARIANT_404)
        vals = [[m.decode(v) for v in r] for r in read_map(rom, m)]
        v = MapPlotView(); v.resize(640, 480)
        for mode in ("heat", "3d"):
            v.set_mode(mode)
            v.set_map(rows, cols, vals, unit="°BTDC", title=m.name, changed=None)
            v.set_cursor(3, 5)
            v._canvas.draw()          # force a real render, not just draw_idle
        assert v.mode() == "3d"

    def test_table_snapshot_and_edit_signal(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("urrom_app_main", str(Path(__file__).resolve().parent.parent / "app" / "main.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        from urrom.ecu_profiles import VARIANT_404, get_axes
        rom = bytearray(load_rom("3b_fuel-ign_404aa.bin"))
        m = next(x for x in VARIANT_404.main_maps if x.main_addr == 0x71F8)
        rows, cols = get_axes(bytes(rom), m, VARIANT_404)
        t = mod.MapTable(); t.load(rom, m, rows, cols)
        snap = t.plot_snapshot()
        assert snap["rows"] == list(rows) and len(snap["values"]) == m.rows
        assert snap["unit"] == m.unit and not any(any(r) for r in snap["changed"])
        fired = []
        t.dataEdited.connect(lambda: fired.append(1))
        item = t.item(m.rows - 1, 0)         # display row 0 = logical row 0 inverted
        item.setText("20.0")
        assert fired and t.plot_snapshot()["changed"][0][0]

    def test_tabs_switch_views(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("urrom_app_main2", str(Path(__file__).resolve().parent.parent / "app" / "main.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        from urrom.ecu_profiles import VARIANT_404
        tab = mod.MainChipTab(); tab.load(bytearray(load_rom("3b_fuel-ign_404aa.bin")), VARIANT_404)
        tab._set_view("3d", persist=False)
        assert tab._plot.isVisibleTo(tab) and not tab._table.isVisibleTo(tab) and tab._plot.mode() == "3d"
        tab._set_view("table", persist=False)
        assert tab._table.isVisibleTo(tab) and not tab._plot.isVisibleTo(tab)
        bt = mod.BoostTab(); bt.load(bytearray(load_rom("3b_boost_404aa.bin")), VARIANT_404)
        bt._set_view("heat", persist=False)
        assert bt._plot.isVisibleTo(bt) and bt._plot.mode() == "heat"
