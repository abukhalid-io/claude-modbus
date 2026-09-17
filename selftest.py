"""
Uji menyeluruh tanpa hardware: simulator + mesin + API GUI.

    python selftest.py

Untuk MCP server ada uji terpisah: python selftest_mcp.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

from core.engine import ModbusEngine, WriteRefused
from core.models import Device, ModbusConfigError, Point
from core.simulator import ModbusSimulator


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="claude_modbus_")
    db = os.path.join(tmp, "history.db")

    # ── model ────────────────────────────────────────────────
    p = Point(name="suhu", address=0, datatype="float32", unit="degC", alarm_high=70)
    assert p.word_count == 2 and p.alarm_state(80) == "high"
    try:
        Point(name="x", address=0, table="input", writable=True)
    except ModbusConfigError:
        pass
    else:
        raise AssertionError("point input+writable harusnya ditolak")
    print("model            : validasi & alarm OK")

    # ── simulator ────────────────────────────────────────────
    sim = ModbusSimulator()
    sim.start()
    assert sim.is_running, "simulator gagal jalan"
    print(f"simulator        : jalan di {sim.url}")

    eng = ModbusEngine(db_path=db)
    assert "simulator" in eng.devices, list(eng.devices)
    dev = eng.device("simulator")
    assert len(dev.points) == 14, len(dev.points)

    # ── baca ─────────────────────────────────────────────────
    eng.connect("simulator")
    readings = eng.read_all("simulator")
    nilai = {r.point: r.value for r in readings}
    assert all(r.quality == "good" for r in readings), \
        [r.error for r in readings if r.quality != "good"]
    assert nilai["nama_perangkat"] == "SIM-TANK-01", nilai["nama_perangkat"]
    assert isinstance(nilai["suhu_tangki"], float)
    assert isinstance(nilai["pompa_jalan"], bool)
    assert 0 <= nilai["level_tangki"] <= 100, nilai["level_tangki"]
    print(f"baca 14 point    : suhu {nilai['suhu_tangki']:.2f} degC, "
          f"level {nilai['level_tangki']:.1f} %, total {nilai['total_liter']:.0f} L")

    raw = eng.read_raw("simulator", "holding", 12, 1)
    assert raw[0] == int(round(nilai["setpoint_suhu"] * 10)), (raw, nilai["setpoint_suhu"])
    print(f"baca mentah      : holding@12 = {raw[0]} (setpoint x10)")

    # ── tulis + skala ────────────────────────────────────────
    back = eng.write_point("simulator", "setpoint_suhu", 71.3)
    assert abs(back.value - 71.3) < 0.05, back.value
    assert eng.read_raw("simulator", "holding", 12, 1)[0] == 713
    print(f"tulis berskala   : 71.3 degC -> register 713 -> baca balik {back.value}")

    assert eng.write_point("simulator", "pemanas_jalan", True).value is True
    assert eng.write_point("simulator", "kecepatan_pompa", 88).value == 88
    print("tulis coil & int : OK")

    # ── pengaman ─────────────────────────────────────────────
    for point, sebab in (("suhu_tangki", "writable=false"),
                         ("alarm_suhu_tinggi", "hanya bisa dibaca")):
        try:
            eng.write_point("simulator", point, 1)
        except WriteRefused as exc:
            assert sebab in str(exc), (point, exc)
        else:
            raise AssertionError(f"tulis ke '{point}' harusnya ditolak")

    dev.allow_write = False
    try:
        eng.write_point("simulator", "kecepatan_pompa", 10)
    except WriteRefused as exc:
        assert "allow_write=false" in str(exc)
    else:
        raise AssertionError("allow_write=false harusnya menolak")
    dev.allow_write = True
    print("pengaman tulis   : 3 lapis semuanya menolak dengan alasan jelas")

    # ── polling + riwayat + statistik ────────────────────────
    eng.start_polling("simulator", 0.4)
    time.sleep(3.0)
    assert eng.is_polling("simulator")
    eng.stop_polling("simulator")
    st = eng.history.stats("simulator", "suhu_tangki")
    assert st["numeric_samples"] >= 5, st
    assert st["min"] <= st["mean"] <= st["max"]
    assert st["direction"] in ("naik", "turun", "datar")
    print(f"polling+statistik: {st['numeric_samples']} sampel, "
          f"{st['min']:.2f}..{st['max']:.2f} degC, tren {st['direction']}")

    csv_path = os.path.join(tmp, "riwayat.csv")
    n = eng.history.export_csv(csv_path, "simulator")
    assert n > 0 and os.path.getsize(csv_path) > 0
    print(f"ekspor csv       : {n} baris")

    # ── alarm ────────────────────────────────────────────────
    eng.write_point("simulator", "setpoint_suhu", 95)
    time.sleep(1.4)
    eng.read_all("simulator")
    suhu = eng.latest["simulator"]["suhu_tangki"]
    assert suhu.alarm == "high", (suhu.value, suhu.alarm)
    print(f"alarm            : suhu {suhu.value:.1f} degC -> status '{suhu.alarm}'")
    eng.write_point("simulator", "setpoint_suhu", 65)

    # ── profil baru dari nol ─────────────────────────────────
    eng.add_device({"id": "uji_profil", "name": "Uji", "host": "127.0.0.1",
                    "port": 15020, "allow_write": False})
    eng.add_point("uji_profil", {"name": "level", "address": 4, "datatype": "int16",
                                 "scale": 0.1, "unit": "%"})
    r = eng.read_point("uji_profil", "level")
    assert r.quality == "good" and 0 <= r.value <= 100, r
    assert os.path.exists(os.path.join(eng.profiles_dir, "uji_profil.json"))
    eng.remove_device("uji_profil")
    assert not os.path.exists(os.path.join(eng.profiles_dir, "uji_profil.json"))
    print(f"profil baru      : dibuat, dibaca ({r.value} %), lalu dihapus bersih")

    # ── scan ─────────────────────────────────────────────────
    blocks = eng.scan_registers("simulator", "holding", 0, 15, 8)
    assert all(b["ok"] for b in blocks), blocks
    print(f"sapu register    : {len(blocks)} blok terbaca")

    eng.close()

    # ── API GUI ──────────────────────────────────────────────
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from gui.app import Api
    api = Api()
    boot = api.boot()
    assert boot["devices"], boot
    assert api.connect("simulator")["ok"]
    live = api.read_all("simulator")
    assert live["ok"] and len(live["readings"]) == 14, live
    w = api.write_point("simulator", "kecepatan_pompa", 55)
    assert w["ok"] and w["readback"]["value"] == 55, w
    bad = api.write_point("simulator", "suhu_tangki", 1)
    assert bad["ok"] is False and "writable" in bad["error"], bad
    rr = api.read_raw("simulator", "holding", 0, 4)
    assert rr["ok"] and len(rr["values"]) == 4 and rr["hex"][0].startswith("0x")
    hist = api.history("simulator", "suhu_tangki", 10)
    assert hist["ok"], hist
    cfg = api.mcp_config()
    assert "mcpServers" in cfg["json"] and "mcp_server.server" in cfg["cli"]
    api.shutdown()
    print("api gui          : boot, baca, tulis, pengaman, riwayat, config MCP OK")

    sim.stop()
    print("\nSEMUA UJI LULUS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
