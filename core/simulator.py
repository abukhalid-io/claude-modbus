"""
Simulator perangkat Modbus TCP - "pabrik mini" untuk mencoba semuanya tanpa
hardware: tangki air dengan pompa, pemanas, dan flowmeter yang nilainya
bergerak sendiri.

Peta register cocok dengan profiles/simulator.json.

Jalankan sendiri:
    python -m core.simulator            # tahan sampai Ctrl+C
"""

from __future__ import annotations

import asyncio
import math
import random
import threading
import time

from pymodbus.client.mixin import ModbusClientMixin
from pymodbus.datastore import (ModbusDeviceContext, ModbusSequentialDataBlock,
                                ModbusServerContext)
from pymodbus.server import ModbusTcpServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 15020
DATATYPE = ModbusClientMixin.DATATYPE

# Peta register (holding register, word order big-endian)
#   0-1   float32  suhu tangki (degC)
#   2-3   float32  tekanan (bar)
#   4     int16    level tangki x10 (%)
#   6-7   float32  laju alir (m3/jam)
#   8-9   uint32   penghitung total liter
#   10    uint16   kecepatan pompa (%)        <- bisa ditulis
#   12    int16    setpoint suhu x10 (degC)   <- bisa ditulis
#   20-27 string   nama perangkat
# Coil:
#   0   pompa jalan     <- bisa ditulis
#   1   pemanas jalan   <- bisa ditulis
#   2   katup buang     <- bisa ditulis
# Discrete input:
#   0   alarm level tinggi
#   1   alarm suhu tinggi
#   2   status siap


def _pack(value, dtype, count: int) -> list[int]:
    regs = ModbusClientMixin.convert_to_registers(value, dtype)
    return (regs + [0] * count)[:count]


class ModbusSimulator:
    """Server Modbus TCP kecil yang nilainya berubah tiap detik."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 unit_id: int = 1) -> None:
        self.host, self.port, self.unit_id = host, port, unit_id
        self.device = ModbusDeviceContext(
            hr=ModbusSequentialDataBlock(0, [0] * 64),
            ir=ModbusSequentialDataBlock(0, [0] * 32),
            co=ModbusSequentialDataBlock(0, [False] * 16),
            di=ModbusSequentialDataBlock(0, [False] * 16),
        )
        self.context = ModbusServerContext(devices=self.device, single=True)

        self._server: ModbusTcpServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._tick_thread: threading.Thread | None = None
        self._running = False
        self._t0 = time.time()
        self._total_liter = 0.0

        self._seed()

    # ── isi awal ──────────────────────────────────────────────
    def _seed(self) -> None:
        self.device.setValues(3, 10, [45])                   # kecepatan pompa 45 %
        self.device.setValues(3, 12, [650])                  # setpoint 65,0 degC
        self.device.setValues(3, 20, _pack("SIM-TANK-01", DATATYPE.STRING, 8))
        self.device.setValues(1, 0, [True, False, False])    # pompa jalan
        self._tick(first=True)

    # ── nilai bergerak ────────────────────────────────────────
    def _tick(self, first: bool = False) -> None:
        t = time.time() - self._t0
        pump_on = bool(self.device.getValues(1, 0, 1)[0])
        heater_on = bool(self.device.getValues(1, 1, 1)[0])
        drain_on = bool(self.device.getValues(1, 2, 1)[0])
        speed = self.device.getValues(3, 10, 1)[0]
        setpoint = self.device.getValues(3, 12, 1)[0] / 10.0

        base = setpoint if heater_on else 28.0
        suhu = base + math.sin(t / 13) * 2.4 + random.uniform(-0.25, 0.25)
        tekanan = (1.6 if pump_on else 0.2) * (speed / 50) + math.sin(t / 7) * 0.12
        tekanan = max(0.0, tekanan + random.uniform(-0.02, 0.02))
        alir = (speed * 0.14 if pump_on else 0.0) + random.uniform(-0.05, 0.05)
        alir = max(0.0, alir)
        level = 50 + math.sin(t / 21) * 28 - (12 if drain_on else 0)
        level = max(0.0, min(100.0, level))

        self._total_liter += alir * 1000 / 3600
        if first:
            self._total_liter = 128_450.0

        self.device.setValues(3, 0, _pack(round(suhu, 2), DATATYPE.FLOAT32, 2))
        self.device.setValues(3, 2, _pack(round(tekanan, 3), DATATYPE.FLOAT32, 2))
        self.device.setValues(3, 4, [int(level * 10)])
        self.device.setValues(3, 6, _pack(round(alir, 3), DATATYPE.FLOAT32, 2))
        self.device.setValues(3, 8, _pack(int(self._total_liter), DATATYPE.UINT32, 2))

        # input register: cermin nilai untuk menguji FC4
        self.device.setValues(4, 0, _pack(round(suhu, 2), DATATYPE.FLOAT32, 2))
        self.device.setValues(4, 2, [int(level * 10)])

        self.device.setValues(2, 0, [level > 85, suhu > 78, pump_on and tekanan > 0.5])

    def _tick_loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception:
                pass
            time.sleep(1.0)

    # ── hidup / mati ──────────────────────────────────────────
    def start(self, wait: float = 3.0) -> None:
        if self._running:
            return
        self._running = True
        ready = threading.Event()

        async def serve() -> None:
            # ModbusTcpServer harus dibuat SAAT event loop sudah jalan
            self._server = ModbusTcpServer(self.context,
                                           address=(self.host, self.port))
            ready.set()
            await self._server.serve_forever()

        def runner() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(serve())
            except Exception:
                pass
            finally:
                ready.set()
                self._loop.close()

        self._thread = threading.Thread(target=runner, name="modbus-sim", daemon=True)
        self._thread.start()
        ready.wait(timeout=wait)
        time.sleep(0.4)          # beri waktu socket listen

        self._tick_thread = threading.Thread(target=self._tick_loop,
                                             name="modbus-sim-tick", daemon=True)
        self._tick_thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            if self._server and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._server.shutdown(), self._loop)
                time.sleep(0.3)
                if self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass          # loop sudah tutup duluan setelah shutdown()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running and bool(self._thread and self._thread.is_alive())

    @property
    def url(self) -> str:
        return f"{self.host}:{self.port}"


if __name__ == "__main__":
    sim = ModbusSimulator()
    sim.start()
    print(f"Simulator Modbus TCP jalan di {sim.url} (unit {sim.unit_id}). Ctrl+C untuk berhenti.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sim.stop()
        print("berhenti")
