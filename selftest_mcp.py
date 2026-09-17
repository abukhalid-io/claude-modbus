"""
Uji MCP server sungguhan: jalankan `python -m mcp_server.server` sebagai proses
anak lewat stdio, lalu panggil tool-nya seperti Claude memanggilnya.

    python selftest_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))


def payload(result) -> dict:
    """Ambil isi JSON dari hasil call_tool."""
    text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    return json.loads(text)


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cm_mcp_")
    env = dict(os.environ)
    env["CLAUDE_MODBUS_DB"] = os.path.join(tmp, "history.db")
    env["PYTHONIOENCODING"] = "utf-8"

    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "mcp_server.server"],
                                   cwd=HERE, env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            info = await s.initialize()
            print(f"server           : {info.serverInfo.name} v{info.serverInfo.version}")

            tools = (await s.list_tools()).tools
            names = [t.name for t in tools]
            print(f"tools            : {len(tools)}")
            assert len(tools) >= 23, names
            assert all(n.startswith("modbus_") for n in names), names

            # setiap tool harus punya deskripsi dan skema input yang benar
            for t in tools:
                assert t.description and len(t.description) > 30, t.name
                assert t.inputSchema.get("type") == "object", t.name
            rp = next(t for t in tools if t.name == "modbus_read_point")
            assert set(rp.inputSchema["properties"]) == {"device_id", "point"}, \
                rp.inputSchema["properties"]
            assert rp.annotations.readOnlyHint is True
            wp = next(t for t in tools if t.name == "modbus_write_point")
            assert wp.annotations.destructiveHint is True
            print("skema & anotasi  : lengkap (readOnly/destructive terpasang)")

            # 0. deteksi port serial (jalur MOXA USB-RS485)
            r = payload(await s.call_tool("modbus_list_serial_ports", {}))
            assert r["ok"] and isinstance(r["ports"], list), r
            print(f"port serial      : {r['count']} terdeteksi "
                  f"{[p['device'] for p in r['ports']] or '(tidak ada konverter tercolok)'}")

            # profil RTU lewat konverter USB-RS485 bisa dibuat & tervalidasi
            r = payload(await s.call_tool("modbus_add_device", {
                "device_id": "uji_rtu", "name": "Uji RTU", "transport": "rtu",
                "serial_port": "COM99", "baudrate": 19200, "parity": "E",
                "stopbits": 1, "handle_local_echo": True, "unit_id": 7}))
            assert r["ok"] and r["device"]["transport"] == "rtu", r
            assert r["device"]["handle_local_echo"] is True, r
            print(f"profil RTU       : {r['device']['serial_port']}@"
                  f"{r['device']['baudrate']} unit {r['device']['unit_id']}, echo on")
            r = payload(await s.call_tool("modbus_connect", {"device_id": "uji_rtu"}))
            assert r["ok"] is False and "port serial" in r["error"], r
            print(f"galat port RTU   : {r['error'][:64]}...")
            await s.call_tool("modbus_remove_device", {"device_id": "uji_rtu"})

            # 1. nyalakan simulator
            r = payload(await s.call_tool("modbus_simulator", {"action": "start"}))
            assert r["ok"] and r["running"], r
            print(f"simulator        : jalan di {r['url']}")

            # 2. daftar perangkat
            r = payload(await s.call_tool("modbus_list_devices", {}))
            sim = next(d for d in r["devices"] if d["id"] == "simulator")
            print(f"perangkat        : {sim['name']} ({sim['points']} point)")

            # 3. peta register
            r = payload(await s.call_tool("modbus_describe_device",
                                          {"device_id": "simulator"}))
            assert len(r["points"]) == 14, len(r["points"])

            # 4. baca semuanya
            r = payload(await s.call_tool("modbus_read_all",
                                          {"device_id": "simulator", "store": True}))
            nilai = {x["point"]: x["value"] for x in r["readings"]}
            assert nilai["nama_perangkat"] == "SIM-TANK-01", nilai
            assert all(x["quality"] == "good" for x in r["readings"]), r["readings"]
            print(f"baca semua       : suhu {nilai['suhu_tangki']} degC, "
                  f"level {nilai['level_tangki']} %, "
                  f"pompa {'jalan' if nilai['pompa_jalan'] else 'mati'}")

            # 5. baca register mentah
            r = payload(await s.call_tool("modbus_read_raw", {
                "device_id": "simulator", "table": "holding",
                "address": 10, "count": 4}))
            assert len(r["values"]) == 4, r
            print(f"baca mentah      : holding@10 x4 = {r['values']}")

            # 6. tulis (dengan skala) lalu baca balik
            r = payload(await s.call_tool("modbus_write_point", {
                "device_id": "simulator", "point": "setpoint_suhu", "value": 68.5}))
            assert r["ok"] and abs(r["readback"]["value"] - 68.5) < 0.05, r
            print(f"tulis setpoint   : 68.5 -> baca balik {r['readback']['value']} degC")

            r = payload(await s.call_tool("modbus_write_point", {
                "device_id": "simulator", "point": "pemanas_jalan", "value": 1}))
            assert r["readback"]["value"] is True, r
            print("tulis coil       : pemanas ON")

            # 7. pengaman tulis
            r = payload(await s.call_tool("modbus_write_point", {
                "device_id": "simulator", "point": "suhu_tangki", "value": 5}))
            assert r["ok"] is False and "writable=false" in r["error"], r
            print(f"pengaman tulis   : ditolak -> {r['error'][:58]}...")

            # 8. galat yang menuntun
            r = payload(await s.call_tool("modbus_read_point", {
                "device_id": "simulator", "point": "salah_nama"}))
            assert r["ok"] is False and "Yang tersedia" in r["error"], r
            print("pesan galat      : menyebutkan point yang tersedia")

            # 9. polling + statistik
            await s.call_tool("modbus_start_polling",
                              {"device_id": "simulator", "interval_seconds": 0.5})
            await asyncio.sleep(3.5)
            await s.call_tool("modbus_stop_polling", {"device_id": "simulator"})

            r = payload(await s.call_tool("modbus_statistics", {
                "device_id": "simulator", "point": "suhu_tangki", "minutes": 10}))
            assert r["numeric_samples"] >= 4, r
            print(f"statistik        : {r['numeric_samples']} sampel, "
                  f"min {r['min']}, max {r['max']}, rata2 {r['mean']}, "
                  f"tren {r['direction']}")

            r = payload(await s.call_tool("modbus_history", {
                "device_id": "simulator", "point": "level_tangki",
                "minutes": 10, "limit": 5}))
            assert r["returned"] >= 1, r
            print(f"riwayat          : {r['returned']} baris terakhir diambil")

            # 10. alarm
            await s.call_tool("modbus_write_point", {
                "device_id": "simulator", "point": "setpoint_suhu", "value": 95})
            await asyncio.sleep(1.5)
            r = payload(await s.call_tool("modbus_alarms", {"device_id": "simulator"}))
            print(f"alarm            : {r['active_count']} aktif "
                  f"{[a['point'] for a in r['active']]}")
            assert any(a["point"] == "suhu_tangki" for a in r["active"]), r

            # 11. ekspor CSV
            csv_path = os.path.join(tmp, "riwayat.csv")
            r = payload(await s.call_tool("modbus_export_csv", {
                "device_id": "simulator", "path": csv_path, "minutes": 10}))
            assert r["ok"] and r["rows"] > 0 and os.path.exists(csv_path), r
            print(f"ekspor csv       : {r['rows']} baris -> {os.path.basename(csv_path)}")

            # 12. bikin profil baru dari nol (seperti dari datasheet)
            r = payload(await s.call_tool("modbus_add_device", {
                "device_id": "uji_datasheet", "name": "Meteran Uji",
                "host": "127.0.0.1", "port": 15020, "allow_write": False}))
            assert r["ok"], r
            r = payload(await s.call_tool("modbus_add_point", {
                "device_id": "uji_datasheet", "name": "suhu", "address": 0,
                "datatype": "float32", "unit": "degC", "alarm_high": 70}))
            assert r["ok"], r
            r = payload(await s.call_tool("modbus_read_point", {
                "device_id": "uji_datasheet", "point": "suhu"}))
            assert r["ok"] and isinstance(r["reading"]["value"], float), r
            print(f"profil baru      : dibuat & langsung terbaca "
                  f"{r['reading']['value']} degC")

            r = payload(await s.call_tool("modbus_write_point", {
                "device_id": "uji_datasheet", "point": "suhu", "value": 1}))
            assert r["ok"] is False and "allow_write=false" in r["error"], r
            print("allow_write=false: perintah tulis ditolak")

            await s.call_tool("modbus_remove_device", {"device_id": "uji_datasheet"})

            # 13. scan
            r = payload(await s.call_tool("modbus_scan_units", {
                "device_id": "simulator", "start": 1, "end": 3}))
            assert r["responding_unit_ids"], r
            print(f"scan unit id     : menjawab {r['responding_unit_ids']}")

            await s.call_tool("modbus_simulator", {"action": "stop"})

    print("\nSEMUA UJI MCP LULUS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
