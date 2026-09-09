"""
tools/dis8051.py — Minimal table-driven Intel 8051 / SAB80C5xx disassembler.

Usage:
    python tools/dis8051.py <rom.bin> [--start 0x0000] [--end 0x2000]
    python tools/dis8051.py <rom.bin> --xref 0x1600          # who references this address?
    python tools/dis8051.py <rom.bin> --movc                  # every MOVC + the DPTR feeding it

Written for the M2.3 boost-chip / M2.3.2 motor-chip RE work in UrROM.  Linear
sweep (no flow analysis), so data regions produce garbage — use --start/--end
to focus on code.  Good enough to find table lookups and their callers.
"""

from __future__ import annotations
import sys
import argparse
from pathlib import Path

# ── Opcode table ────────────────────────────────────────────────────────────
# Each entry: (mnemonic-with-placeholders, length)
# Placeholders: d8=direct, i8=#imm8, i16=#imm16, r8=rel, a11=addr11, a16=addr16,
#               b8=bit address, @Ri / Rn encoded via the opcode nibble.
_T: dict[int, tuple[str, int]] = {
    0x00: ("NOP", 1), 0x01: ("AJMP a11", 2), 0x02: ("LJMP a16", 3), 0x03: ("RR A", 1),
    0x04: ("INC A", 1), 0x05: ("INC d8", 2), 0x06: ("INC @R0", 1), 0x07: ("INC @R1", 1),
    0x10: ("JBC b8,r8", 3), 0x11: ("ACALL a11", 2), 0x12: ("LCALL a16", 3), 0x13: ("RRC A", 1),
    0x14: ("DEC A", 1), 0x15: ("DEC d8", 2), 0x16: ("DEC @R0", 1), 0x17: ("DEC @R1", 1),
    0x20: ("JB b8,r8", 3), 0x21: ("AJMP a11", 2), 0x22: ("RET", 1), 0x23: ("RL A", 1),
    0x24: ("ADD A,i8", 2), 0x25: ("ADD A,d8", 2), 0x26: ("ADD A,@R0", 1), 0x27: ("ADD A,@R1", 1),
    0x30: ("JNB b8,r8", 3), 0x31: ("ACALL a11", 2), 0x32: ("RETI", 1), 0x33: ("RLC A", 1),
    0x34: ("ADDC A,i8", 2), 0x35: ("ADDC A,d8", 2), 0x36: ("ADDC A,@R0", 1), 0x37: ("ADDC A,@R1", 1),
    0x40: ("JC r8", 2), 0x41: ("AJMP a11", 2), 0x42: ("ORL d8,A", 2), 0x43: ("ORL d8,i8", 3),
    0x44: ("ORL A,i8", 2), 0x45: ("ORL A,d8", 2), 0x46: ("ORL A,@R0", 1), 0x47: ("ORL A,@R1", 1),
    0x50: ("JNC r8", 2), 0x51: ("ACALL a11", 2), 0x52: ("ANL d8,A", 2), 0x53: ("ANL d8,i8", 3),
    0x54: ("ANL A,i8", 2), 0x55: ("ANL A,d8", 2), 0x56: ("ANL A,@R0", 1), 0x57: ("ANL A,@R1", 1),
    0x60: ("JZ r8", 2), 0x61: ("AJMP a11", 2), 0x62: ("XRL d8,A", 2), 0x63: ("XRL d8,i8", 3),
    0x64: ("XRL A,i8", 2), 0x65: ("XRL A,d8", 2), 0x66: ("XRL A,@R0", 1), 0x67: ("XRL A,@R1", 1),
    0x70: ("JNZ r8", 2), 0x71: ("ACALL a11", 2), 0x72: ("ORL C,b8", 2), 0x73: ("JMP @A+DPTR", 1),
    0x74: ("MOV A,i8", 2), 0x75: ("MOV d8,i8", 3), 0x76: ("MOV @R0,i8", 2), 0x77: ("MOV @R1,i8", 2),
    0x80: ("SJMP r8", 2), 0x81: ("AJMP a11", 2), 0x82: ("ANL C,b8", 2), 0x83: ("MOVC A,@A+PC", 1),
    0x84: ("DIV AB", 1), 0x85: ("MOV d8,d8", 3), 0x86: ("MOV d8,@R0", 2), 0x87: ("MOV d8,@R1", 2),
    0x90: ("MOV DPTR,i16", 3), 0x91: ("ACALL a11", 2), 0x92: ("MOV b8,C", 2), 0x93: ("MOVC A,@A+DPTR", 1),
    0x94: ("SUBB A,i8", 2), 0x95: ("SUBB A,d8", 2), 0x96: ("SUBB A,@R0", 1), 0x97: ("SUBB A,@R1", 1),
    0xA0: ("ORL C,/b8", 2), 0xA1: ("AJMP a11", 2), 0xA2: ("MOV C,b8", 2), 0xA3: ("INC DPTR", 1),
    0xA4: ("MUL AB", 1), 0xA5: ("DB A5h", 1), 0xA6: ("MOV @R0,d8", 2), 0xA7: ("MOV @R1,d8", 2),
    0xB0: ("ANL C,/b8", 2), 0xB1: ("ACALL a11", 2), 0xB2: ("CPL b8", 2), 0xB3: ("CPL C", 1),
    0xB4: ("CJNE A,i8,r8", 3), 0xB5: ("CJNE A,d8,r8", 3), 0xB6: ("CJNE @R0,i8,r8", 3), 0xB7: ("CJNE @R1,i8,r8", 3),
    0xC0: ("PUSH d8", 2), 0xC1: ("AJMP a11", 2), 0xC2: ("CLR b8", 2), 0xC3: ("CLR C", 1),
    0xC4: ("SWAP A", 1), 0xC5: ("XCH A,d8", 2), 0xC6: ("XCH A,@R0", 1), 0xC7: ("XCH A,@R1", 1),
    0xD0: ("POP d8", 2), 0xD1: ("ACALL a11", 2), 0xD2: ("SETB b8", 2), 0xD3: ("SETB C", 1),
    0xD4: ("DA A", 1), 0xD5: ("DJNZ d8,r8", 3), 0xD6: ("XCHD A,@R0", 1), 0xD7: ("XCHD A,@R1", 1),
    0xE0: ("MOVX A,@DPTR", 1), 0xE1: ("AJMP a11", 2), 0xE2: ("MOVX A,@R0", 1), 0xE3: ("MOVX A,@R1", 1),
    0xE4: ("CLR A", 1), 0xE5: ("MOV A,d8", 2), 0xE6: ("MOV A,@R0", 1), 0xE7: ("MOV A,@R1", 1),
    0xF0: ("MOVX @DPTR,A", 1), 0xF1: ("ACALL a11", 2), 0xF2: ("MOVX @R0,A", 1), 0xF3: ("MOVX @R1,A", 1),
    0xF4: ("CPL A", 1), 0xF5: ("MOV d8,A", 2), 0xF6: ("MOV @R0,A", 1), 0xF7: ("MOV @R1,A", 1),
}
# Register-nibble opcodes x8..xF
for r in range(8):
    _T[0x08 + r] = (f"INC R{r}", 1)
    _T[0x18 + r] = (f"DEC R{r}", 1)
    _T[0x28 + r] = (f"ADD A,R{r}", 1)
    _T[0x38 + r] = (f"ADDC A,R{r}", 1)
    _T[0x48 + r] = (f"ORL A,R{r}", 1)
    _T[0x58 + r] = (f"ANL A,R{r}", 1)
    _T[0x68 + r] = (f"XRL A,R{r}", 1)
    _T[0x78 + r] = (f"MOV R{r},i8", 2)
    _T[0x88 + r] = (f"MOV d8,R{r}", 2)
    _T[0x98 + r] = (f"SUBB A,R{r}", 1)
    _T[0xA8 + r] = (f"MOV R{r},d8", 2)
    _T[0xB8 + r] = (f"CJNE R{r},i8,r8", 3)
    _T[0xC8 + r] = (f"XCH A,R{r}", 1)
    _T[0xD8 + r] = (f"DJNZ R{r},r8", 2)
    _T[0xE8 + r] = (f"MOV A,R{r}", 1)
    _T[0xF8 + r] = (f"MOV R{r},A", 1)

