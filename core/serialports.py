"""Deteksi port serial, dengan konverter MOXA/USB-RS485 diletakkan paling atas."""

from __future__ import annotations

import serial.tools.list_ports

# kata kunci yang menandakan konverter RS-485 pada deskripsi driver
PRIORITAS = ("moxa", "uport", "rs-485", "rs485", "usb serial", "ft232", "ch340",
             "cp210", "prolific", "pl2303")


def list_serial_ports() -> list[dict]:
    """[{device, description, hwid, likely_rs485}] - MOXA duluan."""
    out = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").strip()
        manuf = (p.manufacturer or "").strip()
        teks = f"{desc} {manuf}".lower()
        skor = next((i for i, k in enumerate(PRIORITAS) if k in teks), len(PRIORITAS))
        out.append({
            "device": p.device,
            "description": desc or "(tanpa keterangan)",
            "manufacturer": manuf,
            "hwid": (p.hwid or "").strip(),
            "likely_rs485": skor < len(PRIORITAS),
            "is_moxa": "moxa" in teks or "uport" in teks,
            "_skor": skor,
        })
    out.sort(key=lambda d: (d["_skor"], d["device"]))
    for d in out:
        d.pop("_skor")
    return out


def describe_ports() -> str:
    ports = list_serial_ports()
    if not ports:
        return ("Tidak ada port serial terdeteksi. Colokkan konverter USB-RS485 "
                "(mis. MOXA UPort) dan pastikan drivernya terpasang.")
    baris = [f"{p['device']:<8} {p['description']}"
             + ("   <- kemungkinan konverter RS-485" if p["likely_rs485"] else "")
             for p in ports]
    return "\n".join(baris)


if __name__ == "__main__":
    print(describe_ports())
