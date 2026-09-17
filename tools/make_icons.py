"""
Bikin ikon aplikasi Claude Modbus dari satu definisi.

Lambangnya: gelombang kotak (sinyal digital) di atas badge terracotta —
langsung kebaca sebagai "data digital", dan masih jelas di ukuran 16 px.

    python -m tools.make_icons

Menghasilkan:
    assets/icon.ico        multi-ukuran untuk jendela & taskbar Windows
    assets/icon-512.png    ikon besar
    assets/icon-256.png
    gui/web/icon.svg       dipakai favicon + lambang di sidebar GUI
    docs/logo.png          lockup lambang + nama untuk README
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
WEB = os.path.join(ROOT, "gui", "web")
DOCS = os.path.join(ROOT, "docs")

TOP = (224, 138, 102)        # #E08A66
BOTTOM = (193, 95, 60)       # #C15F3C
CREAM = (253, 251, 246)      # #FDFBF6
INK = (31, 30, 29)           # #1F1E1D

SS = 4                       # supersampling: gambar besar lalu dikecilkan
BASE = 256                   # ukuran acuan; semua koordinat relatif ke ini


def _gradient(size: int) -> Image.Image:
    """Gradasi vertikal terracotta."""
    g = Image.new("RGB", (1, size))
    px = g.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
    return g.resize((size, size), Image.NEAREST)


def draw_mark(size: int, margin_ratio: float = 0.055) -> Image.Image:
    """Gambar lambang persegi membulat berisi gelombang kotak."""
    S = size * SS
    m = S * margin_ratio
    radius = (S - 2 * m) * 0.235

    # badge: gradasi dipotong oleh mask sudut membulat
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((m, m, S - m, S - m), radius=radius, fill=255)
    badge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    badge.paste(_gradient(S).convert("RGBA"), (0, 0), mask)

    d = ImageDraw.Draw(badge)

    # gelombang kotak: naik-turun-naik, satu setengah siklus
    lo, hi = S * 0.655, S * 0.375
    xs = [S * 0.205, S * 0.405, S * 0.605, S * 0.805]
    w = S * 0.085
    pts = [(xs[0], lo), (xs[0], hi), (xs[1], hi), (xs[1], lo),
           (xs[2], lo), (xs[2], hi), (xs[3], hi)]
    d.line(pts, fill=CREAM, width=round(w), joint="curve")
    for x, y in (pts[0], pts[-1]):                      # ujung dibulatkan
        d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=CREAM)

    # titik baca di ujung kanan, penanda "sedang mengambil data"
    r = S * 0.062
    cx, cy = xs[3], hi
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=CREAM)
    d.ellipse((cx - r * 0.42, cy - r * 0.42, cx + r * 0.42, cy + r * 0.42),
              fill=BOTTOM)

    return badge.resize((size, size), Image.LANCZOS)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#E08A66"/>
      <stop offset="1" stop-color="#C15F3C"/>
    </linearGradient>
  </defs>
  <rect x="14" y="14" width="228" height="228" rx="53.6" fill="url(#g)"/>
  <path d="M52.5 167.7 V96 H103.7 V167.7 H154.9 V96 H206.1"
        fill="none" stroke="#FDFBF6" stroke-width="21.8"
        stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="206.1" cy="96" r="15.9" fill="#FDFBF6"/>
  <circle cx="206.1" cy="96" r="6.7" fill="#C15F3C"/>
</svg>
"""


def make_logo(path: str) -> None:
    """Lockup: lambang + tulisan 'Claude Modbus' untuk README."""
    W, H = 1100, 300
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    mark = draw_mark(200)
    img.paste(mark, (48, (H - 200) // 2), mark)

    d = ImageDraw.Draw(img)
    font_big = font_small = None
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf"):
        try:
            font_big = ImageFont.truetype(name, 76)
            break
        except OSError:
            continue
    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            font_small = ImageFont.truetype(name, 34)
            break
        except OSError:
            continue
    font_big = font_big or ImageFont.load_default()
    font_small = font_small or ImageFont.load_default()

    d.text((288, 96), "Claude Modbus", font=font_big, fill=INK)
    d.text((292, 180), "baca  ·  olah  ·  kendalikan", font=font_small,
           fill=(120, 116, 108))
    img.save(path)


def main() -> None:
    for folder in (ASSETS, WEB, DOCS):
        os.makedirs(folder, exist_ok=True)

    master = draw_mark(512)
    master.save(os.path.join(ASSETS, "icon-512.png"))
    master.resize((256, 256), Image.LANCZOS).save(os.path.join(ASSETS, "icon-256.png"))

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [draw_mark(s) for s in sizes]          # digambar ulang tiap ukuran
    frames[-1].save(os.path.join(ASSETS, "icon.ico"),
                    format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])

    with open(os.path.join(WEB, "icon.svg"), "w", encoding="utf-8") as f:
        f.write(SVG)

    make_logo(os.path.join(DOCS, "logo.png"))

    print("ikon dibuat:")
    for p in ("assets/icon.ico", "assets/icon-512.png", "assets/icon-256.png",
              "gui/web/icon.svg", "docs/logo.png"):
        full = os.path.join(ROOT, p.replace("/", os.sep))
        print(f"  {p:<24} {os.path.getsize(full):>7} byte")


if __name__ == "__main__":
    main()
