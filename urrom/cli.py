"""
urrom/cli.py
============
Headless CLI interface for UrROM.

Usage:
  python -m urrom.cli scan <rom.034|rom.bin>  [--strict]
  python -m urrom.cli info <rom.034|rom.bin>

Exit codes:
  0  — clean (no errors)
  1  — warnings found (not with --strict)
  2  — errors found
  3  — ROM unrecognised / load failure
"""

from __future__ import annotations
import sys
from pathlib import Path


def _load_rom(path: Path) -> tuple[bytes, object]:
    """Load and normalise a ROM file. Returns (wh_bytes, det)."""
    from urrom.ecu_profiles import normalize_rom, detect_rom
    from urrom.descramble import descramble_034, is_valid_034

    raw = path.read_bytes()
    if path.suffix.lower() == '.034':
        if not is_valid_034(raw):
            print(f"ERROR: {path.name} — not a valid .034 file (54903-byte variant not supported)")
            sys.exit(3)
        raw = bytes(descramble_034(raw))
    wh, notes = normalize_rom(raw)
    det = detect_rom(bytes(wh))
    return bytes(wh), det


def cmd_scan(args):
    """Scan a ROM for tuning issues."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom scan')
    p.add_argument('rom', help='ROM file (.bin or .034)')
    p.add_argument('--strict', action='store_true',
                   help='Exit code 1 on warnings (not just errors)')
    p.add_argument('--json', action='store_true', help='Output JSON')
    ns = p.parse_args(args)

    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(3)

    wh, det = _load_rom(path)
    if not det.variant:
        print(f"WARN: {path.name} — variant unrecognised (confidence: {det.confidence})")
        print(f"      CRC32 0x{det.crc32:08X}  build 0x{det.build_number:04X}")

    from urrom.tuning_checks import run_all_checks
    issues = run_all_checks(wh, det.variant, crc32=det.crc32)

    n_err  = sum(1 for i in issues if i.severity == 'error')
    n_warn = sum(1 for i in issues if i.severity == 'warning')
    n_info = sum(1 for i in issues if i.severity == 'info')

    if ns.json:
        import json
        out = {
            'rom': str(path),
            'variant': det.variant.software_id if det.variant else None,
            'confidence': det.confidence,
            'crc32': f'0x{det.crc32:08X}',
            'errors': n_err, 'warnings': n_warn, 'info': n_info,
            'issues': [
                {'severity': i.severity, 'category': i.category,
                 'map': i.map_name, 'description': i.description,
                 'cell': list(i.cell) if i.cell else None}
                for i in issues
            ]
        }
        print(json.dumps(out, indent=2))
    else:
        v_name = det.variant.name if det.variant else 'Unknown'
        print(f"UrROM scan — {path.name}")
        print(f"  Variant:    {v_name} [{det.variant.software_id if det.variant else '?'}]")
        print(f"  Confidence: {det.confidence}")
        print(f"  CRC32:      0x{det.crc32:08X}")
        print(f"  Issues:     {n_err} errors  {n_warn} warnings  {n_info} info")
        print()
        for iss in issues:
            cell_s = f" [{iss.cell[0]},{iss.cell[1]}]" if iss.cell and iss.cell[1] is not None else ""
            sym = {'error': '✗', 'warning': '⚠', 'info': 'ℹ'}.get(iss.severity, '?')
            print(f"  {sym} [{iss.severity.upper():<7}] {iss.map_name}{cell_s}")
            print(f"    {iss.description}")

        if not issues:
            print("  ✓ No issues found")

    if n_err:
        sys.exit(2)
    if ns.strict and n_warn:
        sys.exit(1)
    sys.exit(0)


def cmd_info(args):
    """Print ROM identification info."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom info')
    p.add_argument('rom', help='ROM file (.bin or .034)')
    ns = p.parse_args(args)

    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found"); sys.exit(3)

    wh, det = _load_rom(path)
    v = det.variant

    from urrom.ecu_profiles import KNOWN_CRCS, get_boost_pairing
    label = KNOWN_CRCS.get(det.crc32, (None, 'unknown'))[1]
    pairs = get_boost_pairing(det.crc32)

    print(f"ROM:        {path.name}")
    print(f"Variant:    {v.name if v else 'Unknown'}")
    print(f"Software:   {v.software_id if v else '?'}")
    print(f"Confidence: {det.confidence}")
    print(f"Method:     {det.method}")
    print(f"CRC32:      0x{det.crc32:08X}")
    print(f"Build:      0x{det.build_number:04X}")
    print(f"Label:      {label}")
    if pairs:
        from urrom.ecu_profiles import KNOWN_CRCS as KC
        pair_labels = [KC.get(c, (None, f'0x{c:08X}'))[1].split(' — ')[0] for c in pairs]
        print(f"Boost pair: {' OR '.join(pair_labels)}")
    if v:
        print(f"ECU PNs:    {', '.join(v.ecu_pns)}")
        n_conf = sum(1 for m in v.main_maps if m.confidence == 'CONFIRMED' and m.rows > 1)
        print(f"Maps:       {n_conf} confirmed  ({len(v.main_maps)} total)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print("urrom <command> [options]")
        print("  scan <rom>  [--strict] [--json]  — check for tuning issues")
        print("  info <rom>                        — print ROM identification")
        sys.exit(0)
    cmd = sys.argv[1]
    rest = sys.argv[2:]
    if cmd == 'scan':
        cmd_scan(rest)
    elif cmd == 'info':
        cmd_info(rest)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(3)


if __name__ == '__main__':
    main()
