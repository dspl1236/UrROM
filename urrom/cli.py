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
    p.add_argument('--html', metavar='FILE', default=None,
                   help='Write HTML scan report to FILE')
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

    if ns.html:
        _write_scan_html(ns.html, Path(ns.rom), det, issues)
        if not ns.json:
            print(f"HTML report written to {ns.html}")

    if n_err:
        sys.exit(2)
    if ns.strict and n_warn:
        sys.exit(1)
    sys.exit(0)


def _write_scan_html(out_path: str, rom_path: Path, det, issues) -> None:
    """Write a standalone HTML scan report."""
    import html as _html
    from datetime import datetime
    n_err  = sum(1 for i in issues if i.severity == 'error')
    n_warn = sum(1 for i in issues if i.severity == 'warning')
    n_info = sum(1 for i in issues if i.severity == 'info')
    v_name = det.variant.name if det.variant else 'Unknown'
    sw_id  = det.variant.software_id if det.variant else '?'
    ts     = datetime.now().strftime('%Y-%m-%d %H:%M')

    badge_col = '#ff4444' if n_err else '#ffaa00' if n_warn else '#2dff6e'
    badge_txt = (f'✗ {n_err} error{"s" if n_err!=1 else ""}' if n_err else
                 f'⚠ {n_warn} warning{"s" if n_warn!=1 else ""}' if n_warn else
                 '✓ Clean')

    rows = []
    sev_cols = {'error': '#ff4444', 'warning': '#ffaa00', 'info': '#6e7681'}
    for iss in issues:
        col = sev_cols.get(iss.severity, '#c9d1d9')
        cell_s = f'[{iss.cell[0]},{iss.cell[1]}]' if iss.cell and iss.cell[1] is not None else ''
        rows.append(
            f'<tr>'
            f'<td style="color:{col};font-weight:bold;">{iss.severity.upper()}</td>'
            f'<td>{_html.escape(iss.category)}</td>'
            f'<td>{_html.escape(iss.map_name)}</td>'
            f'<td>{_html.escape(cell_s)}</td>'
            f'<td>{_html.escape(iss.description)}</td>'
            f'</tr>'
        )
    rows_html = '\n'.join(rows) if rows else (
        '<tr><td colspan="5" style="color:#2dff6e;text-align:center;">No issues found</td></tr>')

    html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<title>UrROM Scan — {_html.escape(rom_path.name)}</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:Consolas,monospace;padding:20px;margin:0;}}