# SFR names (8051 core + SAB80C515/535 extensions used by Bosch M2.3 chips)
SFR = {
    0x80: "P0", 0x81: "SP", 0x82: "DPL", 0x83: "DPH", 0x87: "PCON", 0x88: "TCON",
    0x89: "TMOD", 0x8A: "TL0", 0x8B: "TL1", 0x8C: "TH0", 0x8D: "TH1", 0x90: "P1",
    0x98: "SCON", 0x99: "SBUF", 0xA0: "P2", 0xA8: "IEN0", 0xA9: "IP0", 0xB0: "P3",
    0xB8: "IEN1", 0xB9: "IP1", 0xC0: "IRCON", 0xC1: "CCEN", 0xC2: "CCL1", 0xC3: "CCH1",
    0xC4: "CCL2", 0xC5: "CCH2", 0xC6: "CCL3", 0xC7: "CCH3", 0xC8: "T2CON", 0xCA: "CRCL",
    0xCB: "CRCH", 0xCC: "TL2", 0xCD: "TH2", 0xD0: "PSW", 0xD8: "ADCON", 0xD9: "ADDAT",
    0xDA: "DAPR", 0xDB: "P6", 0xE0: "ACC", 0xE8: "P4", 0xF0: "B", 0xF8: "P5",
}


