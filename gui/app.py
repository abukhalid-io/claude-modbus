"""
Claude Modbus - GUI.

Jendela pywebview (HTML/CSS/JS) di atas ModbusEngine yang sama dengan yang
dipakai MCP server, jadi apa yang kamu lihat di sini adalah data yang juga
dibaca Claude.

Jalankan:  python -m gui.app      (atau klik JALANKAN.bat)
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

try:
    import webview
except ImportError:
    print("pywebview belum terpasang.\n\n    pip install pywebview\n")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import ModbusEngine, ModbusError, WriteRefused  # noqa: E402
from core.models import DATATYPES, TABLES, ModbusConfigError  # noqa: E402
from core.serialports import list_serial_ports  # noqa: E402
from core.simulator import ModbusSimulator  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WEB = os.path.join(HERE, "web")
PREFS = os.path.join(ROOT, "data", "gui.json")
ICON = os.path.join(ROOT, "assets", "icon.ico")
APP_ID = "abukhalid.claude-modbus"
TITLE = "Claude Modbus  -  pembaca & pengendali perangkat Modbus"


def _set_app_id() -> None:
    """Taskbar Windows mengelompokkan per AppUserModelID; tanpa ini jendela
    kita menumpang ikon python.exe. Harus dipanggil sebelum jendela dibuat."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _apply_window_icon() -> bool:
    """Pasang icon.ico ke jendela (judul + taskbar) lewat WM_SETICON."""
    if sys.platform != "win32" or not os.path.exists(ICON):
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, TITLE)
        if not hwnd:
            return False
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x0010, 0x0080
        for size, which in ((32, 1), (16, 0)):        # 1 = ICON_BIG, 0 = ICON_SMALL
            handle = user32.LoadImageW(None, ICON, IMAGE_ICON, size, size,
                                       LR_LOADFROMFILE)
            if handle:
                user32.SendMessageW(hwnd, WM_SETICON, which, handle)
        return True
    except Exception:
        return False


def _prefs() -> dict:
    try:
        with open(PREFS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"theme": "light"}


