"""
ModbusEngine - satu mesin yang dipakai bersama oleh GUI dan MCP server.

Tugasnya: menyimpan profil perangkat, membuka koneksi Modbus TCP/RTU, membaca
dan menulis titik ukur (sudah diskalakan + satuan), menjalankan polling latar,
dan mencatat riwayat ke SQLite.

Pengaman tulis: setiap perintah tulis ditolak kecuali profil perangkat memberi
`allow_write: true` DAN titik ukurnya `writable: true`.
"""

from __future__ import annotations

import threading
import time

from pymodbus.client import ModbusSerialClient, ModbusTcpClient
from pymodbus.client.mixin import ModbusClientMixin

from core import profiles as profile_store
from core.history import History
from core.models import (BIT_TABLES, DATATYPES, READ_ONLY_TABLES, Device,
                         ModbusConfigError, Point, Reading)

# kode fungsi untuk datastore & pembacaan
READ_FN = {"holding": "read_holding_registers", "input": "read_input_registers",
           "coil": "read_coils", "discrete": "read_discrete_inputs"}

MAX_REGISTERS = 125          # batas satu permintaan Modbus
MAX_BITS = 2000


class ModbusError(RuntimeError):
    """Gagal berkomunikasi dengan perangkat."""


class WriteRefused(PermissionError):
    """Perintah tulis ditolak pengaman."""


