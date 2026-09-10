# UrROM roadmap (agreed 2026-09-09)

Ordered. Each item is scoped so it can land on its own.

1. **Log-to-map.** *(landed 2026-09-09: recorder, live trace on table/heat/3D, CSV replay; mean-lambda per cell waits for a wideband)* Record KWPBridge live data to CSV inside UrROM; a trace
   overlay that paints where the engine actually ran (per-cell hit counts,
   mean lambda per cell once a wideband is present) on the table, the heat
   map and the 3D surface, live and from a replayed log.
2. **Compare as a first-class view.** *(landed 2026-09-09: cross-family pairing by role, resampling, Difference / Blend, Heat / 3D, raw toggle)* Two chips side by side, difference map
   in real units, cross-family resampling (today's `xcompare` CLI) in the app,
   with an A/B slider.
3. **Provenance on every byte.** *(landed 2026-09-09: urrom/provenance.py chains per family; editor line + cell tooltips on main and boost tabs)* Per-cell tooltip: where the address came from
   (descriptor / XDF / diff), which chips confirmed it, the decode formula and
   its source.
4. **Guard rails in the editor.** Sensor headroom, checksum, load-axis clamp,
   knock-retard region: warn while typing with the reason; a "what this edit
   changes" line in real units next to the raw byte.
5. **Undo you can read.** Session log as sentences, exportable as the tune's
   commit message.
6. **Bench mode.** Drive the loaded maps with a simulated engine (KWPBridge's
   mock) so the cursor walks the surfaces before a real ECU is involved.

Deliberately out: auto-tuning from a wideband; anything that needs an account
or a network.
