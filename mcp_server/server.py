"""
modbus_mcp - MCP server supaya Claude bisa membaca, menganalisis, dan
(kalau diizinkan) mengendalikan perangkat Modbus.

Jalankan lewat stdio:
    python -m mcp_server.server

Pengaman tulis berlapis:
  1. profil perangkat harus punya allow_write = true
  2. titik ukur harus writable = true
  3. kalau env CLAUDE_MODBUS_READONLY=1 diset, semua perintah tulis ditolak
"""

from __future__ import annotations

import functools
import json
import os
import sys
import time
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine import ModbusEngine, ModbusError, WriteRefused  # noqa: E402
from core.models import ModbusConfigError  # noqa: E402
from core.serialports import list_serial_ports  # noqa: E402
from core.simulator import ModbusSimulator  # noqa: E402

mcp = FastMCP("modbus_mcp")

_engine: ModbusEngine | None = None
_sim: ModbusSimulator | None = None

READONLY = os.environ.get("CLAUDE_MODBUS_READONLY", "").strip() in ("1", "true", "yes")


def engine() -> ModbusEngine:
    global _engine
    if _engine is None:
        _engine = ModbusEngine(
            profiles_dir=os.environ.get("CLAUDE_MODBUS_PROFILES") or None,
            db_path=os.environ.get("CLAUDE_MODBUS_DB") or None,
        )
    return _engine


