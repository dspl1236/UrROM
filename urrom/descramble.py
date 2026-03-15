"""
urrom/descramble.py
===================
034 Rip Chip .034 file format descramble / scramble.

Algorithm
---------
Reverse-engineered from ECUGUI.jar (034EFI Rip Chip v1.0.0, 2012-03-15)
by decompiling com.bprog.model.BitShift and com.bprog.model.MakeNative.

Per-byte transform (BitShift.algOne, a=0xAA, b=0x55):
  1. algZero — swap adjacent bit pairs within the byte:
       t1 = byte & 0xAA          (even-position bits: 7,5,3,1)
       t2 = t1 >> 1              (shift right)
       t3 = byte & 0x55          (odd-position bits: 6,4,2,0)
       t4 = (t3 << 1) & 0xFF     (shift left)
       az = t2 | t4
  2. bSwap — swap the low and high nibbles:
       result = ((az >> 4) & 0x0F) | ((az << 4) & 0xF0)

The transform is self-inverse: T(T(x)) == x for all byte values.

File format (.034)
------------------
Encoded (scrambled) 65536-byte file.
Leading zero bytes may be stripped; strip before applying the transform.

  decode(raw):
    stripped = raw.lstrip(b'\\x00')   # remove leading zeros
    return bytes(transform(b) for b in stripped)

  encode(rom_bytes, target_size=65536):
    stacked = _stack(rom_bytes, target_size)   # fill to 65536 with repetition
    return bytes(transform(b) for b in stacked)

Verified
--------
- Self-inverse property: T(T(x)) == x for all 256 byte values
- 034_-_893906266D_Stock.034 decodes to match 893906266D_MMS05C_physical.bin
  at offset 0x8000 (working half of the 64KB doubled ROM)
- Round-trip encode→decode preserves ADU 551C working-half CRC exactly

Notes
-----
- The .034 format stores a 64KB doubled image (65536 bytes).
  For 32KB flat ROMs (3B / V8), the 32KB data is stacked (repeated) to fill 65536.
- Leading zeros in .034 files are a product of older Rip Chip versions;
  the strip step handles both old and new format files.
- The 034 EFI ECU definition (.ecu) file is a Java-serialised object specifying
  map addresses, checksums, and example bytes for the specific ECU. This module
  handles only the binary data transform; ECU definitions remain in ecu_profiles.py.
"""

from __future__ import annotations


# ── Core byte transform ───────────────────────────────────────────────────────

def _transform_byte(b: int) -> int:
    """
    BitShift.algOne(byte, a=0xAA, b=0x55).
    Self-inverse: transform(transform(x)) == x for all x in 0..255.
    """
    b &= 0xFF
    # Step 1 — algZero: swap adjacent bit pairs
    az = ((b & 0xAA) >> 1) | (((b & 0x55) << 1) & 0xFF)
    # Step 2 — bSwap: swap nibbles
    return ((az >> 4) & 0x0F) | ((az << 4) & 0xF0)


# Pre-computed lookup table for speed
_LUT: bytes = bytes(_transform_byte(i) for i in range(256))


# ── Public API ────────────────────────────────────────────────────────────────

def descramble_034(raw: bytes) -> bytes:
    """
    Decode a .034 file to raw ROM bytes.

    Strips any leading 0x00 bytes (artifact of older Rip Chip versions),
    then applies the per-byte transform.

    Args:
        raw: Raw bytes read from a .034 file.

    Returns:
        Decoded ROM bytes. Length will be at most 65536.
        For a valid main chip .034: 65536 bytes (64KB doubled image).
    """
    # strip leading zeros (ArrayUtil.strip(data, 0, 0))
    start = 0
    while start < len(raw) and raw[start] == 0:
        start += 1
    return bytes(_LUT[b] for b in raw[start:])


def scramble_034(rom: bytes, target_size: int = 65536) -> bytes:
    """
    Encode raw ROM bytes into .034 format.

    For 64KB files: applies the transform directly.
    For 32KB flat files (3B / V8): stacks (repeats) the data to fill
    target_size bytes first, with the last byte of the source always
    written at position target_size-1 (MakeNative.make() behaviour).

    Args:
        rom:         Raw ROM bytes (32KB or 64KB).
        target_size: Output size in bytes (default 65536).

    Returns:
        Scrambled .034 bytes ready to write to disk.
    """
    stacked = _stack(rom, target_size)
    return bytes(_LUT[b] for b in stacked)


def is_valid_034(raw: bytes) -> bool:
    """
    Heuristic check: does this look like a valid .034 file?

    Returns True if the file is 65536 bytes and decodes to a ROM with
    more than 100 distinct byte values (high entropy — not blank/padded).
    """
    if len(raw) not in (65536, 65535):
        return False
    decoded = descramble_034(raw)
    return len(set(decoded)) > 100


# ── Internal helpers ──────────────────────────────────────────────────────────

def _stack(src: bytes, dest_size: int) -> bytearray:
    """
    ArrayUtil.stack(src, dest_size, redoLast=True).

    Fills a bytearray of dest_size by repeating src as many whole times
    as fit, then writing src[0:remainder] for the tail.
    Finally, stacked[-1] is always set to src[-1] (redoLast=True).
    """
    src_len = len(src)
    if src_len == 0:
        return bytearray(dest_size)

    stacked = bytearray(dest_size)
    whole_dups = dest_size // src_len
    remainder  = dest_size % src_len

    idx = 0
    for _ in range(whole_dups):
        stacked[idx:idx + src_len] = src
        idx += src_len

    if remainder:
        stacked[idx:idx + remainder] = src[:remainder]

    # redoLast = True
    stacked[dest_size - 1] = src[-1]
    return stacked