class ModbusEngine:
    def __init__(self, profiles_dir: str | None = None,
                 db_path: str | None = None) -> None:
        self.devices: dict[str, Device] = {}
        self._clients: dict[str, object] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._pollers: dict[str, _Poller] = {}
        self.latest: dict[str, dict[str, Reading]] = {}
        self.profiles_dir = profiles_dir or profile_store.DEFAULT_DIR
        self.history = History(db_path) if db_path else History()
        self.log: list[dict] = []
        self.reload_profiles()

    # ══════════════════════════════════════════════════════════
    #  PROFIL
    # ══════════════════════════════════════════════════════════
    def reload_profiles(self) -> list[Device]:
        self.devices = {d.id: d for d in profile_store.load_all(self.profiles_dir)}
        for did in self.devices:
            self._locks.setdefault(did, threading.Lock())
            self.latest.setdefault(did, {})
        return list(self.devices.values())

    def device(self, device_id: str) -> Device:
        if device_id not in self.devices:
            tersedia = ", ".join(self.devices) or "(belum ada perangkat)"
            raise ModbusConfigError(
                f"perangkat '{device_id}' tidak ada. Yang tersedia: {tersedia}")
        return self.devices[device_id]

    def add_device(self, data: dict) -> Device:
        dev = data if isinstance(data, Device) else Device(**data)
        if dev.id in self.devices:
            raise ModbusConfigError(f"perangkat '{dev.id}' sudah ada")
        self.devices[dev.id] = dev
        self._locks[dev.id] = threading.Lock()
        self.latest[dev.id] = {}
        profile_store.save(dev, self.profiles_dir)
        self._note(f"perangkat '{dev.id}' ditambahkan")
        return dev

    def update_device(self, device_id: str, changes: dict) -> Device:
        dev = self.device(device_id)
        data = dev.to_dict()
        data.update({k: v for k, v in changes.items() if k not in ("id", "points")})
        if "points" in changes:
            data["points"] = changes["points"]
        new = Device(**data)
        was_connected = self.is_connected(device_id)
        self.disconnect(device_id)
        self.devices[device_id] = new
        profile_store.save(new, self.profiles_dir)
        if was_connected:
            self.connect(device_id)
        return new

    def remove_device(self, device_id: str) -> None:
        self.stop_polling(device_id)
        self.disconnect(device_id)
        self.devices.pop(device_id, None)
        self.latest.pop(device_id, None)
        profile_store.delete(device_id, self.profiles_dir)
        self._note(f"perangkat '{device_id}' dihapus")

    def add_point(self, device_id: str, data: dict) -> Point:
        dev = self.device(device_id)
        point = data if isinstance(data, Point) else Point(**data)
        if any(p.name == point.name for p in dev.points):
            raise ModbusConfigError(
                f"point '{point.name}' sudah ada di perangkat '{device_id}'")
        dev.points.append(point)
        profile_store.save(dev, self.profiles_dir)
        return point

    def remove_point(self, device_id: str, name: str) -> None:
        dev = self.device(device_id)
        dev.point(name)                       # lempar kalau tidak ada
        dev.points = [p for p in dev.points if p.name != name]
        self.latest.get(device_id, {}).pop(name, None)
        profile_store.save(dev, self.profiles_dir)

    # ══════════════════════════════════════════════════════════
    #  KONEKSI
    # ══════════════════════════════════════════════════════════
    def connect(self, device_id: str) -> bool:
        dev = self.device(device_id)
        if self.is_connected(device_id):
            return True
        if dev.transport == "tcp":
            client = ModbusTcpClient(dev.host, port=dev.port, timeout=dev.timeout,
                                     retries=1)
        else:
            client = ModbusSerialClient(
                dev.serial_port, baudrate=dev.baudrate, bytesize=dev.bytesize,
                parity=dev.parity, stopbits=dev.stopbits, timeout=dev.timeout,
                retries=1)
        ok = bool(client.connect())
        if not ok:
            try:
                client.close()
            except Exception:
                pass
            raise ModbusError(
                f"gagal terhubung ke '{dev.id}' di {dev.endpoint}. "
                "Periksa alamat/port, kabel, dan apakah perangkat menyala.")
        self._clients[device_id] = client
        self._note(f"'{dev.id}' terhubung di {dev.endpoint}")
        return True

    def disconnect(self, device_id: str) -> None:
        client = self._clients.pop(device_id, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            self._note(f"'{device_id}' diputus")

    def is_connected(self, device_id: str) -> bool:
        client = self._clients.get(device_id)
        return bool(client is not None and getattr(client, "connected", False))

    def status(self) -> list[dict]:
        out = []
        for did, dev in self.devices.items():
            poller = self._pollers.get(did)
            out.append({
                "id": did, "name": dev.name, "transport": dev.transport,
                "endpoint": dev.endpoint, "unit_id": dev.unit_id,
                "connected": self.is_connected(did),
                "allow_write": dev.allow_write,
                "points": len(dev.points),
                "polling": bool(poller and poller.running),
                "poll_interval": poller.interval if poller else dev.poll_interval,
                "last_values": len(self.latest.get(did, {})),
            })
        return out

    def close(self) -> None:
        for did in list(self._pollers):
            self.stop_polling(did)
        for did in list(self._clients):
            self.disconnect(did)
        self.history.close()

    # ══════════════════════════════════════════════════════════
    #  BACA
    # ══════════════════════════════════════════════════════════
    def _client(self, device_id: str):
        if not self.is_connected(device_id):
            self.connect(device_id)
        return self._clients[device_id]

    def read_raw(self, device_id: str, table: str, address: int,
                 count: int = 1) -> list:
        """Baca register/bit mentah tanpa penafsiran tipe data."""
        dev = self.device(device_id)
        if table not in READ_FN:
            raise ModbusConfigError(
                f"table '{table}' tidak dikenal. Pilih: {', '.join(READ_FN)}")
        cap = MAX_BITS if table in BIT_TABLES else MAX_REGISTERS
        if not 1 <= count <= cap:
            raise ModbusConfigError(f"count untuk table '{table}' harus 1..{cap}")
        client = self._client(device_id)
        with self._locks[device_id]:
            fn = getattr(client, READ_FN[table])
            try:
                rr = fn(address, count=count, device_id=dev.unit_id)
            except Exception as exc:
                raise ModbusError(f"gagal baca {table}@{address} dari '{device_id}': {exc}")
        if rr.isError():
            raise ModbusError(
                f"perangkat '{device_id}' menolak baca {table}@{address} "
                f"x{count}: {rr}. Cek alamat awal (sebagian alat memakai "
                "penomoran 4xxxx / offset 1) dan unit id.")
        return list(rr.bits[:count]) if table in BIT_TABLES else list(rr.registers)

    def read_point(self, device_id: str, name: str) -> Reading:
        dev = self.device(device_id)
        point = dev.point(name)
        try:
            raw = self.read_raw(device_id, point.table, point.address,
                                point.word_count)
        except (ModbusError, ModbusConfigError) as exc:
            r = Reading(device=device_id, point=name, value=None, unit=point.unit,
                        quality="error", error=str(exc))
            self.latest.setdefault(device_id, {})[name] = r
            return r
        return self._decode(dev, point, raw)

    def _decode(self, dev: Device, point: Point, raw: list) -> Reading:
        if point.table in BIT_TABLES:
            value = bool(raw[0])
        else:
            dtype = DATATYPES[point.datatype]
            value = ModbusClientMixin.convert_from_registers(
                [int(x) for x in raw], dtype, word_order=point.word_order)
            if isinstance(value, (int, float)):
                value = round(point.apply_scale(value), 6)
            elif isinstance(value, str):
                value = value.rstrip("\x00").strip()
        r = Reading(device=dev.id, point=point.name, value=value, unit=point.unit,
                    raw=[int(x) for x in raw] if point.table not in BIT_TABLES
                        else [int(bool(raw[0]))],
                    alarm=point.alarm_state(value))
        self.latest.setdefault(dev.id, {})[point.name] = r
        return r

    def read_all(self, device_id: str, store: bool = False) -> list[Reading]:
        dev = self.device(device_id)
        out = [self.read_point(device_id, p.name) for p in dev.points]
        if store and out:
            self.history.append(out)
        return out

    # ══════════════════════════════════════════════════════════
    #  TULIS (dijaga)
    # ══════════════════════════════════════════════════════════
    def _guard(self, dev: Device, point: Point | None = None) -> None:
        if not dev.allow_write:
            raise WriteRefused(
                f"menulis ke '{dev.id}' ditolak: profil perangkat memakai "
                "allow_write=false. Nyalakan sendiri di GUI (halaman Perangkat) "
                "atau ubah profilnya kalau memang mau menulis ke alat ini.")
        if point is not None:
            if point.table in READ_ONLY_TABLES:
                raise WriteRefused(
                    f"point '{point.name}' ada di table '{point.table}' yang "
                    "menurut standar Modbus hanya bisa dibaca")
            if not point.writable:
                raise WriteRefused(
                    f"point '{point.name}' ditandai writable=false di profil")

    def write_point(self, device_id: str, name: str, value) -> Reading:
        dev = self.device(device_id)
        point = dev.point(name)
        self._guard(dev, point)

        if point.table == "coil":
            regs = bool(value)
        else:
            if isinstance(value, bool):
                raise ModbusConfigError(
                    f"point '{name}' butuh angka, bukan true/false")
            scaled = point.remove_scale(float(value))
            if point.datatype in ("int16", "uint16", "int32", "uint32",
                                  "int64", "uint64"):
                scaled = int(round(scaled))
            regs = ModbusClientMixin.convert_to_registers(
                scaled, DATATYPES[point.datatype], word_order=point.word_order)

        self._write(dev, point.table, point.address, regs)
        self._note(f"tulis '{device_id}.{name}' = {value}")
        return self.read_point(device_id, name)

    def write_raw(self, device_id: str, table: str, address: int, values: list):
        dev = self.device(device_id)
        self._guard(dev)
        if table not in ("holding", "coil"):
            raise ModbusConfigError(
                "hanya table 'holding' dan 'coil' yang bisa ditulis")
        payload = ([bool(v) for v in values] if table == "coil"
                   else [int(v) & 0xFFFF for v in values])
        self._write(dev, table, address, payload)
        self._note(f"tulis mentah '{device_id}' {table}@{address} = {payload}")
        return self.read_raw(device_id, table, address, len(payload))

    def _write(self, dev: Device, table: str, address: int, payload) -> None:
        client = self._client(dev.id)
        with self._locks[dev.id]:
            try:
                if table == "coil":
                    rr = (client.write_coil(address, bool(payload), device_id=dev.unit_id)
                          if isinstance(payload, bool) else
                          client.write_coils(address, list(payload), device_id=dev.unit_id))
                else:
                    regs = payload if isinstance(payload, list) else [payload]
                    rr = (client.write_register(address, regs[0], device_id=dev.unit_id)
                          if len(regs) == 1 else
                          client.write_registers(address, regs, device_id=dev.unit_id))
            except Exception as exc:
                raise ModbusError(f"gagal tulis {table}@{address} ke '{dev.id}': {exc}")
        if rr.isError():
            raise ModbusError(
                f"perangkat '{dev.id}' menolak tulis {table}@{address}: {rr}")

    # ══════════════════════════════════════════════════════════
    #  POLLING
    # ══════════════════════════════════════════════════════════
    def start_polling(self, device_id: str, interval: float | None = None) -> float:
        dev = self.device(device_id)
        if not dev.points:
            raise ModbusConfigError(
                f"perangkat '{device_id}' belum punya point untuk dipoll")
        self.stop_polling(device_id)
        self.connect(device_id)
        sec = max(0.2, float(interval or dev.poll_interval))
        poller = _Poller(self, device_id, sec)
        self._pollers[device_id] = poller
        poller.start()
        self._note(f"polling '{device_id}' tiap {sec}s")
        return sec

    def stop_polling(self, device_id: str) -> None:
        poller = self._pollers.pop(device_id, None)
        if poller:
            poller.stop()
            self._note(f"polling '{device_id}' dihentikan")

    def is_polling(self, device_id: str) -> bool:
        p = self._pollers.get(device_id)
        return bool(p and p.running)

    # ══════════════════════════════════════════════════════════
    #  SCAN
    # ══════════════════════════════════════════════════════════
    def scan_units(self, device_id: str, start: int = 1, end: int = 16,
                   table: str = "holding", address: int = 0) -> list[dict]:
        """Coba baca 1 register ke tiap unit id, lihat siapa yang menjawab."""
        dev = self.device(device_id)
        found = []
        client = self._client(device_id)
        fn_name = READ_FN.get(table, "read_holding_registers")
        for uid in range(max(0, start), min(255, end) + 1):
            with self._locks[device_id]:
                try:
                    rr = getattr(client, fn_name)(address, count=1, device_id=uid)
                    ok = not rr.isError()
                    detail = (str(list(rr.registers)) if ok and hasattr(rr, "registers")
                              else str(list(rr.bits)[:1]) if ok else str(rr))
                except Exception as exc:
                    ok, detail = False, str(exc)
            found.append({"unit_id": uid, "responded": ok, "detail": detail})
        aktif = [f["unit_id"] for f in found if f["responded"]]
        self._note(f"scan unit '{device_id}' {start}-{end}: {aktif or 'tidak ada'}")
        return found

    def scan_registers(self, device_id: str, table: str, start: int,
                       end: int, chunk: int = 8) -> list[dict]:
        """Sapu blok alamat, laporkan blok mana yang bisa dibaca dan isinya."""
        out = []
        addr = start
        while addr <= end:
            count = min(chunk, end - addr + 1)
            try:
                values = self.read_raw(device_id, table, addr, count)
                out.append({"address": addr, "count": count, "ok": True,
                            "values": values})
            except (ModbusError, ModbusConfigError) as exc:
                out.append({"address": addr, "count": count, "ok": False,
                            "error": str(exc)[:160]})
            addr += count
        return out

    # ══════════════════════════════════════════════════════════
    def _note(self, text: str) -> None:
        self.log.append({"ts": time.time(), "text": text})
        if len(self.log) > 500:
            del self.log[:100]


class _Poller(threading.Thread):
    def __init__(self, engine: ModbusEngine, device_id: str, interval: float) -> None:
        super().__init__(name=f"poll-{device_id}", daemon=True)
        self.engine, self.device_id, self.interval = engine, device_id, interval
        self.running = False
        self.cycles = 0
        self.errors = 0
        self._halt = threading.Event()

    def run(self) -> None:
        self.running = True
        while not self._halt.is_set():
            t0 = time.perf_counter()
            try:
                readings = self.engine.read_all(self.device_id)
                self.engine.history.append(readings)
                self.errors += sum(1 for r in readings if r.quality != "good")
                self.cycles += 1
            except Exception:
                self.errors += 1
            sisa = self.interval - (time.perf_counter() - t0)
            if self._halt.wait(max(0.05, sisa)):
                break
        self.running = False

    def stop(self) -> None:
        self._halt.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=self.interval + 1.5)