def ok(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def fail(message: str, hint: str = "") -> str:
    out = {"ok": False, "error": message}
    if hint:
        out["hint"] = hint
    return json.dumps(out, indent=2, ensure_ascii=False)


def guarded(fn):
    """Bungkus tool supaya galat jadi pesan yang bisa ditindaklanjuti.

    functools.wraps penting: FastMCP membaca signature lewat __wrapped__ untuk
    membangun inputSchema. Tanpa itu, semua tool akan kehilangan parameternya.
    """
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except WriteRefused as exc:
            return fail(str(exc), "Ini pengaman, bukan bug. Nyalakan allow_write "
                                  "di profil perangkat kalau memang disengaja.")
        except ModbusConfigError as exc:
            return fail(str(exc), "Periksa lagi nama perangkat/point lewat "
                                  "modbus_list_devices atau modbus_describe_device.")
        except ModbusError as exc:
            return fail(str(exc), "Pastikan perangkat terhubung (modbus_connect) "
                                  "dan alamat registernya benar.")
        except Exception as exc:                       # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")
    return wrapper


def _reading(r) -> dict:
    d = {"point": r.point, "value": r.value, "unit": r.unit,
         "quality": r.quality, "time": time.strftime("%H:%M:%S", time.localtime(r.ts))}
    if r.alarm:
        d["alarm"] = r.alarm
    if r.error:
        d["error"] = r.error
    if r.raw:
        d["raw_registers"] = r.raw
    return d


# ══════════════════════════════════════════════════════════════
#  PROFIL & KONEKSI
# ══════════════════════════════════════════════════════════════
@mcp.tool(name="modbus_list_devices", annotations={
    "title": "Daftar perangkat Modbus", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_list_devices() -> str:
    """Daftar semua perangkat Modbus yang sudah punya profil, lengkap dengan
    status koneksi, jumlah titik ukur, apakah boleh ditulis, dan apakah sedang
    dipoll. Mulailah dari sini untuk tahu apa yang tersedia."""
    return ok({"ok": True, "readonly_mode": READONLY, "devices": engine().status()})


@mcp.tool(name="modbus_describe_device", annotations={
    "title": "Rincian perangkat & peta register", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_describe_device(
    device_id: Annotated[str, Field(description="ID perangkat, mis. 'simulator'")],
) -> str:
    """Tampilkan profil lengkap satu perangkat: pengaturan koneksi dan seluruh
    titik ukur (alamat register, tipe data, skala, satuan, batas alarm, dan
    apakah boleh ditulis). Pakai ini sebelum membaca atau menulis."""
    dev = engine().device(device_id)
    d = dev.to_dict()
    d.pop("points")
    return ok({"ok": True, "device": d, "endpoint": dev.endpoint,
               "connected": engine().is_connected(device_id),
               "points": [p.to_dict() for p in dev.points]})


@mcp.tool(name="modbus_list_serial_ports", annotations={
    "title": "Daftar port serial (konverter USB-RS485)", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_list_serial_ports() -> str:
    """Daftar port COM/tty yang terdeteksi di komputer ini, konverter RS-485
    (MOXA UPort, FTDI, CH340, dan sejenisnya) diletakkan paling atas dan
    ditandai. Pakai ini untuk tahu port mana yang harus dipakai sebuah
    perangkat Modbus RTU sebelum membuat profilnya."""
    ports = list_serial_ports()
    return ok({"ok": True, "count": len(ports), "ports": ports,
               "hint": "Kalau kosong: colokkan konverter USB-RS485 dan pastikan "
                       "drivernya terpasang. Untuk MOXA UPort, mode RS-485 2-kawat "
                       "diatur di MOXA Driver Manager, bukan dari aplikasi ini."})


@mcp.tool(name="modbus_connect", annotations={
    "title": "Hubungkan ke perangkat", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@guarded
def modbus_connect(
    device_id: Annotated[str, Field(description="ID perangkat yang mau dihubungkan")],
) -> str:
    """Buka koneksi Modbus TCP/RTU ke perangkat. Aman dipanggil berulang.
    Tool baca/tulis juga akan menyambung sendiri kalau belum terhubung."""
    engine().connect(device_id)
    return ok({"ok": True, "device": device_id, "connected": True,
               "endpoint": engine().device(device_id).endpoint})


@mcp.tool(name="modbus_disconnect", annotations={
    "title": "Putuskan koneksi", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@guarded
def modbus_disconnect(
    device_id: Annotated[str, Field(description="ID perangkat")],
) -> str:
    """Tutup koneksi ke perangkat dan hentikan polling-nya. Berguna untuk
    melepas port serial supaya bisa dipakai program lain."""
    engine().stop_polling(device_id)
    engine().disconnect(device_id)
    return ok({"ok": True, "device": device_id, "connected": False})


# ══════════════════════════════════════════════════════════════
#  BACA
# ══════════════════════════════════════════════════════════════
@mcp.tool(name="modbus_read_all", annotations={
    "title": "Baca semua titik ukur", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_read_all(
    device_id: Annotated[str, Field(description="ID perangkat")],
    store: Annotated[bool, Field(description="Simpan hasilnya ke riwayat")] = False,
) -> str:
    """Ambil satu potret kondisi perangkat: baca seluruh titik ukur di profil,
    hasilnya sudah diskalakan lengkap dengan satuan dan status alarm.
    Ini cara tercepat menjawab 'bagaimana keadaan alat sekarang?'."""
    readings = engine().read_all(device_id, store=store)
    alarms = [r.point for r in readings if r.alarm in ("low", "high")]
    return ok({"ok": True, "device": device_id,
               "time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "active_alarms": alarms,
               "readings": [_reading(r) for r in readings]})


@mcp.tool(name="modbus_read_point", annotations={
    "title": "Baca satu titik ukur", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_read_point(
    device_id: Annotated[str, Field(description="ID perangkat")],
    point: Annotated[str, Field(description="Nama titik ukur, mis. 'suhu_tangki'")],
) -> str:
    """Baca satu titik ukur saja, hasilnya sudah diskalakan dan diberi satuan."""
    return ok({"ok": True, "device": device_id,
               "reading": _reading(engine().read_point(device_id, point))})


@mcp.tool(name="modbus_read_raw", annotations={
    "title": "Baca register mentah", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_read_raw(
    device_id: Annotated[str, Field(description="ID perangkat")],
    table: Annotated[str, Field(description="holding | input | coil | discrete")],
    address: Annotated[int, Field(description="Alamat awal (basis 0)", ge=0, le=65535)],
    count: Annotated[int, Field(description="Berapa register/bit dibaca", ge=1, le=2000)] = 1,
) -> str:
    """Baca register apa adanya tanpa memakai profil — untuk menjelajah alat
    yang peta registernya belum diketahui. Hasilnya angka 16-bit mentah
    (atau true/false untuk coil/discrete)."""
    values = engine().read_raw(device_id, table, address, count)
    return ok({"ok": True, "device": device_id, "table": table, "address": address,
               "count": count, "values": values,
               "hex": [f"0x{int(v):04X}" for v in values] if table in
                      ("holding", "input") else None})


# ══════════════════════════════════════════════════════════════
#  TULIS
# ══════════════════════════════════════════════════════════════
@mcp.tool(name="modbus_write_point", annotations={
    "title": "Tulis nilai ke titik ukur", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_write_point(
    device_id: Annotated[str, Field(description="ID perangkat")],
    point: Annotated[str, Field(description="Nama titik ukur yang writable")],
    value: Annotated[float, Field(description="Nilai dalam satuan teknis "
                                              "(skala profil diterapkan otomatis). "
                                              "Untuk coil pakai 1 = ON, 0 = OFF")],
) -> str:
    """Tulis ke perangkat sungguhan — ini menggerakkan alat di lapangan.
    Nilai ditulis dalam satuan teknis; penskalaan dan tipe data diurus
    otomatis. Ditolak kecuali profil perangkat allow_write=true DAN titik
    ukurnya writable=true. Setelah menulis, nilainya dibaca ulang dan
    dikembalikan sebagai bukti."""
    if READONLY:
        return fail("Server dijalankan dengan CLAUDE_MODBUS_READONLY=1, "
                    "semua perintah tulis dimatikan.",
                    "Hapus env var itu lalu jalankan ulang server MCP.")
    dev = engine().device(device_id)
    p = dev.point(point)
    val: Any = bool(value) if p.table == "coil" else value
    after = engine().write_point(device_id, point, val)
    return ok({"ok": True, "device": device_id, "point": point,
               "written": val, "readback": _reading(after)})


@mcp.tool(name="modbus_write_raw", annotations={
    "title": "Tulis register mentah", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_write_raw(
    device_id: Annotated[str, Field(description="ID perangkat")],
    table: Annotated[str, Field(description="holding | coil")],
    address: Annotated[int, Field(description="Alamat awal (basis 0)", ge=0, le=65535)],
    values: Annotated[list[int], Field(description="Daftar nilai 16-bit "
                                                   "(atau 0/1 untuk coil)")],
) -> str:
    """Tulis langsung ke register tanpa lewat profil. Berbahaya: tidak ada
    penskalaan dan tidak ada pengecekan arti register. Tetap tunduk pada
    allow_write di profil perangkat."""
    if READONLY:
        return fail("Server dijalankan read-only (CLAUDE_MODBUS_READONLY=1).")
    after = engine().write_raw(device_id, table, address, values)
    return ok({"ok": True, "device": device_id, "table": table, "address": address,
               "written": values, "readback": after})


# ══════════════════════════════════════════════════════════════
#  POLLING & RIWAYAT
# ══════════════════════════════════════════════════════════════
@mcp.tool(name="modbus_start_polling", annotations={
    "title": "Mulai polling berkala", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@guarded
def modbus_start_polling(
    device_id: Annotated[str, Field(description="ID perangkat")],
    interval_seconds: Annotated[float, Field(description="Jeda antar siklus baca",
                                             ge=0.2, le=3600)] = 2.0,
) -> str:
    """Baca seluruh titik ukur berulang di latar belakang dan simpan ke riwayat.
    Inilah yang membuat modbus_statistics punya data untuk dianalisis.
    Nyalakan dulu, tunggu beberapa siklus, baru minta statistik."""
    sec = engine().start_polling(device_id, interval_seconds)
    return ok({"ok": True, "device": device_id, "interval_seconds": sec,
               "note": "Riwayat mulai terkumpul. Cek dengan modbus_statistics."})


@mcp.tool(name="modbus_stop_polling", annotations={
    "title": "Hentikan polling", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_stop_polling(
    device_id: Annotated[str, Field(description="ID perangkat")],
) -> str:
    """Hentikan polling latar belakang untuk perangkat ini."""
    engine().stop_polling(device_id)
    return ok({"ok": True, "device": device_id, "polling": False})


@mcp.tool(name="modbus_history", annotations={
    "title": "Riwayat pembacaan", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_history(
    device_id: Annotated[str, Field(description="ID perangkat")],
    point: Annotated[str, Field(description="Nama titik ukur")],
    minutes: Annotated[float, Field(description="Ambil data sekian menit terakhir",
                                    ge=0.1, le=100000)] = 60,
    limit: Annotated[int, Field(description="Maksimum baris dikembalikan",
                                ge=1, le=5000)] = 200,
) -> str:
    """Ambil deret waktu mentah satu titik ukur dari riwayat, terbaru dulu.
    Untuk ringkasan angka pakai modbus_statistics; pakai tool ini kalau perlu
    melihat nilai satu per satu."""
    since = time.time() - minutes * 60
    rows = engine().history.query(device_id, point, since=since, limit=limit)
    return ok({"ok": True, "device": device_id, "point": point,
               "window_minutes": minutes, "returned": len(rows),
               "samples": [{"time": time.strftime("%Y-%m-%d %H:%M:%S",
                                                  time.localtime(r["ts"])),
                            "value": r["value"], "quality": r["quality"]}
                           for r in rows]})


@mcp.tool(name="modbus_statistics", annotations={
    "title": "Statistik & tren titik ukur", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_statistics(
    device_id: Annotated[str, Field(description="ID perangkat")],
    point: Annotated[str, Field(description="Nama titik ukur")],
    minutes: Annotated[float, Field(description="Jendela analisis, dalam menit",
                                    ge=0.1, le=100000)] = 60,
) -> str:
    """Ringkas riwayat satu titik ukur: jumlah sampel, min, max, rata-rata,
    median, simpangan baku, nilai awal/akhir, dan arah tren (naik/turun/datar)
    beserta kemiringannya per menit. Butuh data dari polling."""
    since = time.time() - minutes * 60
    s = engine().history.stats(device_id, point, since=since)
    if not s.get("numeric_samples"):
        return ok({"ok": True, "device": device_id, "point": point,
                   "samples": 0,
                   "hint": "Belum ada data. Jalankan modbus_start_polling lalu "
                           "tunggu beberapa siklus."})
    s["ok"] = True
    s["window_minutes"] = minutes
    return ok(s)


@mcp.tool(name="modbus_recorded_points", annotations={
    "title": "Titik ukur yang punya riwayat", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_recorded_points() -> str:
    """Daftar semua pasangan perangkat+titik ukur yang sudah punya data riwayat,
    lengkap dengan jumlah sampel dan rentang waktunya."""
    return ok({"ok": True, "points": engine().history.points()})


@mcp.tool(name="modbus_alarms", annotations={
    "title": "Cek kondisi alarm", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_alarms(
    device_id: Annotated[str, Field(description="ID perangkat")],
    refresh: Annotated[bool, Field(description="Baca ulang dari perangkat "
                                               "sebelum mengevaluasi")] = True,
) -> str:
    """Bandingkan nilai sekarang dengan batas alarm_low/alarm_high di profil,
    lalu laporkan titik ukur mana yang melewati batas."""
    eng = engine()
    if refresh:
        eng.read_all(device_id)
    dev = eng.device(device_id)
    hasil = []
    for p in dev.points:
        r = eng.latest.get(device_id, {}).get(p.name)
        if r is None or not r.alarm:
            continue
        hasil.append({"point": p.name, "value": r.value, "unit": p.unit,
                      "state": r.alarm, "low": p.alarm_low, "high": p.alarm_high})
    aktif = [h for h in hasil if h["state"] in ("low", "high")]
    return ok({"ok": True, "device": device_id, "active_count": len(aktif),
               "active": aktif, "monitored": hasil})


@mcp.tool(name="modbus_export_csv", annotations={
    "title": "Ekspor riwayat ke CSV", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
@guarded
def modbus_export_csv(
    device_id: Annotated[str, Field(description="ID perangkat")],
    path: Annotated[str, Field(description="Path file CSV tujuan")],
    point: Annotated[str, Field(description="Batasi ke satu titik ukur "
                                            "(kosong = semua)")] = "",
    minutes: Annotated[float, Field(description="Jendela waktu dalam menit",
                                    ge=0.1, le=100000)] = 1440,
) -> str:
    """Tulis riwayat pembacaan ke file CSV agar bisa dibuka di Excel."""
    since = time.time() - minutes * 60
    n = engine().history.export_csv(path, device_id, point or None, since)
    return ok({"ok": True, "rows": n, "path": path})


# ══════════════════════════════════════════════════════════════
#  MEMBANGUN PROFIL & MENJELAJAH
# ══════════════════════════════════════════════════════════════
@mcp.tool(name="modbus_add_device", annotations={
    "title": "Tambah profil perangkat", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
@guarded
def modbus_add_device(
    device_id: Annotated[str, Field(description="ID unik, huruf/angka/garis bawah")],
    name: Annotated[str, Field(description="Nama yang enak dibaca")] = "",
    transport: Annotated[str, Field(description="'tcp' atau 'rtu'")] = "tcp",
    host: Annotated[str, Field(description="Alamat IP untuk Modbus TCP")] = "127.0.0.1",
    port: Annotated[int, Field(description="Port TCP", ge=1, le=65535)] = 502,
    serial_port: Annotated[str, Field(description="Port serial untuk RTU, mis. COM3 "
                                                 "(lihat modbus_list_serial_ports)")] = "COM1",
    baudrate: Annotated[int, Field(description="Baudrate untuk RTU")] = 9600,
    parity: Annotated[str, Field(description="Paritas RTU: N, E, atau O")] = "N",
    stopbits: Annotated[int, Field(description="Stop bit RTU: 1 atau 2", ge=1, le=2)] = 1,
    bytesize: Annotated[int, Field(description="Bit data RTU, biasanya 8", ge=5, le=8)] = 8,
    framer: Annotated[str, Field(description="'rtu' (biasa) atau 'ascii'")] = "rtu",
    handle_local_echo: Annotated[bool, Field(
        description="Nyalakan kalau konverter USB-RS485 memantulkan balik byte "
                    "yang baru dikirim sehingga balasan selalu terbaca kacau")] = False,
    unit_id: Annotated[int, Field(description="Slave/unit id", ge=0, le=255)] = 1,
    allow_write: Annotated[bool, Field(description="Izinkan perintah tulis "
                                                   "ke alat ini")] = False,
    note: Annotated[str, Field(description="Catatan bebas")] = "",
) -> str:
    """Buat profil perangkat baru dan simpan sebagai JSON. Setelah ini,
    tambahkan titik ukur dengan modbus_add_point. Berguna saat kamu punya
    datasheet peta register dan ingin memasukkannya ke sistem.

    Untuk alat yang disambung lewat konverter USB-RS485 (mis. MOXA UPort),
    pakai transport='rtu' dan isi serial_port dengan port dari
    modbus_list_serial_ports."""
    dev = engine().add_device({
        "id": device_id, "name": name or device_id, "transport": transport,
        "host": host, "port": port, "serial_port": serial_port,
        "baudrate": baudrate, "parity": parity, "stopbits": stopbits,
        "bytesize": bytesize, "framer": framer,
        "handle_local_echo": handle_local_echo, "unit_id": unit_id,
        "allow_write": allow_write, "note": note, "points": [],
    })
    return ok({"ok": True, "device": dev.to_dict()})


@mcp.tool(name="modbus_add_point", annotations={
    "title": "Tambah titik ukur ke profil", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": False})
@guarded
def modbus_add_point(
    device_id: Annotated[str, Field(description="ID perangkat")],
    name: Annotated[str, Field(description="Nama titik ukur, mis. 'suhu_masuk'")],
    address: Annotated[int, Field(description="Alamat register basis 0", ge=0, le=65535)],
    table: Annotated[str, Field(description="holding | input | coil | discrete")] = "holding",
    datatype: Annotated[str, Field(description="uint16, int16, uint32, int32, "
                                               "float32, float64, string "
                                               "(diabaikan untuk coil/discrete)")] = "uint16",
    word_order: Annotated[str, Field(description="'big' atau 'little' untuk "
                                                 "tipe >16 bit")] = "big",
    length: Annotated[int, Field(description="Jumlah register untuk datatype string",
                                 ge=1, le=64)] = 1,
    scale: Annotated[float, Field(description="Pengali, mis. 0.1 kalau register "
                                              "menyimpan nilai x10")] = 1.0,
    offset: Annotated[float, Field(description="Ditambahkan setelah dikalikan")] = 0.0,
    unit: Annotated[str, Field(description="Satuan, mis. degC, bar, %")] = "",
    description: Annotated[str, Field(description="Arti titik ukur ini")] = "",
    writable: Annotated[bool, Field(description="Boleh ditulis")] = False,
    alarm_low: Annotated[float | None, Field(description="Batas bawah alarm")] = None,
    alarm_high: Annotated[float | None, Field(description="Batas atas alarm")] = None,
) -> str:
    """Tambahkan satu titik ukur ke profil perangkat: alamat register, cara
    menafsirkan datanya, skala, satuan, dan batas alarm."""
    p = engine().add_point(device_id, {
        "name": name, "address": address, "table": table, "datatype": datatype,
        "word_order": word_order, "length": length, "scale": scale,
        "offset": offset, "unit": unit, "description": description,
        "writable": writable, "alarm_low": alarm_low, "alarm_high": alarm_high})
    return ok({"ok": True, "device": device_id, "point": p.to_dict()})


@mcp.tool(name="modbus_remove_device", annotations={
    "title": "Hapus profil perangkat", "readOnlyHint": False,
    "destructiveHint": True, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_remove_device(
    device_id: Annotated[str, Field(description="ID perangkat yang dihapus")],
) -> str:
    """Hapus profil perangkat beserta file JSON-nya. Riwayat pembacaan tetap
    tersimpan di database."""
    engine().remove_device(device_id)
    return ok({"ok": True, "removed": device_id})


@mcp.tool(name="modbus_scan_units", annotations={
    "title": "Cari unit id yang menjawab", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_scan_units(
    device_id: Annotated[str, Field(description="ID perangkat (dipakai sebagai "
                                                "pengaturan koneksi)")],
    start: Annotated[int, Field(description="Unit id awal", ge=0, le=255)] = 1,
    end: Annotated[int, Field(description="Unit id akhir", ge=0, le=255)] = 16,
) -> str:
    """Sapu rentang unit/slave id dan lihat mana yang menjawab. Berguna kalau
    alamat slave sebuah alat tidak diketahui. Hanya membaca 1 register per id."""
    hasil = engine().scan_units(device_id, start, end)
    aktif = [h["unit_id"] for h in hasil if h["responded"]]
    return ok({"ok": True, "device": device_id, "responding_unit_ids": aktif,
               "detail": hasil,
               "note": "Simulator bawaan menjawab semua unit id; alat sungguhan "
                       "biasanya hanya satu."})


@mcp.tool(name="modbus_scan_registers", annotations={
    "title": "Sapu blok register", "readOnlyHint": True,
    "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@guarded
def modbus_scan_registers(
    device_id: Annotated[str, Field(description="ID perangkat")],
    table: Annotated[str, Field(description="holding | input | coil | discrete")] = "holding",
    start: Annotated[int, Field(description="Alamat awal", ge=0, le=65535)] = 0,
    end: Annotated[int, Field(description="Alamat akhir", ge=0, le=65535)] = 31,
    chunk: Annotated[int, Field(description="Berapa alamat per permintaan",
                                ge=1, le=125)] = 8,
) -> str:
    """Baca blok alamat berurutan dan laporkan isinya — untuk memetakan alat
    yang dokumentasinya tidak lengkap. Blok yang ditolak perangkat ikut
    dilaporkan supaya ketahuan batas alamat yang valid."""
    return ok({"ok": True, "device": device_id, "table": table,
               "blocks": engine().scan_registers(device_id, table, start, end, chunk)})


# ══════════════════════════════════════════════════════════════
#  SIMULATOR
# ══════════════════════════════════════════════════════════════
@mcp.tool(name="modbus_simulator", annotations={
    "title": "Kendalikan simulator bawaan", "readOnlyHint": False,
    "destructiveHint": False, "idempotentHint": True, "openWorldHint": False})
@guarded
def modbus_simulator(
    action: Annotated[str, Field(description="'start', 'stop', atau 'status'")] = "status",
    port: Annotated[int, Field(description="Port TCP simulator", ge=1, le=65535)] = 15020,
) -> str:
    """Nyalakan/matikan simulator perangkat Modbus bawaan (tangki dengan pompa,
    pemanas, dan flowmeter yang nilainya bergerak sendiri). Dipakai untuk
    mencoba semua tool ini tanpa hardware — pasangkan dengan perangkat
    berprofil 'simulator'."""
    global _sim
    if action == "start":
        if _sim and _sim.is_running:
            return ok({"ok": True, "running": True, "url": _sim.url,
                       "note": "sudah jalan"})
        _sim = ModbusSimulator(port=port)
        _sim.start()
        return ok({"ok": True, "running": _sim.is_running, "url": _sim.url,
                   "next": "Panggil modbus_connect('simulator') lalu "
                           "modbus_read_all('simulator')."})
    if action == "stop":
        if _sim:
            _sim.stop()
        return ok({"ok": True, "running": False})
    return ok({"ok": True, "running": bool(_sim and _sim.is_running),
               "url": _sim.url if _sim else None})


if __name__ == "__main__":
    mcp.run()
