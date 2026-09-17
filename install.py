"""
Pemasang otomatis Claude Modbus.

Sekali jalan, semua kebutuhan beres:
  1. cek versi Python
  2. buat virtual environment .venv (terpisah dari Python sistem)
  3. pasang semua dependensi
  4. verifikasi: impor paket + uji mandiri tanpa hardware
  5. siapkan ikon aplikasi (kalau belum ada)
  6. buat pintasan Desktop & Start Menu berikon (Windows)
  7. tawarkan mendaftarkan MCP server ke Claude Code

Pakai:
    python install.py                 # pasang lengkap, tanya soal MCP
    python install.py --mcp           # sekalian daftarkan MCP tanpa bertanya
    python install.py --no-mcp        # lewati pendaftaran MCP
    python install.py --system        # pasang ke Python sistem, tanpa venv
    python install.py --no-shortcut   # jangan buat pintasan
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
MIN_PYTHON = (3, 10)
WIN = sys.platform == "win32"

OK, BAD, DOT = "[ok]", "[!!]", " - "


def say(step: str, text: str = "") -> None:
    print(f"\n{step} {text}".rstrip())


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, **kw)


def venv_python(base: str = VENV) -> str:
    return (os.path.join(base, "Scripts", "python.exe") if WIN
            else os.path.join(base, "bin", "python"))


def venv_pythonw(base: str = VENV) -> str:
    """Windows: pythonw.exe menjalankan GUI tanpa jendela konsol hitam."""
    p = os.path.join(base, "Scripts", "pythonw.exe")
    return p if WIN and os.path.exists(p) else venv_python(base)


# ══════════════════════════════════════════════════════════════
#  LANGKAH
# ══════════════════════════════════════════════════════════════
def step_python() -> None:
    say("1/7", "Memeriksa Python")
    if sys.version_info < MIN_PYTHON:
        print(f"{BAD} Butuh Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+, "
              f"yang terpasang {sys.version.split()[0]}.")
        print(f"{DOT}Unduh dari https://python.org lalu jalankan ulang install.py")
        sys.exit(1)
    print(f"{OK} Python {sys.version.split()[0]} di {sys.executable}")


def step_venv(system: bool) -> str:
    say("2/7", "Menyiapkan lingkungan")
    if system:
        print(f"{OK} Memakai Python sistem (--system)")
        return sys.executable
    if os.path.exists(venv_python()):
        print(f"{OK} .venv sudah ada, dipakai ulang")
        return venv_python()
    print(f"{DOT}Membuat .venv ...")
    r = run([sys.executable, "-m", "venv", VENV])
    if r.returncode != 0 or not os.path.exists(venv_python()):
        print(f"{BAD} Gagal membuat virtual environment.")
        print(f"{DOT}Coba: python -m pip install --user virtualenv")
        print(f"{DOT}Atau pasang tanpa venv: python install.py --system")
        sys.exit(1)
    print(f"{OK} .venv dibuat")
    return venv_python()


def step_deps(py: str) -> None:
    say("3/7", "Memasang dependensi")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "--quiet"])
    req = os.path.join(ROOT, "requirements.txt")
    r = run([py, "-m", "pip", "install", "-r", req])
    if r.returncode != 0:
        print(f"{BAD} pip gagal memasang dependensi.")
        print(f"{DOT}Periksa koneksi internet, lalu jalankan ulang install.py")
        sys.exit(1)
    # pillow hanya dipakai untuk membuat ikon; tidak wajib untuk menjalankan app
    run([py, "-m", "pip", "install", "pillow", "--quiet"])
    print(f"{OK} pymodbus, pywebview, mcp, pyserial terpasang")


def step_verify(py: str) -> None:
    say("4/7", "Memverifikasi pemasangan")
    cek = ("import pymodbus, webview, mcp, serial, importlib.metadata as m;"
           "print('pymodbus', m.version('pymodbus'));"
           "print('pywebview', m.version('pywebview'));"
           "print('mcp', m.version('mcp'))")
    r = run([py, "-c", cek], capture_output=True)
    if r.returncode != 0:
        print(f"{BAD} Paket belum bisa diimpor:\n{r.stderr.strip()}")
        sys.exit(1)
    for line in r.stdout.strip().splitlines():
        print(f"{DOT}{line}")

    r = run([py, "-m", "core.models"], capture_output=True)
    if r.returncode != 0:
        print(f"{BAD} Uji mandiri gagal:\n{r.stderr.strip()}")
        sys.exit(1)
    print(f"{OK} Uji model lulus - inti aplikasi sehat")


def step_icons(py: str) -> None:
    say("5/7", "Menyiapkan ikon")
    ico = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.exists(ico):
        print(f"{OK} assets/icon.ico sudah ada")
        return
    r = run([py, "-m", "tools.make_icons"], capture_output=True)
    if r.returncode == 0 and os.path.exists(ico):
        print(f"{OK} Ikon dibuat ulang dari tools/make_icons.py")
    else:
        print(f"{DOT}Lewati: ikon tidak bisa dibuat (butuh pillow). "
              "Aplikasi tetap jalan, hanya ikon taskbar yang standar.")


def step_shortcut(py: str, skip: bool) -> None:
    say("6/7", "Membuat pintasan")
    if skip:
        print(f"{DOT}Dilewati (--no-shortcut)")
        return
    if not WIN:
        print(f"{DOT}Pintasan otomatis baru tersedia di Windows. "
              f"Jalankan manual: {py} -m gui.app")
        return

    target = venv_pythonw(os.path.dirname(os.path.dirname(py))
                          if ".venv" in py else VENV)
    if not os.path.exists(target):
        target = py
    ico = os.path.join(ROOT, "assets", "icon.ico")
    dibuat = []
    for folder in (os.path.join(os.path.expanduser("~"), "Desktop"),
                   os.path.join(os.environ.get("APPDATA", ""), "Microsoft",
                                "Windows", "Start Menu", "Programs")):
        if not folder or not os.path.isdir(folder):
            continue
        lnk = os.path.join(folder, "Claude Modbus.lnk")
        ps = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
            "$s.TargetPath = '{target}';"
            "$s.Arguments = '-m gui.app';"
            "$s.WorkingDirectory = '{root}';"
            "$s.IconLocation = '{ico}';"
            "$s.Description = 'Claude Modbus - baca, olah, kendalikan perangkat Modbus';"
            "$s.Save()"
        ).format(lnk=lnk.replace("'", "''"), target=target.replace("'", "''"),
                 root=ROOT.replace("'", "''"), ico=ico.replace("'", "''"))
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(lnk):
            dibuat.append(os.path.basename(folder))
    if dibuat:
        print(f"{OK} Pintasan berikon dibuat di: {', '.join(dibuat)}")
    else:
        print(f"{DOT}Pintasan tidak bisa dibuat; pakai JALANKAN.bat saja")


def step_mcp(py: str, mode: str) -> None:
    say("7/7", "Menyambungkan ke Claude (MCP)")
    perintah = ["claude", "mcp", "add", "modbus", "--", py, "-m", "mcp_server.server"]
    tampil = f'claude mcp add modbus -- "{py}" -m mcp_server.server'

    if shutil.which("claude") is None:
        print(f"{DOT}Claude Code belum ada di PATH. Kalau nanti sudah terpasang, "
              "jalankan sendiri:")
        print(f"{DOT}{tampil}")
        return

    if mode == "ask":
        try:
            jawab = input("\n    Daftarkan MCP server ke Claude Code sekarang? "
                          "[Y/n] ").strip().lower()
        except EOFError:
            jawab = "n"
        mode = "yes" if jawab in ("", "y", "ya", "yes") else "no"
    if mode == "no":
        print(f"{DOT}Dilewati. Perintahnya kalau nanti mau:")
        print(f"{DOT}{tampil}")
        return

    r = subprocess.run(perintah, cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0:
        print(f"{OK} MCP server 'modbus' terdaftar di Claude Code")
    else:
        print(f"{DOT}Gagal mendaftarkan otomatis ({r.stderr.strip()[:120]}). "
              "Jalankan manual:")
        print(f"{DOT}{tampil}")


# ══════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="Pemasang Claude Modbus")
    ap.add_argument("--system", action="store_true",
                    help="pasang ke Python sistem, jangan bikin .venv")
    ap.add_argument("--no-shortcut", action="store_true",
                    help="jangan buat pintasan Desktop/Start Menu")
    mcp = ap.add_mutually_exclusive_group()
    mcp.add_argument("--mcp", action="store_true", help="daftarkan MCP tanpa bertanya")
    mcp.add_argument("--no-mcp", action="store_true", help="lewati pendaftaran MCP")
    args = ap.parse_args()

    print("=" * 62)
    print("  Claude Modbus - pemasangan otomatis")
    print(f"  folder: {ROOT}")
    print("=" * 62)

    step_python()
    py = step_venv(args.system)
    step_deps(py)
    step_verify(py)
    step_icons(py)
    step_shortcut(py, args.no_shortcut)
    step_mcp(py, "yes" if args.mcp else "no" if args.no_mcp else "ask")

    print("\n" + "=" * 62)
    print("  SELESAI")
    print("=" * 62)
    print("\n  Jalankan aplikasinya:")
    print("      JALANKAN.bat" if WIN else f"      {py} -m gui.app")
    print("\n  Belum punya alat Modbus? Di GUI klik 'Nyalakan' pada kotak")
    print("  Simulator bawaan, lalu 'Hubungkan'.")
    print("\n  Uji semuanya tanpa hardware:")
    print(f"      {py} selftest.py")
    print(f"      {py} selftest_mcp.py\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
