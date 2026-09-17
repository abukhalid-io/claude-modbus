"""
Model data Claude Modbus: titik ukur (point), profil perangkat, dan hasil baca.

Semua murni dataclass + fungsi konversi, tanpa I/O, supaya gampang diuji:
    python -m core.models
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from pymodbus.client.mixin import ModbusClientMixin

DATATYPES = {
    "int16": ModbusClientMixin.DATATYPE.INT16,
    "uint16": ModbusClientMixin.DATATYPE.UINT16,
    "int32": ModbusClientMixin.DATATYPE.INT32,
    "uint32": ModbusClientMixin.DATATYPE.UINT32,
    "int64": ModbusClientMixin.DATATYPE.INT64,
    "uint64": ModbusClientMixin.DATATYPE.UINT64,
    "float32": ModbusClientMixin.DATATYPE.FLOAT32,
    "float64": ModbusClientMixin.DATATYPE.FLOAT64,
    "string": ModbusClientMixin.DATATYPE.STRING,
}

# berapa register 16-bit yang dipakai tiap tipe
WORDS = {"int16": 1, "uint16": 1, "int32": 2, "uint32": 2,
         "int64": 4, "uint64": 4, "float32": 2, "float64": 4, "bool": 1}

TABLES = {
    "holding": "Holding register (FC3/FC6/FC16, baca-tulis)",
    "input": "Input register (FC4, hanya baca)",
    "coil": "Coil (FC1/FC5, bit baca-tulis)",
    "discrete": "Discrete input (FC2, bit hanya baca)",
}

BIT_TABLES = ("coil", "discrete")
READ_ONLY_TABLES = ("input", "discrete")


class ModbusConfigError(ValueError):
    """Profil perangkat / titik ukur tidak masuk akal."""


@dataclass
class Point:
    """Satu titik ukur: alamat register + cara menafsirkannya."""

    name: str
    address: int
    table: str = "holding"
    datatype: str = "uint16"
    word_order: str = "big"          # 'big' = word tinggi dulu, 'little' = terbalik
    length: int = 1                  # khusus datatype 'string' (jumlah register)
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    description: str = ""
    writable: bool = False
    alarm_low: float | None = None
    alarm_high: float | None = None

    def __post_init__(self) -> None:
        if self.table not in TABLES:
            raise ModbusConfigError(
                f"table '{self.table}' tidak dikenal. Pilih salah satu: "
                f"{', '.join(TABLES)}")
        if self.table in BIT_TABLES:
            self.datatype = "bool"
        elif self.datatype not in DATATYPES:
            raise ModbusConfigError(
                f"datatype '{self.datatype}' tidak dikenal. Pilih salah satu: "
                f"{', '.join(DATATYPES)}, atau pakai table coil/discrete untuk bit")
        if self.word_order not in ("big", "little"):
            raise ModbusConfigError("word_order harus 'big' atau 'little'")
        if self.address < 0 or self.address > 65535:
            raise ModbusConfigError("address harus 0..65535")
        if self.table in READ_ONLY_TABLES and self.writable:
            raise ModbusConfigError(
                f"table '{self.table}' hanya bisa dibaca, jadi writable harus false")

    @property
    def word_count(self) -> int:
        if self.datatype == "string":
            return max(1, self.length)
        return WORDS.get(self.datatype, 1)

    def apply_scale(self, raw: float) -> float:
        return raw * self.scale + self.offset

    def remove_scale(self, value: float) -> float:
        if self.scale == 0:
            raise ModbusConfigError(f"point '{self.name}' punya scale 0, tidak bisa ditulis")
        return (value - self.offset) / self.scale

    def alarm_state(self, value) -> str:
        """'low' | 'high' | 'ok' | '' (kalau tidak ada batas / bukan angka)."""
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return ""
        if self.alarm_low is None and self.alarm_high is None:
            return ""
        if self.alarm_low is not None and value < self.alarm_low:
            return "low"
        if self.alarm_high is not None and value > self.alarm_high:
            return "high"
        return "ok"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Device:
    """Profil satu perangkat Modbus."""

    id: str
    name: str = ""
    transport: str = "tcp"           # tcp | rtu
    host: str = "127.0.0.1"
    port: int = 502
    serial_port: str = "COM1"
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    unit_id: int = 1
    timeout: float = 3.0
    allow_write: bool = False        # pengaman: tulis ditolak kecuali dinyalakan
    poll_interval: float = 2.0
    note: str = ""
    points: list[Point] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ModbusConfigError("device id tidak boleh kosong")
        if self.transport not in ("tcp", "rtu"):
            raise ModbusConfigError("transport harus 'tcp' atau 'rtu'")
        if not 0 <= self.unit_id <= 255:
            raise ModbusConfigError("unit_id harus 0..255")
        self.name = self.name or self.id
        self.points = [p if isinstance(p, Point) else Point(**p) for p in self.points]
        names = [p.name for p in self.points]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ModbusConfigError(f"nama point ganda: {', '.join(sorted(dup))}")

    @property
    def endpoint(self) -> str:
        return (f"{self.host}:{self.port}" if self.transport == "tcp"
                else f"{self.serial_port}@{self.baudrate}-{self.bytesize}{self.parity}{self.stopbits}")

    def point(self, name: str) -> Point:
        for p in self.points:
            if p.name == name:
                return p
        tersedia = ", ".join(p.name for p in self.points) or "(belum ada point)"
        raise ModbusConfigError(
            f"point '{name}' tidak ada di perangkat '{self.id}'. Yang tersedia: {tersedia}")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["points"] = [p.to_dict() for p in self.points]
        return d


@dataclass
class Reading:
    """Hasil satu pembacaan titik ukur."""

    device: str
    point: str
    value: float | int | bool | str | None
    unit: str = ""
    raw: list[int] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    quality: str = "good"            # good | error
    error: str = ""
    alarm: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


if __name__ == "__main__":
    p = Point(name="suhu", address=0, datatype="float32", scale=1.0, unit="degC",
              alarm_high=80)
    assert p.word_count == 2
    assert p.apply_scale(25.5) == 25.5
    assert p.alarm_state(90) == "high" and p.alarm_state(30) == "ok"

    s = Point(name="skala", address=4, datatype="int16", scale=0.1, offset=-5)
    assert abs(s.apply_scale(300) - 25.0) < 1e-9
    assert abs(s.remove_scale(25.0) - 300) < 1e-9

    b = Point(name="pompa", address=0, table="coil")
    assert b.datatype == "bool" and b.word_count == 1

    d = Device(id="sim", name="Simulator", points=[p, s, b])
    assert d.point("suhu") is p
    assert d.endpoint == "127.0.0.1:502"

    for bad, pesan in (
        (lambda: Point(name="x", address=0, table="salah"), "table"),
        (lambda: Point(name="x", address=0, datatype="float24"), "datatype"),
        (lambda: Point(name="x", address=0, table="input", writable=True), "hanya bisa dibaca"),
        (lambda: Device(id="", name="kosong"), "id"),
        (lambda: Device(id="a", points=[p, p]), "ganda"),
    ):
        try:
            bad()
        except ModbusConfigError as exc:
            assert pesan in str(exc), (pesan, exc)
        else:
            raise AssertionError(f"harusnya gagal: {pesan}")

    print("semua tes model lulus")
