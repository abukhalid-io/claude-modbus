"""Simpan/muat profil perangkat sebagai JSON di folder profiles/."""

from __future__ import annotations

import json
import os
import re

from core.models import Device, ModbusConfigError

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "profiles")


def _safe(device_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", device_id).strip("_")
    if not slug:
        raise ModbusConfigError(f"device id '{device_id}' tidak bisa dijadikan nama file")
    return slug


def path_for(device_id: str, folder: str | None = None) -> str:
    return os.path.join(folder or DEFAULT_DIR, f"{_safe(device_id)}.json")


def load_all(folder: str | None = None) -> list[Device]:
    folder = folder or DEFAULT_DIR
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                out.append(Device(**json.load(f)))
        except (OSError, ValueError, TypeError, ModbusConfigError) as exc:
            print(f"[profil] lewati {name}: {exc}")
    return out


def save(device: Device, folder: str | None = None) -> str:
    folder = folder or DEFAULT_DIR
    os.makedirs(folder, exist_ok=True)
    p = path_for(device.id, folder)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(device.to_dict(), f, indent=2, ensure_ascii=False)
    return p


def delete(device_id: str, folder: str | None = None) -> bool:
    try:
        os.remove(path_for(device_id, folder))
        return True
    except OSError:
        return False