h1{{font-size:16px;color:#c9d1d9;}} .meta{{color:#6e7681;font-size:11px;margin:8px 0 16px;}}
.badge{{display:inline-block;padding:4px 14px;border-radius:12px;font-size:12px;
        background:{badge_col}20;border:1px solid {badge_col};color:{badge_col};}}
table{{border-collapse:collapse;width:100%;font-size:11px;margin-top:12px;}}
th{{background:#1a2332;color:#6e7681;padding:6px 10px;text-align:left;}}
td{{padding:5px 10px;border-bottom:1px solid #1a2332;}}
tr:hover{{background:#131920;}}
@media print{{body{{background:white;color:black;}} td{{border-bottom:1px solid #ddd;}}}}
</style></head><body>
<h1>UrROM Scan Report</h1>
<div class='meta'>
ROM: {_html.escape(rom_path.name)} &nbsp;·&nbsp;
Variant: {_html.escape(v_name)} [{_html.escape(sw_id)}] &nbsp;·&nbsp;
CRC32: 0x{det.crc32:08X} &nbsp;·&nbsp;
Scanned: {ts}
</div>
<span class='badge'>{badge_txt}</span>
&nbsp; {n_err} errors &nbsp;·&nbsp; {n_warn} warnings &nbsp;·&nbsp; {n_info} info
<table>
<tr><th>Severity</th><th>Category</th><th>Map</th><th>Cell</th><th>Description</th></tr>
{rows_html}
</table>
</body></html>"""

    Path(out_path).write_text(html, encoding='utf-8')


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


def cmd_maps(args):
    """List every map the chip's firmware references (551 64KB images)."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom maps')
    p.add_argument('rom', help='64KB 551 chip image (.bin or .034)')
    p.add_argument('--all', action='store_true', help='include 1D tables (default: 2D only)')
    p.add_argument('--json', action='store_true')
    ns = p.parse_args(args)
    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found"); sys.exit(3)
    raw = path.read_bytes()
    if path.suffix.lower() == '.034':
        from urrom.descramble import descramble_034
        raw = bytes(descramble_034(raw))
    from urrom.ecu_profiles import decode_descriptor_tables, DESCRIPTOR_INPUTS
    maps = decode_descriptor_tables(raw)
    if not maps:
        print("No descriptor tables found — is this a 64KB split-bank 551 image?"); sys.exit(3)
    if not ns.all:
        maps = [m for m in maps if m['two_d']]
    def nm(v): return '-' if v is None else DESCRIPTOR_INPUTS.get(v, f'{v:02X}h')
    if ns.json:
        import json
        print(json.dumps([{**m, 'x_input': nm(m['x_input']), 'y_input': nm(m['y_input'])} for m in maps], indent=1))
        return
    print(f"{len(maps)} firmware-referenced maps in {path.name}")
    print(f"{'data WH':8s} {'desc':6s} {'shape':7s} {'X':9s} {'Y':9s} axes")
    for m in maps:
        shape = f"{m['rows']}x{m['cols']}" if m['two_d'] else f"{m['rows']}"
        xa = m['x_axis']; ya = m['y_axis']
        ax = f"X={xa[0]}..{xa[-1]}" + (f"  Y={ya[0]}..{ya[-1]}" if ya else "")
        print(f"0x{m['data']:04X}   0x{m['desc']:04X} {shape:7s} {nm(m['x_input']):9s} {nm(m['y_input']):9s} {ax}")


def cmd_chip(args):
    """Convert between native chip images and 27C512 (fill) images."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom chip')
    p.add_argument('rom', help='chip image (.bin, or .034 for the main chip)')
    p.add_argument('--to', choices=['27c512', 'native'], default='27c512',
                   help="27c512: repeat the native image to fill 64KB (default); "
                        "native: fold a repeated 64KB image back to the chip's own size")
    p.add_argument('-o', '--out', metavar='FILE', help='output file (default: <rom>_<to>.bin)')
    ns = p.parse_args(args)
    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found"); sys.exit(3)
    raw = path.read_bytes()
    if path.suffix.lower() == '.034':
        from urrom.descramble import descramble_034
        raw = bytes(descramble_034(raw))
    from urrom.ecu_profiles import fold_repeated_image, expand_to_chip, chip_name_for_size
    native, copies, notes = fold_repeated_image(raw)
    for n in notes:
        print(f"note: {n}")
    if ns.to == 'native':
        out = native
    else:
        try:
            out = expand_to_chip(native, '27C512')
        except ValueError as e:
            print(f"ERROR: {e}"); sys.exit(3)
    out_path = Path(ns.out) if ns.out else path.with_name(f"{path.stem}_{ns.to}.bin")
    out_path.write_bytes(out)
    print(f"{path.name}: {chip_name_for_size(len(native))} native image "
          f"({len(native):,} B) -> {out_path.name} {chip_name_for_size(len(out))} ({len(out):,} B)")


def cmd_xcompare(args):
    """Cross-family decoded comparison of the main fuel or ignition map."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom xcompare')
    p.add_argument('a', help='reference chip (its axes are used)')
    p.add_argument('b', help='chip to compare (resampled onto A)')
    p.add_argument('--role', choices=['fuel', 'ign'], default='fuel')
    p.add_argument('--a-map', type=lambda s: int(s, 0), default=None, help='override A map data address')
    p.add_argument('--b-map', type=lambda s: int(s, 0), default=None, help='override B map data address')
    p.add_argument('--csv', metavar='FILE', default=None)
    p.add_argument('--raw', action='store_true', help='compare raw bytes instead of decoded values')
    ns = p.parse_args(args)
    from urrom.xcompare import xcompare, format_report, write_csv
    x = xcompare(Path(ns.a), Path(ns.b), ns.role, ns.a_map, ns.b_map, raw_values=ns.raw)
    print(format_report(x, unit="raw" if ns.raw else ""))
    if ns.csv:
        write_csv(x, Path(ns.csv)); print(f"CSV written to {ns.csv}")


def cmd_coding(args):
    """Decode the coding-plug band table (ADC ch4 -> ignition set) from a firmware image."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom coding')
    p.add_argument('rom')
    p.add_argument('--adc', type=lambda s: int(s, 0), default=None, help='look up one ADC count')
    p.add_argument('--volts', type=float, default=None, help='look up one pin voltage')
    ns = p.parse_args(args)
    from urrom.coding_plug import decode_coding_plug, format_table
    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found"); sys.exit(3)
    _, det = _load_rom(path)
    sw = det.variant.software_id if det and det.variant else ""
    # 404: the whole 32 KB chip is firmware+cal; 551: firmware is the lower 32 KB
    fw = bytes(path.read_bytes()[:0x8000])
    dec = decode_coding_plug(fw, sw)
    if dec is None:
        print("coding ladder not found"); sys.exit(2)
    print(f"{Path(ns.rom).name} [{sw or '?'}]")
    print(format_table(dec))
    if ns.adc is not None or ns.volts is not None:
        b = dec.band_for_adc(ns.adc) if ns.adc is not None else dec.band_for_volts(ns.volts)
        print()
        print(f"-> band {b.band}: {b.ign_set} {b.main_map}  (coding no. {b.coding_no})")


def cmd_rescale_load(args):
    """Compress the 404 load scale for a bigger turbo (docs/3B_load_headroom_RE.md)."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom rescale-load')
    p.add_argument('rom'); p.add_argument('out')
    p.add_argument('--factor', type=float, required=True, help='0.2..1.0; stock-load headroom = 255/factor')
    p.add_argument('--cap', type=int, default=255, help='new air-per-rev cap in load counts (default 255)')
    ns = p.parse_args(args)
    from urrom.load_rescale import rescale_load
    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found"); sys.exit(3)
    rom = path.read_bytes()
    _, det = _load_rom(path)
    sw = det.variant.software_id if det and det.variant else ""
    if sw != "404":
        print(f"ERROR: {path.name} is not a 3B/RR/S2 (404) fuel/ign chip ({sw or 'unknown'})"); sys.exit(2)
    out, report = rescale_load(rom, ns.factor, ns.cap)
    Path(ns.out).write_bytes(bytes(out))
    print(report.text()); print(f"written {ns.out} (checksum applied)")


def cmd_boost_sensor(args):
    """Re-encode a 404 boost chip for a different MAP sensor (docs/3B_boost_chip_RE.md)."""
    import argparse
    import urrom.boost_sensor as bs
    keys = [s.key for s in bs.sensors()]
    p = argparse.ArgumentParser(prog='urrom boost-sensor')
    p.add_argument('rom'); p.add_argument('out')
    p.add_argument('--from', dest='src', default='bosch200', choices=keys)
    p.add_argument('--to', dest='dst', required=True, choices=keys)
    p.add_argument('--limits-psi', type=float, default=None, help='raise target ceiling / overboost release to this boost')
    p.add_argument('--scale-gains', action='store_true', help='keep duty-per-kPa by scaling the P/I gain tables')
    ns = p.parse_args(args)
    from urrom.boost_rescale import convert_sensor, target_summary
    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found"); sys.exit(3)
    rom = path.read_bytes()
    if len(rom) not in (0x2000, 0x10000):
        print(f"ERROR: {path.name} is {len(rom)} bytes; expected an 8 KB 404 boost chip (or its 27C512 image)"); sys.exit(2)
    if len(rom) == 0x10000:
        from urrom.ecu_profiles import fold_repeated_image
        rom, _, _ = fold_repeated_image(rom)
    out, report = convert_sensor(rom, ns.src, ns.dst, ns.limits_psi, ns.scale_gains)
    Path(ns.out).write_bytes(bytes(out))
    print(report.text())
    for a, (lo, hi, psi) in target_summary(bytes(out), ns.dst).items():
        print(f"  target 0x{a:04X} on {ns.dst}: {lo}..{hi} kPa abs (peak {psi:+.1f} psi)")
    print(f"written {ns.out} (8 KB native; use 'chip' for the 27C512 image)")


def cmd_gt_scaffold(args):
    """Scaffold a 404 fuel/ign chip for a bigger turbo (docs/3B_GT3071_step4_fuel_spark.md)."""
    import argparse
    p = argparse.ArgumentParser(prog='urrom gt-scaffold')
    p.add_argument('rom'); p.add_argument('out')
    p.add_argument('--factor', type=float, default=0.75, help='load-scale factor (default 0.75)')
    p.add_argument('--top', type=int, default=225, help='new LOAD axis top (default 225)')
    p.add_argument('--new-cols', type=int, default=3, help='columns added above the old top (default 3)')
    p.add_argument('--enrich', type=float, default=0.08, help='fuel ramp in the new columns (default 0.08)')
    p.add_argument('--retard-deg', type=float, default=1.5, help='ignition ramp in the new columns (default 1.5)')
    p.add_argument('--limiter', type=int, default=250, help='load limiter 1 (default 250)')
    p.add_argument('--injector-ratio', type=float, default=1.0, help='stock_cc / new_cc (default 1.0 = not scaled)')
    ns = p.parse_args(args)
    from urrom.gt_builder import build_scaffold
    path = Path(ns.rom)
    if not path.exists():
        print(f"ERROR: {path} not found"); sys.exit(3)
    _, det = _load_rom(path)
    sw = det.variant.software_id if det and det.variant else ""
    if sw != "404":
        print(f"ERROR: {path.name} is not a 3B/RR/S2 (404) fuel/ign chip ({sw or 'unknown'})"); sys.exit(2)
    out, report = build_scaffold(path.read_bytes(), ns.factor, ns.top, ns.new_cols, ns.enrich,
                                 ns.retard_deg, ns.limiter, ns.injector_ratio)
    Path(ns.out).write_bytes(bytes(out))
    print(report.text()); print(f"written {ns.out} (checksum applied) — a scaffold, not a tune")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print("urrom <command> [options]")
        print("  scan <rom>  [--strict] [--json]  — check for tuning issues")
        print("  info <rom>                        — print ROM identification")
        print("  maps <rom>  [--all] [--json]      — list maps the firmware references (551 64KB)")
        print("  chip <rom>  [--to 27c512|native]  — fill a 27C512 image / fold one back to native size")
        print("  xcompare <A> <B> [--role fuel|ign] — decoded cross-family map comparison (B resampled onto A)")
        print("  coding <rom> [--adc N | --volts V] — coding-plug bands -> ignition set (3B/RR/S2 and 551)")
        print("  rescale-load <rom> <out> --factor K [--cap N] — compress the 404 load scale (GAIN, axes, limiters, cap)")
        print("  gt-scaffold <rom> <out> [--factor K --top N --injector-ratio R ...] — big-turbo scaffold for a 404 fuel/ign chip")
        print("  boost-sensor <rom> <out> --to KEY [--from KEY] [--limits-psi P] [--scale-gains] — re-encode a 404 boost chip for another MAP sensor")
        sys.exit(0)
    cmd = sys.argv[1]
    rest = sys.argv[2:]
    if cmd == 'scan':
        cmd_scan(rest)
    elif cmd == 'info':
        cmd_info(rest)
    elif cmd == 'maps':
        cmd_maps(rest)
    elif cmd == 'chip':
        cmd_chip(rest)
    elif cmd == 'xcompare':
        cmd_xcompare(rest)
    elif cmd == 'gt-scaffold':
        cmd_gt_scaffold(rest)
    elif cmd == 'boost-sensor':
        cmd_boost_sensor(rest)
    elif cmd == 'rescale-load':
        cmd_rescale_load(rest)
    elif cmd == 'coding':
        cmd_coding(rest)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(3)


if __name__ == '__main__':
    main()
