"""
UrROM — Open-source ROM editor for Bosch Motronic M2.3 / M2.3.2.

Supported ECU families:
  5-cylinder 2.2 20v Turbo (dual EPROM — main chip + boost chip):
    3B   — Audi 200 20vT / UrQ RR / S2 Coupe early   (895 907 404 BA)
    AAN  — UrS4 / UrS6                                (4A0 907 551 AA/B/C)
    ABY  — S2 Coupe late                              (895 907 551 A)
    ADU  — RS2 Avant                                  (8A0 907 551 A/B/C)
    RR   — UrQuattro 20vT (late)                      (895 907 404)

  V8 32v (single EPROM — no boost chip):
    PT   — Audi V8 3.6L                               (443/893 907 404)
    ABH  — Audi V8 4.2L                               (4A0 907 557 A)
"""

from .version import APP_VERSION, APP_NAME, WINDOW_TITLE

__version__ = APP_VERSION
__all__ = ["APP_VERSION", "APP_NAME", "WINDOW_TITLE"]
