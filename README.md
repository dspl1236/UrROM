# UrROM

Open-source ROM editor for **Bosch Motronic M2.3 / M2.3.2** — the ECU family
used in Audi's legendary 5-cylinder 2.2 20v turbo and V8 engines.

## Supported ECUs

### 5-cylinder 2.2 20vT — dual EPROM (main chip + boost chip)

| Engine | ECU Part Number | Software | Application |
|--------|----------------|----------|-------------|
| 3B     | 895 907 404 BA | 404      | Audi 200 20vT / UrQuattro RR / S2 early |
| AAN    | 4A0 907 551 AA | 551AA    | UrS4 / UrS6 |
| ABY    | 895 907 551 A  | 551A     | S2 Coupe late |
| ADU    | 8A0 907 551    | 551B/C   | RS2 Avant |

### V8 32v — single EPROM

| Engine | ECU Part Number | Software | Application |
|--------|----------------|----------|-------------|
| PT     | 443 907 404 A  | 404V8    | Audi V8 3.6L |
| ABH    | 4A0 907 557 A  | 557      | Audi V8 4.2L |

## Status: v0.1.0 — Early development

- ROM normalisation, checksum verify/rewrite, variant detection
- Map read/write, rev limit, ignition encode/decode
- 42 unit tests passing
- GUI coming next

Map addresses are PROVISIONAL pending ROM contributions.
