"""
tools/build_guide.py — build docs/UrROM_How_To.pdf (the user guide).

    python tools/build_guide.py

Screenshots come from docs/img/ (captured offscreen from the app).  Text is
kept here rather than in a separate source so the guide is regenerated in
one step whenever the app changes.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "docs" / "img"
OUT = ROOT / "docs" / "UrROM_How_To.pdf"

try:
    from urrom.version import APP_VERSION
except Exception:  # pragma: no cover
    APP_VERSION = ""

INK = colors.HexColor("#1B222C")
MUTED = colors.HexColor("#5C6672")
ACCENT = colors.HexColor("#1F6F8B")
WARN = colors.HexColor("#B4621E")
RULE = colors.HexColor("#C9D0D8")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=20, leading=24,
                    textColor=INK, spaceBefore=6, spaceAfter=8)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=13.5, leading=17,
                    textColor=ACCENT, spaceBefore=12, spaceAfter=4)
BODY = ParagraphStyle("Body", parent=ss["BodyText"], fontName="Helvetica", fontSize=10, leading=14,
                      textColor=INK, spaceAfter=6, alignment=TA_LEFT)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, leading=11, textColor=MUTED)
CAP = ParagraphStyle("Cap", parent=SMALL, spaceBefore=2, spaceAfter=10)
BUL = ParagraphStyle("Bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=3)
NOTE = ParagraphStyle("Note", parent=BODY, backColor=colors.HexColor("#FFF4E5"), borderColor=WARN,
                      borderWidth=0.6, borderPadding=6, leftIndent=4, spaceBefore=6, spaceAfter=10)
CODE = ParagraphStyle("Code", parent=BODY, fontName="Courier", fontSize=8.8, leading=11.5,
                      backColor=colors.HexColor("#F1F3F5"), borderPadding=5, leftIndent=4, spaceAfter=8)
TITLE = ParagraphStyle("Title", parent=H1, fontSize=30, leading=34, spaceAfter=4)
SUB = ParagraphStyle("Sub", parent=BODY, fontSize=12, leading=16, textColor=MUTED)


def P(text, style=BODY):
    return Paragraph(text, style)


def bullets(items):
    return [Paragraph(f"• {t}", BUL) for t in items]


def shot(name, caption, width=160 * mm):
    p = IMG / name
    if not p.exists():
        return [P(f"[missing screenshot {name}]", SMALL)]
    from PIL import Image as PILImage
    with PILImage.open(p) as im:
        w, h = im.size
    img = Image(str(p), width=width, height=width * h / w)
    return [KeepTogether([img, Paragraph(caption, CAP)])]


CELL = ParagraphStyle("Cell", parent=BODY, fontSize=8.8, leading=11)
CELLH = ParagraphStyle("CellH", parent=CELL, fontName="Helvetica-Bold")


def table(rows, widths=None, header=True):
    rows = [[Paragraph(str(c), CELLH if (header and i == 0) else CELL) if isinstance(c, str) else c
             for c in r] for i, r in enumerate(rows)]
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.8),
             ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
             ("TOPPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style += [("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    t.setStyle(TableStyle(style))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 12 * mm, f"UrROM how-to  ·  {date.today():%Y-%m-%d}"
                      + (f"  ·  v{APP_VERSION}" if APP_VERSION else ""))
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, str(doc.page))
    canvas.restoreState()


def build() -> Path:
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=20 * mm,
                            title="UrROM how-to", author="UrROM", subject="Editing Bosch Motronic M2.3 / M2.3.2 chips")
    s = []

    # ── Title ────────────────────────────────────────────────────────────
    s += [Spacer(1, 30 * mm), P("UrROM", TITLE),
          P("How to read, edit, compare and log the Bosch Motronic M2.3 / M2.3.2 chips in the Audi 5-cylinder 20V turbo", SUB),
          Spacer(1, 6 * mm),
          P("3B · RR · S2 (447/857/895 907 404) and AAN · ABY · ADU · RS2 (4A0/8A0 907 551), with KWPBridge live data", SUB),
          Spacer(1, 40 * mm),
          P("Everything in this guide that is a fact about the ECU was traced in the firmware and is written up in the "
            "<b>docs/</b> folder of the repository; the app shows the same evidence on every cell. Where something is still "
            "an assumption it says so, in the app and here.", BODY),
          PageBreak()]

    # ── 1 What it is ─────────────────────────────────────────────────────
    s += [P("1. What UrROM is", H1),
          P("UrROM opens the EPROM images of the two chips in these ECUs, shows every calibration map the firmware "
            "actually references, lets you edit them in real units, keeps the checksum the ECU checks at boot, and writes "
            "burner-ready images. It also compares chips, decodes the coding plug, applies known firmware patches, and, "
            "with KWPBridge on a K-line cable, overlays the live engine position on the maps."),
          P("Supported chips", H2),
          table([["Family", "ECU part numbers", "Chips", "Notes"],
                 ["M2.3 \"404\"", "447 907 404 (3B, 200 20V), 857 907 404 (RR, UrQuattro), 895 907 404 (S2 3B)",
                  "32 KB fuel/ignition + 8 KB boost MCU", "16-bit checksum at 0x7F00; early KW1281 dialect"],
                 ["M2.3.2 \"551\"", "4A0 907 551 (AAN/ABY), 8A0 907 551 (ADU/RS2), 895 907 551 (S2 ABY)",
                  "64 KB split-bank fuel/ignition + 32 KB boost", "firmware low / calibration high; prjmod + 034 tunes"],
                 ["V8", "441/077 907 557, 441 907 404 (PT/ABH)", "single chip", "preliminary map data"]],
                widths=[26 * mm, 62 * mm, 40 * mm, 42 * mm]),
          P("Install and run", H2),
          P("Download <b>UrROM.exe</b> from the repository's latest release, or run from source:"),
          P("pip install -r requirements.txt<br/>python app/main.py", CODE),
          P("Windows, Python 3.11 or newer. PyQt5, matplotlib and numpy come with the requirements; no OpenGL is needed."),
          CondPageBreak(90 * mm)]

    # ── 2 Workbench ─────────────────────────────────────────────────────
    s += [P("2. The workbench", H1)]
    s += shot("guide_workbench.png",
              "The workbench: map tree on the left, editor in the middle, inspector on the right. "
              "The file bar shows both chips, the checksum state and the KWPBridge status.")
    s += bullets([
        "<b>File → Open ROM…</b> opens the fuel/ignition chip (.bin, .034, 27C512 images fold automatically). "
        "<b>Open Boost Chip…</b> pairs the boost chip; a known pair is checked against the catalogue.",
        "<b>Map tree</b>: fuel, ignition, other, boost control, knock. Click a map to edit it. The tick marks the "
        "confidence (confirmed, provisional, unconfirmed).",
        "<b>Editor</b>: the map with its real axes (rpm down, load across; TPS × rpm on the boost chip). "
        "The description, the provenance line and the confidence badge sit above it.",
        "<b>Inspector</b>: ROM identity (variant, part numbers, CRC, build), the Hardware tab, and the Health scan.",
        "<b>View</b> menu: dock layout is saved between sessions.",
    ])
    s += [P("Saving", H2),
          P("<b>Save ROM…</b> writes the edited chip. On 3B / RR / S2 chips the 16-bit checksum at 0x7F00 is recomputed "
            "(the ECU verifies it at boot and logs a fault if it is wrong); on prjmod 551 chips the 0x3FFA checksum is. "
            "<b>File → Write 27C512 images</b> writes the 64 KB image a modern burner needs: a 32 KB chip stored twice, "
            "an 8 KB boost chip eight times. Opening such an image folds it back."),
          P("Only 27C512 EPROMs are still easy to buy, so burn the 27C512 image, not the native file.", NOTE),
          CondPageBreak(90 * mm)]

    # ── 3 Editing ───────────────────────────────────────────────────────
    s += [P("3. Editing a map", H1),
          P("Type a value in a cell. Decoded units are the default (°BTDC, kPa, %); the <b>Decoded</b> button switches "
            "to raw bytes. Right-click for undo, fill, scale and copy/paste. <b>Revert changes</b> restores the map.")]
    s += shot("guide_workbench_3d.png", "Table / Heat / 3D on every map. Drag the surface to orbit. Edits, the raw toggle, "
              "the boost sensor scale and the live cursor all follow into the views.")
    s += [P("Guard rails", H2),
          P("Every edit gets an <b>Edit</b> line under the map: the change in raw and in real units, then anything the "
            "firmware traces say it risks. Flagged cells turn amber or red and carry the same text in their tooltip. "
            "Nothing is blocked; you decide.")]
    s += shot("guard_ign.png", "An ignition edit at load 174 / 4600 rpm: +6° over stock in the knock region, and an 8° "
              "step against a neighbour.")
    s += bullets([
        "Boost target within a few counts of the sensor's full scale (255 is the sensor ceiling; the controller needs headroom).",
        "Added advance at load ≥ 130 and ≥ 3500 rpm: the boost board's knock control will pull it back and log a fault.",
        "A step of 6° or more against a neighbouring cell.",
        "Fuel taken out under boost; the last load column, which runs at every load above the axis.",
        "A typed value the byte cannot hold; the checksum that will be rewritten on save.",
    ])
    s += [P("Provenance", H2),
          P("The line under each map's description, and the tail of every cell's tooltip, say where the address came "
            "from (firmware descriptor tables, an XDF, or a diff), which real chips confirmed it, the decode formula and "
            "where it was established, the axis source, any open caveat, and the docs section to read.")]
    s += shot("prov_editor.png", "Provenance for the 3B main ignition map: descriptor tables at 0x6093–0x621F, "
              "confirmed on four chips, raw × 0.75 − 22.5 °BTDC.")
    s += [CondPageBreak(90 * mm)]

    # ── 4 Boost editor ──────────────────────────────────────────────────
    s += [P("4. The boost chip", H1),
          P("The boost MCU holds three Boost Target tables (selected by intake-air band) and three N75 duty tables, "
            "TPS down, rpm across. A table byte is a sensor voltage, so pressure depends on the fitted sensor: pick it "
            "in the <b>Sensor</b> selector and choose kPa absolute, bar gauge or psi gauge.")]
    s += shot("guide_boost.png", "Boost Target A on the RR chip at the assumed 200 kPa scale: the peak line shows raw, "
              "kPa, bar and psi.")
    s += [P("Which sensor?", H2),
          table([["Sensor", "Vout at atmosphere (key on, engine off)", "Fitted on"],
                 ["Bosch 200 kPa (linear, assumed)", "2.50 V", "3B / RR / S2 boost board (stock)"],
                 ["MPX4250A 250 kPa", "1.80 V", "AAN / ABY stock, QLCC"],
                 ["linear 250 kPa", "2.00 V", "aftermarket 250 kPa swaps"],
                 ["linear 300 kPa", "1.67 V", "RS2 R201 swap, 034EFI \"3 bar\" tunes"],
                 ["MPXH6400A 400 kPa", "1.17 V", "prjmod speed-density"]],
                widths=[52 * mm, 62 * mm, 56 * mm]),
          Spacer(1, 4),
          P("<b>Identify from a voltage reading…</b> in the selector ranks the sensors from a measured output voltage. "
            "The controller itself does not depend on the scale: it subtracts its own key-on ambient from both target and "
            "measurement, so the tables are absolute sensor counts and the scale only changes what a count means. That is "
            "also why a 250 kPa sensor raises boost without any table edit: the same bytes sit 145 counts above ambient "
            "instead of 109.", BODY),
          P("The stock 3B board's sensor is still assumed to be linear 0–200 kPa. A mechanical gauge reading on one full "
            "pull confirms or corrects it; the cluster gauge cannot, because its scaling is unknown.", NOTE),
          CondPageBreak(90 * mm)]

    # ── 5 Compare ───────────────────────────────────────────────────────
    s += [P("5. Comparing chips", H1),
          P("The <b>Compare</b> tab takes a second chip, even from the other family. A 551 map is paired with the 3B "
            "map by role and bilinear-resampled onto the A chip's axes, so the delta is in real units on your grid. "
            "Same-family chips at the same address compare cell for cell.")]
    s += shot("guide_compare.png", "The Compare tab with the S2 chip loaded as ROM B against the 3B: base, delta and "
              "compare tables side by side, the per-map change strip below, and Most changed map to jump to the "
              "biggest difference. Fuel Map 1 differs in 213 of 256 cells, all a few raw counts leaner on the S2.")
    s += shot("cmp_s2_diff3d.png", "Difference surface: S2 minus 3B ignition. The overrun spike and the mid-band ridge "
              "are the S2's part-throttle advance.")
    s += shot("cmp_adu_blend.png", "Blend mode with the slider at 100 %: the ADU (RS2) main ignition map resampled onto "
              "the 3B's load and rpm axes.")
    s += bullets(["<b>B map</b> chooses which map on the B chip to pair; the default follows the role (fuel, main ignition).",
                  "<b>Difference</b> shows B − A; <b>Blend</b> morphs A into B with the slider.",
                  "<b>Raw bytes</b> compares undecoded values when a formula is in question.",
                  "The summary gives cells differing, mean, rms, min and max; <b>Export diff report…</b> and the "
                  "<b>Most changed map</b> jump work for same-family chips."])
    s += [CondPageBreak(90 * mm)]

    # ── 6 Hardware tab ──────────────────────────────────────────────────
    s += [P("6. The Hardware tab", H1),
          P("Everything about the chip that is not a map: what the chip family supports, the boost sensor, the coding "
            "plug, firmware patches and modifications. Items that cannot apply to the loaded chip are hidden; a checkbox "
            "shows them dimmed.")]
    s += shot("hwtab_lc3b.png", "A 3B with vwnut8392's launch control installed: the patch card with its five editable "
              "values, and the coding-plug decoder above it.", width=62 * mm)
    s += [P("Coding plug", H2),
          P("Both families read the coding plug on ADC channel 4 and bin it into nine bands. The card shows each band's "
            "voltage, the coding number a tester would display, and the ignition set it selects. The plug's resistance is "
            "not in the ROM: measure the pin voltage with the plug fitted (3B: ECU pins 38 / 39 / 54 against pin 2; AAN: "
            "38 / 39 against pin 30) or read the coding number from the ECU's identification. The 3B sends it in the "
            "third ID block as \"PMC n\"."),
          P("Firmware patches", H2),
          table([["Patch", "Chips", "What it does"],
                 ["3B Spark-Cut Launch Control (vwnut8392 V1.01)", "3B, RR, S2",
                  "Hard spark cut with clutch down, throttle over the threshold, rpm between launch and ceiling. "
                  "Needs ECU pin 38 wired to a clutch switch. Launch rpm, throttle, ceiling, spark and dwell are editable."],
                 ["MFTS boost-cut bypass, load-overflow decap, lambda cold-start delay", "551 (prjmod)",
                  "Existing 551 patches; offsets confirmed per variant."]],
                widths=[58 * mm, 26 * mm, 86 * mm]),
          Spacer(1, 4),
          P("Apply and Revert restore the exact stock bytes; UrROM keeps the factory ID text so a tester still sees a "
            "stock ECU. Launch control is off-road use and can damage the engine, turbo or exhaust if abused.", NOTE),
          CondPageBreak(90 * mm)]

    # ── 7 Live data ─────────────────────────────────────────────────────
    s += [P("7. Live data with KWPBridge", H1),
          P("KWPBridge (github.com/dspl1236/KWPBridge) owns the K-line on a KKL cable and broadcasts the ECU's data on a "
            "local port. UrROM connects to it automatically, checks the ECU's part number against the loaded chip, and "
            "then draws the live position on the current map."),
          P("python -m kwpbridge --port COM3 --protocol kwp1281", CODE),
          bullets(["551 family: the standard four-cell groups (rpm, load, coolant, lambda, timing, MAP on prjmod).",
                   "3B / RR / S2: the early KW1281 dialect. KWPBridge reads the ten-byte group 000 and a RAM window that "
                   "gives rpm and load in the map's own axis units, plus the ECU's own timing formula.",
                   "The badge in the file bar shows the connection state and the live summary."]),
          P("Recording and trace", H2),
          P("<b>Tools → Start recording live data…</b> writes every sample to a CSV that <b>Overlay data log on map…</b> "
            "replays onto any map. <b>Live trace on current map</b> paints where the engine actually runs.")]
    s += shot("trace_heat.png", "The live trace on the fuel map: ringed cells are where the engine spent its time, "
              "sized by sample count. The table lightens the same cells and gives the count in the tooltip.")
    s += [P("Bench mode", H2),
          P("<b>Tools → Bench mode (simulated engine)</b> starts KWPBridge's mock ECU for the loaded chip inside UrROM. "
            "The cursor walks the maps through cold start, idle, cruise, a boost run and decel, the trace fills in, and "
            "the badge reads BENCH. Use it to learn the maps or rehearse a session before the cable goes on.")]
    s += shot("guide_bench.png", "Bench mode on a 3B, Heat view: the file bar reads BENCH with the simulated rpm, "
              "coolant, lambda and timing, the amber square is the live cursor and the white rings are the trace "
              "filling in as the mock walks Fuel Map 1 through warm-up exactly as a car would.")
    s += [CondPageBreak(90 * mm)]

    # ── 8 Session log ───────────────────────────────────────────────────
    s += [P("8. Session log and undo", H1),
          P("<b>Tools → Session log… (Ctrl+L)</b> lists every edit as a sentence with its axis position and real units, "
            "one line per cell (edits back to the starting value drop out):"),
          P("Ignition Map 2 (main, coding A) at 4600 rpm / load 174: 9.8 → 15.8 °BTDC (+6)<br/>"
            "Boost Target B (normal IAT band, default) at TPS 219 / 4886 rpm: 189.8 → 196.1 kPa abs (+6.3)", CODE),
          bullets(["<b>Copy as commit message</b>: a git-style title, a per-map summary and the sentences.",
                   "<b>Undo last edit</b> reverts the most recent change in whichever tab holds that map.",
                   "<b>Save…</b> writes the log as text or HTML. Boost-chip edits are logged in the sensor's units."])]
    s += shot("guide_sessionlog.png", "The session log after two ignition edits: the header shows the file, chip and "
              "start time, the body is the commit-style summary and one sentence per cell, and the buttons undo, "
              "copy or save it.", width=130 * mm)
    s += [P("Command line", H2),
          P("python -m urrom.cli scan rom.bin           # tuning checks, exit 2 on errors<br/>"
            "python -m urrom.cli info rom.bin           # identification<br/>"
            "python -m urrom.cli maps rom.bin           # every map the firmware references<br/>"
            "python -m urrom.cli chip boost.bin         # 8/32 KB image → 27C512 image (--to native folds back)<br/>"
            "python -m urrom.cli xcompare A.bin B.bin --role ign   # cross-family compare<br/>"
            "python -m urrom.cli coding rom.bin --volts 2.5        # coding-plug band lookup", CODE),
          CondPageBreak(90 * mm)]

    # ── 9 Recipes ───────────────────────────────────────────────────────
    s += [P("9. Recipes for a 3B (200 20V)", H1),
          P("OEM combination", H2),
          P("The three factory chip sets for the 3B engine differ in known ways. The RR boost chip runs about +5 kPa "
            "with more wastegate duty at high rpm; the S2 fuel/ignition chip adds 1.5–3° in the mid band and top-end fuel "
            "while leaning idle and cruise; the S2 boost chip is 0.2 bar lower. The combination that moves the right "
            "things is the <b>RR boost chip with the S2 fuel/ignition chip</b>, every byte factory. Images are in "
            "<b>roms/27c512/404/</b>."),
          P("Repository layout: stock chip reads sit flat in <b>roms/</b>; UrROM-built tunes are filed by ECU family under "
            "<b>roms/tunes/&lt;family&gt;/</b> and their burner images under <b>roms/27c512/&lt;family&gt;/</b>, where "
            "<b>404</b> is the 3B / RR / S2 fuel-ign and boost chips and <b>551</b> is AAN / ABY / ADU / RS2. Every tune has a row "
            "in roms/README.md with its base chip, what changed, its CRC32 and its status.", SMALL),
          table([["Boost chip", "Target B peak (200 kPa scale)", "gauge"],
                 ["3B stock", "186 kPa", "+0.86 bar / 12.5 psi"],
                 ["RR", "191 kPa", "+0.91 bar / 13.2 psi"],
                 ["UrROM stage 1 (RR + 6 raw)", "196 kPa", "+0.96 bar / 13.9 psi"],
                 ["S2", "169 kPa", "+0.69 bar / 10.0 psi"]],
                widths=[52 * mm, 62 * mm, 56 * mm]),
          Spacer(1, 4),
          P("Stage 1", H2),
          P("<b>roms/tunes/404/3b_stage1_boost_rrbase_plus5kpa.bin</b> is the RR boost chip with the three target tables "
            "lifted 6 raw and capped at 250. Duty, gains, knock tables and ceilings are untouched. Run it with a boost "
            "gauge: about 14 psi confirms the sensor scale and that the controller reaches its target."),
          P("The stock sensor caps any 200 kPa-sensor chip near 1.0 bar (raw 255 is full scale). Beyond that the first "
            "step is a 250 kPa sensor, identified by its voltage at atmosphere, after which the same tables mean about "
            "1.4 bar and must be scaled back down in the editor.", NOTE),
          P("Stock boost on a 3-bar sensor", H2),
          P("The main ECU reads the MAP sensor in one place only, a 3-point correction that is 1.00 on every stock chip, so a "
            "different sensor changes nothing but the boost board. <b>python -m urrom.cli boost-sensor &lt;chip&gt; &lt;out&gt; "
            "--to vmap300_034</b> re-encodes a stock boost chip so every target, threshold, ceiling and correction keeps its kPa; "
            "duty tables are untouched. Two are published for the GM / 034 3-bar sensor (kPa = raw/255 × 300 + 21):"),
          table([["File (roms/tunes/404/)", "Base", "Peak target", "Use"],
                 ["3b_boost_404aa_3bar034.bin", "stock 3B boost", "12.4 psi (stock)", "3-bar sensor, otherwise stock car"],
                 ["rr_boost_404b_3bar034.bin", "stock RR boost", "13.3 psi (RR)", "3-bar sensor with the RR profile"]],
                widths=[58 * mm, 30 * mm, 32 * mm, 50 * mm]),
          P("Bench the sensor before burning: 5 V supply, about 1.3 V at atmosphere for a GM 3-bar; the identifier in the boost "
            "tab confirms the preset from that number. The cluster boost gauge is driven off the boost board with an untraced "
            "scale and will read about 1.5 × high on a 3-bar sensor; use a mechanical gauge. One raw count is now 1.18 kPa instead "
            "of 0.78, so the loop acts 1.5 × harder per kPa; if boost hunts on a steady pull, rebuild with --scale-gains.", NOTE),
          P("The RS2-turbo chipset", H2),
          P("What the 200 20V would have shipped with if it had the RS2's K24-7200: <b>roms/tunes/404/3b_rs2turbo_boost_mpx4250.bin</b> "
            "and <b>3b_rs2turbo_fuel-ign_greens38.bin</b>, built by <b>python -m urrom.cli rs2-chipset</b> from the 3B's own chips "
            "with the RS2 D02 boost chip and the ADU fuel/ign chip as references. Hardware assumed: K24-7200, RS2 green injectors "
            "(0 280 150 984, 405 cc) on the RS2 3.8 bar regulator, and the AAN/ADU 250 kPa MPX4250A sensor on the boost board."),
          bullets(["<b>Boost chip:</b> the 3B chip re-encoded for the 250 kPa sensor (the RS2's 14.2 psi peak is raw 253 of 255 on the "
                   "200 kPa sensor), the RS2 factory full-throttle curve written in by rpm with the 3B's part-throttle shape scaled "
                   "under it, the RS2 per-rpm N75 duty envelope, duty ceiling 78 %, overboost release 22 psi.",
                   "<b>Fuel/ign chip:</b> fuel maps and cranking × 305/405 for the greens, the ADU main ignition map resampled onto "
                   "the 3B axes into all four main maps, air-per-rev cap 255, load limiter 210, checksum valid.",
                   "<b>Not RS2 on purpose:</b> the fuel map. The ADU's runs 15 % leaner above load 100 relative to an injector scale "
                   "the 551 keeps elsewhere, so it is 3B × injector ratio here, to be trimmed on a wideband."]),
          table([["rpm", "3B WOT target", "RS2 WOT target (in the chip)"],
                 ["2250", "12.5 psi", "7.3 psi"], ["3300", "12.5", "9.8"], ["4100", "12.5", "12.4"],
                 ["4900", "12.3", "14.0"], ["5300 and up", "12.2 → 10.3", "14.2"]],
                widths=[30 * mm, 40 * mm, 60 * mm]),
          Spacer(1, 4)]
    s += shot("guide_rs2_boost.png", "The RS2-turbo boost chip in the boost tab on the MPX4250A preset in psi: the RS2 curve "
              "in the top TPS row (7.3 psi at 2250 rpm rising to 14.2 from 5000), the 3B's part-throttle rows scaled beneath it. "
              "Rpm runs right to left as the boost MCU stores it.")
    s += [P("Order on the car: greens and regulator with the old boost chip first (idle and cruise lambda), then the 250 kPa "
            "sensor with this boost chip (about 1.5 V at atmosphere on the bench), then the first pulls with a wideband, checking "
            "the loop reaches 14 psi by 5000 rpm without the release tripping. docs/3B_RS2_turbo_chipset.md has the numbers.", NOTE),
          P("Where to read more", H2),
          table([["Document", "What it holds"],
                 ["docs/3B_boost_chip_RE.md", "boost MCU tables, axes, knock evaluator, control loop, sensor scale"],
                 ["docs/551_calibration_descriptors_RE.md", "descriptor tables, ignition selection (3B §3c, 551 §3e), coding plug §3f"],
                 ["docs/3B_KW1281_RE.md", "the 3B's K-line dialect and group 000 decodes"],
                 ["docs/3B_launch_control_RE.md", "the launch-control patch, byte by byte, and the 404 checksum"],
                 ["docs/AAN_3B_port_notes.md", "AAN/ABY vs 3B comparison, prj file verdict"],
                 ["docs/3B_stage1_notes.md", "the OEM combo and stage-1 numbers"],
                 ["docs/3B_RS2_turbo_chipset.md", "the RS2-turbo chipset, table by table"],
                 ["docs/3B_injection_path_RE.md, 3B_load_headroom_RE.md", "how the 3B computes fuel and load, and where load stops"],
                 ["docs/3B_GT3071_plan.md", "the bigger-turbo build order and the 034 kit analysis"],
                 ["docs/ROADMAP.md", "what was built and in what order"]],
                widths=[62 * mm, 108 * mm])]

    flat = []
    for e in s:
        flat.extend(e) if isinstance(e, (list, tuple)) else flat.append(e)
    doc.build(flat, onFirstPage=footer, onLaterPages=footer)
    return OUT


if __name__ == "__main__":
    print(build())
