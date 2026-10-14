# rs2_xdf

`m232.xdf` is prj's TunerPro definition for the Motronic 2.3.2 (ADU / prjmod
551AA_0202 layout), copied unmodified from github.com/prj/m232 (`TunerPro/m232.xdf`,
GPL-3.0, written 2018-01-07). UrROM auto-loads it for prjmod chips (File → Import
XDF does the same for any XDF) and the XDF parser tests run against it.

The two files the tests originally expected ("RS2 551B fuel timing.xdf",
"8D0907551B RS2 Boost.xdf") were never published; this one covers the fuel/ign
chip. No public XDF exists for the 551 boost chip — UrROM's own boost profile
(docs/3B_boost_chip_RE.md, urrom/ecu_profiles.py) is the definition there.
