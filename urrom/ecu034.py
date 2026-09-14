"""
urrom/ecu034.py — read 034EFI Rip Chip ECU definition files (.ecu / .ECU).

They are Java-serialised com.efi.store.EcuV1 objects holding a list of
com.efi.store.MapV1: data address, dims, scale factor/offset, axis addresses.
Needs the optional javaobj-py3 package; load_definition() raises ImportError
with a hint when it is missing.  Addresses are plain file offsets into the
descrambled image (32 KB for the 3B, 64 KB for the 551).

Caveat learned on the 3B definition: 034's axis addresses point at the Bosch
descriptor's delta bytes and their software never cumulated them, so the axis
labels it shows are wrong.  UrROM's descriptor decode gives the real ones;
use the addresses and scales from here, the axes from ecu_profiles.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class Map034:
    name: str
    data_addr: int
    rows: int
    cols: int
    factor: float
    offset: float
    eight_bit: bool
    x_label: str
    x_addr: int
    x_factor: float
    x_offset: float
    y_label: str
    y_addr: int
    y_factor: float
    y_offset: float
    description: str = ""

    def decode(self, raw: int) -> float:
        return raw * self.factor + self.offset


@dataclass
class Definition034:
    ecu_name: str
    description: str
    ecu_size_kb: int
    checksum_from: int
    checksum_to: int
    checksum_store: int
    checksum_disabled: bool
    id_bytes_addr: int
    id_bytes: bytes
    maps: list[Map034] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self); d["id_bytes"] = self.id_bytes.hex(); return d


def load_definition(path: str | Path) -> Definition034:
    try:
        import javaobj
    except ImportError as e:                     # pragma: no cover
        raise ImportError("reading .ecu files needs `pip install javaobj-py3`") from e
    obj = javaobj.loads(Path(path).read_bytes())
    maps = []
    for m in getattr(obj, "maps", []) or []:
        g = lambda k, d=0: getattr(m, k, d)
        maps.append(Map034(
            name=str(g("mapName", "")), data_addr=int(g("dataLowStart")),
            rows=int(g("dimsLowRows")), cols=int(g("dimsLowCols")),
            factor=float(g("dataFactor", 1.0)), offset=float(g("dataOffset", 0.0)),
            eight_bit=bool(g("data8Bit", True)),
            x_label=str(g("xAxisLabel", "")), x_addr=int(g("xAxisLowStart")),
            x_factor=float(g("xAxisFactor", 1.0)), x_offset=float(g("xAxisOffset", 0.0)),
            y_label=str(g("yAxisLabel", "")), y_addr=int(g("yAxisLowStart")),
            y_factor=float(g("yAxisFactor", 1.0)), y_offset=float(g("yAxisOffset", 0.0)),
            description=str(g("description", "") or "")))
    eg = getattr(obj, "eg5BinBytes", None) or []
    return Definition034(
        ecu_name=str(getattr(obj, "ecuName", "")), description=str(getattr(obj, "description", "") or ""),
        ecu_size_kb=int(getattr(obj, "ecuSize", 0)),
        checksum_from=int(getattr(obj, "csAddressRangeFrom", 0)), checksum_to=int(getattr(obj, "csAddressRangeTo", 0)),
        checksum_store=int(getattr(obj, "csCorrectionFrom", 0)), checksum_disabled=bool(getattr(obj, "disableChecksum", False)),
        id_bytes_addr=int(getattr(obj, "egBytesFromAddress", 0)), id_bytes=bytes(b & 0xFF for b in eg), maps=maps)