def _d8(v: int) -> str:
    return SFR.get(v, f"{v:02X}h") if v >= 0x80 else f"{v:02X}h"


def _bit(v: int) -> str:
    if v >= 0x80:
        base = v & 0xF8
        return f"{SFR.get(base, f'{base:02X}h')}.{v & 7}"
    return f"{0x20 + (v >> 3):02X}h.{v & 7}"


def decode(rom: bytes, pc: int):
    """Return (length, text) for the instruction at pc."""
    op = rom[pc]
    mnem, ln = _T.get(op, (f"DB {op:02X}h", 1))
    args = rom[pc + 1: pc + ln]
    if len(args) < ln - 1:
        return 1, f"DB {op:02X}h"
    txt = mnem
    i = 0
    # Operand order in the byte stream follows the mnemonic's placeholder order,
    # except CJNE/JBC/JB/JNB/DJNZ d8 forms where the direct/imm comes first and
    # rel last — which is also placeholder order.  MOV d8,d8 is src,dst in bytes.
    if op == 0x85:
        src, dst = args
        return 3, f"MOV {_d8(dst)},{_d8(src)}"
    for tok in ("a16", "i16", "a11", "d8", "i8", "b8", "r8"):
        while tok in txt:
            if tok == "a16":
                v = (args[i] << 8) | args[i + 1]; i += 2
                txt = txt.replace("a16", f"{v:04X}h", 1)
            elif tok == "i16":
                v = (args[i] << 8) | args[i + 1]; i += 2
                txt = txt.replace("i16", f"#{v:04X}h", 1)
            elif tok == "a11":
                v = ((op & 0xE0) << 3) | args[i]; i += 1
                v = ((pc + 2) & 0xF800) | v
                txt = txt.replace("a11", f"{v:04X}h", 1)
            elif tok == "d8":
                v = args[i]; i += 1
                txt = txt.replace("d8", _d8(v), 1)
            elif tok == "i8":
                v = args[i]; i += 1
                txt = txt.replace("i8", f"#{v:02X}h", 1)
            elif tok == "b8":
                v = args[i]; i += 1
                txt = txt.replace("b8", _bit(v), 1)
            elif tok == "r8":
                v = args[i]; i += 1
                rel = v - 256 if v > 127 else v
                txt = txt.replace("r8", f"{(pc + ln + rel) & 0xFFFF:04X}h", 1)
    return ln, txt


def disassemble(rom: bytes, start: int = 0, end: int | None = None):
    end = len(rom) if end is None else min(end, len(rom))
    pc = start
    while pc < end:
        ln, txt = decode(rom, pc)
        yield pc, rom[pc:pc + ln], txt
        pc += ln


def xrefs(rom: bytes, target: int, start=0, end=None):
    """Instructions whose 16-bit immediate/address operand equals target."""
    out = []
    for pc, raw, txt in disassemble(rom, start, end):
        if len(raw) == 3 and raw[0] in (0x02, 0x12, 0x90) and ((raw[1] << 8) | raw[2]) == target:
            out.append((pc, txt))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--start", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--end", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--xref", type=lambda s: int(s, 0), default=None,
                    help="list LJMP/LCALL/MOV DPTR references to this address")
    ap.add_argument("--movc", action="store_true",
                    help="list every MOVC A,@A+DPTR with the nearest preceding MOV DPTR,#")
    ns = ap.parse_args(argv)
    rom = Path(ns.rom).read_bytes()

    if ns.xref is not None:
        for pc, txt in xrefs(rom, ns.xref, ns.start, ns.end):
            print(f"{pc:04X}  {txt}")
        return
    if ns.movc:
        last_dptr = None
        for pc, raw, txt in disassemble(rom, ns.start, ns.end):
            if raw[0] == 0x90:
                last_dptr = (pc, (raw[1] << 8) | raw[2])
            elif raw[0] == 0x93:
                d = f"DPTR=#{last_dptr[1]:04X}h (set @{last_dptr[0]:04X})" if last_dptr else "DPTR=?"
                print(f"{pc:04X}  MOVC A,@A+DPTR    {d}")
        return
    for pc, raw, txt in disassemble(rom, ns.start, ns.end):
        print(f"{pc:04X}  {raw.hex(' '):<9} {txt}")


if __name__ == "__main__":
    main()
