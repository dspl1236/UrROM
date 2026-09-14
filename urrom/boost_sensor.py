"""
Boost (MAP) sensor scale for the boost-chip pressure tables.

The boost MCU reads the pressure sensor on an 8-bit ADC against a 5 V
reference, so a table byte is a sensor *voltage* (raw / 255 * 5 V), not a
pressure.  Pressure follows from the sensor's transfer function, which the
ROM does not know.  Everything that shows or edits a boost target in kPa
therefore goes through the per-family sensor selected here:

    kPa_abs = raw / 255 * span + offset

Ratiometric sensors have a non-zero offset (an MPX4250A reads 10 kPa at
0 V, not 0 kPa), so "250 kPa sensor" and "raw/255*250" are not the same
thing.  Datasheet transfer functions:

    MPX4250A   Vout = Vs * (0.004   * P - 0.04)    -> P = raw/255 * 250   + 10
    MPXH6400A  Vout = Vs * (0.002421* P - 0.00842) -> P = raw/255 * 413.05 + 3.48

The Bosch sensor on the 3B/RR boost board is assumed linear 0..200 kPa
until a logged reading on a car settles it (docs/3B_boost_chip_RE.md).

Display can be absolute kPa or gauge bar ((kPa - ATM_KPA) / 100).
State is per chip family ("404" = 3B/RR/S2 boost board, "551" = AAN/ABY/
ADU/RS2 boost chip) and is what the MapDef decode/encode callables read.
"""
from __future__ import annotations

from dataclasses import dataclass

ATM_KPA = 100.0   # gauge reference; 1 bar gauge = 200 kPa abs


@dataclass(frozen=True)
class BoostSensor:
    key: str
    name: str
    span_kpa: float      # kPa per full ADC scale (5 V)
    offset_kpa: float    # kPa at 0 V
    note: str = ""

    def kpa(self, raw: int) -> float:
        return raw / 255 * self.span_kpa + self.offset_kpa

    def raw(self, kpa: float) -> int:
        return max(0, min(255, round((kpa - self.offset_kpa) / self.span_kpa * 255)))

    def volts_at(self, kpa: float) -> float:
        """Sensor output at a given absolute pressure (5 V supply)."""
        return max(0.0, min(5.0, (kpa - self.offset_kpa) / self.span_kpa * 5.0))


SENSORS: list[BoostSensor] = [
    BoostSensor("bosch200", "Bosch 200 kPa (3B/RR/S2 boost board, assumed linear)",
                200.0, 0.0, "Stock 404 boost board. Scale ASSUMED 0..200 kPa linear; "
                            "confirm with a logged boost reading."),
    BoostSensor("mpx4250", "MPX4250A 250 kPa (AAN/ABY stock, QLCC)",
                250.0, 10.0, "Vout = Vs(0.004 P - 0.04): P = raw/255*250 + 10 kPa."),
    BoostSensor("lin250", "Generic linear 250 kPa", 250.0, 0.0,
                "raw/255*250. Use when the fitted 250 kPa sensor is not an MPX4250A."),
    BoostSensor("lin300", "Generic linear 300 kPa (RS2 R201 swap)", 300.0, 0.0,
                "raw/255*300. RS2 R201 sensor; transfer not verified."),
    BoostSensor("vmap300_034", "034 3-bar VMAP (Rip Chip scale: 300 kPa span, 21 kPa at 0 V)",
                300.0, 21.0, "From 034's AAN Boost R2 definition: psi gauge = raw*0.170588 - 11.5, "
                             "i.e. kPa abs = raw/255*300 + 21. Their 26 psi overboost is raw 220, "
                             "which is the max in their GT2871/GT3071 boost chips."),
    BoostSensor("mpxh6400", "MPXH6400A 400 kPa (prjmod SD)", 413.05, 3.48,
                "Vout = Vs(0.002421 P - 0.00842): P = raw/255*413.05 + 3.48 kPa."),
]

_BY_KEY = {s.key: s for s in SENSORS}

# family -> [sensor, display mode]
_DEFAULTS = {"404": "bosch200", "551": "mpx4250"}
_state: dict[str, list] = {f: [_BY_KEY[k], "kpa"] for f, k in _DEFAULTS.items()}


def family_of(software_id: str) -> str:
    return "551" if (software_id or "").startswith(("551", "557")) else "404"


def sensors() -> list[BoostSensor]:
    return list(SENSORS)


def get_sensor(family: str) -> BoostSensor:
    return _state[family][0]


def set_sensor(family: str, key_or_sensor: str | BoostSensor) -> BoostSensor:
    s = key_or_sensor if isinstance(key_or_sensor, BoostSensor) else _BY_KEY[key_or_sensor]
    _state[family][0] = s
    return s


def set_custom(family: str, span_kpa: float, offset_kpa: float = 0.0,
               name: str = "") -> BoostSensor:
    s = BoostSensor("custom", name or f"Custom {span_kpa:g} kPa span, {offset_kpa:g} kPa offset",
                    float(span_kpa), float(offset_kpa), "User-entered transfer function.")
    _state[family][0] = s
    return s


def get_display(family: str) -> str:
    return _state[family][1]


KPA_PER_PSI = 6.894757


def set_display(family: str, mode: str) -> None:
    if mode not in ("kpa", "bar", "psi"):
        raise ValueError(mode)
    _state[family][1] = mode


def unit(family: str) -> str:
    return {"bar": "bar gauge", "psi": "psi gauge"}.get(get_display(family), "kPa abs")


def decode(raw: int, family: str) -> float:
    kpa = get_sensor(family).kpa(raw)
    mode = get_display(family)
    if mode == "bar":
        return round((kpa - ATM_KPA) / 100.0, 2)
    if mode == "psi":
        return round((kpa - ATM_KPA) / KPA_PER_PSI, 1)
    return round(kpa, 1)


def encode(value: float, family: str) -> int:
    mode = get_display(family)
    if mode == "bar":
        kpa = value * 100.0 + ATM_KPA
    elif mode == "psi":
        kpa = value * KPA_PER_PSI + ATM_KPA
    else:
        kpa = value
    return get_sensor(family).raw(kpa)


def kpa_to_bar_gauge(kpa: float) -> float:
    return round((kpa - ATM_KPA) / 100.0, 2)


def kpa_to_psi_gauge(kpa: float) -> float:
    return round((kpa - ATM_KPA) / KPA_PER_PSI, 1)


def identify(volts: float, kpa: float = ATM_KPA) -> list[tuple[BoostSensor, float, float]]:
    """
    Rank the known sensors by how well a measured output voltage at a known
    pressure (default: key on, engine off, ~100 kPa) matches their transfer
    function.  Returns [(sensor, predicted_volts, error_volts)] best first.
    """
    rows = []
    for s in SENSORS:
        v = s.volts_at(kpa)
        rows.append((s, round(v, 2), round(abs(v - volts), 2)))
    rows.sort(key=lambda r: r[2])
    return rows