def _save_prefs(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(PREFS), exist_ok=True)
        with open(PREFS, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def result(fn):
    """Ubah lemparan galat jadi {'ok': False, 'error': ...} untuk JS."""
    def wrapper(*a, **kw):
        try:
            out = fn(*a, **kw)
            if isinstance(out, dict) and "ok" in out:
                return out
            return {"ok": True, "data": out}
        except (WriteRefused, ModbusError, ModbusConfigError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:                        # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    wrapper.__name__ = fn.__name__
    return wrapper


class Api:
    def __init__(self) -> None:
        self.engine = ModbusEngine()
        self.sim: ModbusSimulator | None = None
        self.prefs = _prefs()

    # ── awal ──────────────────────────────────────────────────
    def boot(self) -> dict:
        return {
            "prefs": self.prefs,
            "devices": self.engine.status(),
            "tables": TABLES,
            "datatypes": list(DATATYPES) + ["bool (pakai table coil/discrete)"],
            "serial_ports": list_serial_ports(),
            "profiles_dir": self.engine.profiles_dir,
            "db_path": self.engine.history.path,
            "python": sys.executable,
            "root": ROOT,
        }

    def set_theme(self, mode: str) -> dict:
        self.prefs["theme"] = mode
        _save_prefs(self.prefs)
        return {"ok": True}

    # ── perangkat ─────────────────────────────────────────────
    def devices(self) -> dict:
        return {"ok": True, "devices": self.engine.status()}

    def serial_ports(self) -> dict:
        """Port COM yang terdeteksi; konverter MOXA/USB-RS485 paling atas."""
        return {"ok": True, "ports": list_serial_ports()}

    @result
    def describe(self, device_id: str) -> dict:
        dev = self.engine.device(device_id)
        latest = self.engine.latest.get(device_id, {})
        return {"ok": True, "device": dev.to_dict(), "endpoint": dev.endpoint,
                "connected": self.engine.is_connected(device_id),
                "polling": self.engine.is_polling(device_id),
                "points": [{**p.to_dict(),
                            "value": (latest[p.name].value if p.name in latest else None),
                            "alarm": (latest[p.name].alarm if p.name in latest else ""),
                            "quality": (latest[p.name].quality if p.name in latest else "")}
                           for p in dev.points]}

    @result
    def connect(self, device_id: str) -> dict:
        self.engine.connect(device_id)
        return {"ok": True, "connected": True}

    @result
    def disconnect(self, device_id: str) -> dict:
        self.engine.stop_polling(device_id)
        self.engine.disconnect(device_id)
        return {"ok": True, "connected": False}

    @result
    def add_device(self, data: dict) -> dict:
        data = dict(data)
        data.setdefault("points", [])
        data["handle_local_echo"] = bool(data.get("handle_local_echo"))
        for key in ("port", "unit_id", "baudrate", "bytesize", "stopbits"):
            if key in data and data[key] not in (None, ""):
                data[key] = int(data[key])
        for key in ("timeout", "poll_interval"):
            if key in data and data[key] not in (None, ""):
                data[key] = float(data[key])
        dev = self.engine.add_device(data)
        return {"ok": True, "device": dev.id}

    @result
    def set_allow_write(self, device_id: str, allow: bool) -> dict:
        self.engine.update_device(device_id, {"allow_write": bool(allow)})
        return {"ok": True, "allow_write": bool(allow)}

    @result
    def remove_device(self, device_id: str) -> dict:
        self.engine.remove_device(device_id)
        return {"ok": True}

    @result
    def add_point(self, device_id: str, data: dict) -> dict:
        data = dict(data)
        for key in ("address", "length"):
            data[key] = int(data.get(key) or 0)
        for key in ("scale", "offset"):
            data[key] = float(data.get(key) or (1.0 if key == "scale" else 0.0))
        for key in ("alarm_low", "alarm_high"):
            data[key] = float(data[key]) if data.get(key) not in (None, "") else None
        data["writable"] = bool(data.get("writable"))
        p = self.engine.add_point(device_id, data)
        return {"ok": True, "point": p.name}

    @result
    def remove_point(self, device_id: str, name: str) -> dict:
        self.engine.remove_point(device_id, name)
        return {"ok": True}

    # ── baca / tulis ──────────────────────────────────────────
    @result
    def read_all(self, device_id: str, store: bool = False) -> dict:
        readings = self.engine.read_all(device_id, store=store)
        return {"ok": True, "readings": [r.to_dict() for r in readings],
                "time": time.strftime("%H:%M:%S")}

    @result
    def write_point(self, device_id: str, point: str, value: Any) -> dict:
        p = self.engine.device(device_id).point(point)
        val = bool(value) if p.table == "coil" else float(value)
        r = self.engine.write_point(device_id, point, val)
        return {"ok": True, "readback": r.to_dict()}

    @result
    def read_raw(self, device_id: str, table: str, address: int, count: int) -> dict:
        values = self.engine.read_raw(device_id, table, int(address), int(count))
        return {"ok": True, "values": values,
                "hex": [f"0x{int(v):04X}" for v in values]
                       if table in ("holding", "input") else []}

    @result
    def write_raw(self, device_id: str, table: str, address: int, values: str) -> dict:
        nums = [int(x, 0) for x in str(values).replace(",", " ").split()]
        back = self.engine.write_raw(device_id, table, int(address), nums)
        return {"ok": True, "written": nums, "readback": back}

    @result
    def scan_units(self, device_id: str, start: int, end: int) -> dict:
        hasil = self.engine.scan_units(device_id, int(start), int(end))
        return {"ok": True, "result": hasil,
                "responding": [h["unit_id"] for h in hasil if h["responded"]]}

    @result
    def scan_registers(self, device_id: str, table: str, start: int, end: int) -> dict:
        return {"ok": True,
                "blocks": self.engine.scan_registers(device_id, table,
                                                     int(start), int(end), 8)}

    # ── polling & riwayat ─────────────────────────────────────
    @result
    def start_polling(self, device_id: str, interval: float) -> dict:
        sec = self.engine.start_polling(device_id, float(interval))
        return {"ok": True, "interval": sec}

    @result
    def stop_polling(self, device_id: str) -> dict:
        self.engine.stop_polling(device_id)
        return {"ok": True}

    def live(self, device_id: str) -> dict:
        """Nilai terakhir dari cache polling, tanpa memicu pembacaan baru."""
        latest = self.engine.latest.get(device_id, {})
        return {"ok": True,
                "connected": self.engine.is_connected(device_id),
                "polling": self.engine.is_polling(device_id),
                "time": time.strftime("%H:%M:%S"),
                "readings": [r.to_dict() for r in latest.values()]}

    @result
    def history(self, device_id: str, point: str, minutes: float,
                limit: int = 600) -> dict:
        since = time.time() - float(minutes) * 60
        rows = self.engine.history.query(device_id, point, since=since,
                                         limit=int(limit), newest_first=False)
        stats = self.engine.history.stats(device_id, point, since=since)
        return {"ok": True, "rows": rows, "stats": stats}

    @result
    def recorded_points(self) -> dict:
        return {"ok": True, "points": self.engine.history.points()}

    @result
    def export_csv(self, device_id: str, point: str, minutes: float) -> dict:
        win = webview.windows[0]
        res = win.create_file_dialog(webview.SAVE_DIALOG, directory=ROOT,
                                     save_filename=f"{device_id}_riwayat.csv")
        if not res:
            return {"ok": False, "cancel": True, "error": "dibatalkan"}
        path = res if isinstance(res, str) else res[0]
        n = self.engine.history.export_csv(path, device_id, point or None,
                                           time.time() - float(minutes) * 60)
        return {"ok": True, "rows": n, "path": path}

    @result
    def clear_history(self, device_id: str) -> dict:
        return {"ok": True, "deleted": self.engine.history.clear(device_id or None)}

    # ── simulator & mcp ───────────────────────────────────────
    @result
    def simulator(self, action: str) -> dict:
        if action == "start":
            if self.sim and self.sim.is_running:
                return {"ok": True, "running": True, "url": self.sim.url}
            self.sim = ModbusSimulator()
            self.sim.start()
            return {"ok": True, "running": self.sim.is_running, "url": self.sim.url}
        if action == "stop":
            if self.sim:
                self.sim.stop()
            return {"ok": True, "running": False}
        return {"ok": True, "running": bool(self.sim and self.sim.is_running),
                "url": self.sim.url if self.sim else None}

    def mcp_config(self) -> dict:
        cfg = {"mcpServers": {"modbus": {
            "command": sys.executable,
            "args": ["-m", "mcp_server.server"],
            "cwd": ROOT,
            "env": {"CLAUDE_MODBUS_DB": self.engine.history.path},
        }}}
        return {"ok": True,
                "json": json.dumps(cfg, indent=2),
                "cli": f'claude mcp add modbus -- "{sys.executable}" -m mcp_server.server',
                "root": ROOT}

    def log(self) -> dict:
        return {"ok": True, "lines": self.engine.log[-60:][::-1]}

    def shutdown(self) -> None:
        try:
            self.engine.close()
        finally:
            if self.sim:
                self.sim.stop()


def main() -> None:
    _set_app_id()
    api = Api()
    window = webview.create_window(
        TITLE, os.path.join(WEB, "index.html"),
        js_api=api, width=1440, height=920, min_size=(1120, 720),
        background_color="#F5F4EE")
    window.events.closed += api.shutdown

    def on_shown() -> None:
        # jendela baru ada setelah ditampilkan; coba beberapa kali kalau telat
        import threading

        def pasang() -> None:
            for _ in range(20):
                if _apply_window_icon():
                    return
                time.sleep(0.15)
        threading.Thread(target=pasang, daemon=True).start()

    window.events.shown += on_shown
    webview.start(icon=ICON if os.path.exists(ICON) else None)


if __name__ == "__main__":
    main()
