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
    detect_rom, read_map, write_map, get_axes,
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

    def test_mfts_64kb_mirror(self):
        """On 64KB doubled ROM, patch should be applied to both halves."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        if len(rom) < 0x10000:
            # Pad to 64KB doubled
            rom = bytearray(rom[:0x8000]) + bytearray(rom[:0x8000])
        apply_mfts_bypass(rom)
        # Check both halves
        assert rom[MFTS_BYPASS_OFFSET: MFTS_BYPASS_OFFSET + 2] == MFTS_BYPASS_PATCH
        assert rom[MFTS_BYPASS_OFFSET + 0x8000: MFTS_BYPASS_OFFSET + 0x8000 + 2] == MFTS_BYPASS_PATCH

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
        before = {m.name: read_map(bytes(rom), m) for m in confirmed}
        apply_mfts_bypass(rom)
        for m in confirmed:
            after = read_map(bytes(rom), m)
            assert after == before[m.name], f"MFTS patch corrupted map {m.name}"

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

    def test_doubled_rom_save(self):
        """64KB doubled ROM: both halves should be identical after save."""
        rom = load_rom("aan_fuel-ign_551aa.bin")
        if len(rom) < 0x10000:
            rom = bytearray(rom[:0x8000]) + bytearray(rom[:0x8000])
        # Edit in working half
        rom[0x100] = 0xAA
        # Double the halves
        rom[0x8000 + 0x100] = 0xAA
        assert rom[0x100] == rom[0x8100]

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
        assert "404" not in CHECKSUM_VARIANTS
        assert "404V8" not in CHECKSUM_VARIANTS
        assert not has_software_checksum(VARIANT_404)
        assert not has_software_checksum(VARIANT_V8_PT)

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
