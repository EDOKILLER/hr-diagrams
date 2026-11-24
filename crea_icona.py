#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Icona di Diagrammi H-R: un piccolo diagramma H-R (sequenza principale dal blu al rosso,
ramo delle giganti, nane bianche) con un'isocrona azzurra, su fondo indaco.
© 2026 Edoardo Battistel"""
import math
import random
from PIL import Image, ImageDraw, ImageFilter

LATO = 512
S = 4  # sovracampionamento


def colore_stella(t):
    """t = 0 (blu caldo) … 1 (rosso freddo)."""
    tappe = [(0.0, (150, 185, 255)), (0.25, (225, 235, 255)), (0.5, (255, 244, 214)),
             (0.7, (255, 205, 130)), (0.85, (255, 150, 80)), (1.0, (255, 95, 70))]
    for (a, ca), (b, cb) in zip(tappe, tappe[1:]):
        if t <= b:
            u = (t - a) / (b - a)
            return tuple(int(ca[i] + u * (cb[i] - ca[i])) for i in range(3))
    return tappe[-1][1]


def crea(percorso_png="diagrammi_hr.png", percorso_ico="diagrammi_hr.ico"):
    L = LATO * S
    img = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    # fondo: quadrato arrotondato con sfumatura indaco
    fondo = Image.new("RGBA", (L, L))
    d = ImageDraw.Draw(fondo)
    for y in range(L):
        u = y / L
        d.line([(0, y), (L, y)], fill=(int(28 + 10 * u), int(24 + 6 * u), int(78 - 30 * u), 255))
    maschera = Image.new("L", (L, L), 0)
    ImageDraw.Draw(maschera).rounded_rectangle([0, 0, L - 1, L - 1], radius=int(0.18 * L), fill=255)
    img.paste(fondo, (0, 0), maschera)

    # assi
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = 0.16 * L, 0.12 * L, 0.88 * L, 0.86 * L
    d.line([(x0, y0), (x0, y1), (x1, y1)], fill=(170, 180, 225, 200), width=int(0.012 * L))

    def punto(c, m):  # c 0..1 (colore), m 0..1 (luminosità, 0 = brillante)
        return x0 + 0.06 * L + c * (x1 - x0 - 0.1 * L), y0 + 0.04 * L + m * (y1 - y0 - 0.1 * L)

    random.seed(7)

    def sp(t):  # sequenza principale: t = 0 punto di svolta … 1 nane rosse
        return 0.30 + 0.62 * t, 0.50 + 0.44 * t + 0.05 * math.sin(t * math.pi)

    def gig(t):  # dal punto di svolta al ramo delle giganti
        c0, m0 = sp(0.0)
        if t < 0.25:
            u = t / 0.25
            return c0 + 0.16 * u, m0 - 0.04 * u
        u = (t - 0.25) / 0.75
        return c0 + 0.16 + 0.42 * u, m0 - 0.04 - 0.40 * u ** 1.25

    stelle = []
    for _ in range(150):
        t = random.random() ** 0.9
        c, m = sp(t)
        stelle.append((c + random.gauss(0, 0.012), m + random.gauss(0, 0.012), colore_stella(0.25 + 0.75 * t),
                       0.0075 + 0.0075 * (1 - t)))
    for _ in range(50):
        t = random.random()
        c, m = gig(t)
        stelle.append((c + random.gauss(0, 0.01), m + random.gauss(0, 0.01), colore_stella(0.55 + 0.45 * t),
                       0.009 + 0.008 * t))
    for _ in range(6):  # stelle vagabonde blu
        t = random.random() * 0.3
        stelle.append((0.30 - 0.25 * t, 0.50 - 0.32 * t, colore_stella(0.05), 0.012))
    for _ in range(12):  # nane bianche
        stelle.append((0.06 + random.random() * 0.14, 0.86 + random.random() * 0.08, (220, 228, 255), 0.006))

    # alone luminoso
    alone = Image.new("RGBA", (L, L), (0, 0, 0, 0))
    da = ImageDraw.Draw(alone)
    for c, m, col, r in stelle:
        x, y = punto(c, m)
        rr = r * L * 2.2
        da.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (70,))
    alone = alone.filter(ImageFilter.GaussianBlur(0.02 * L))
    img = Image.alpha_composite(img, Image.composite(alone, Image.new("RGBA", (L, L)), maschera))
    d = ImageDraw.Draw(img)

    # isocrona
    linea = [punto(*sp(1 - i / 120)) for i in range(121)] + [punto(*gig(i / 120)) for i in range(1, 121)]
    d.line(linea, fill=(65, 224, 255, 230), width=int(0.011 * L), joint="curve")

    for c, m, col, r in stelle:
        x, y = punto(c, m)
        rr = r * L
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col + (255,))

    # scritta H-R
    try:
        from PIL import ImageFont
        font = None
        for nome in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(nome, int(0.13 * L))
                break
            except OSError:
                continue
        if font:
            d.text((0.86 * L, 0.10 * L), "H-R", font=font, fill=(255, 214, 120, 255), anchor="rt")
    except Exception:
        pass

    img = img.resize((LATO, LATO), Image.LANCZOS)
    img.save(percorso_png)
    img.save(percorso_ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return percorso_png


if __name__ == "__main__":
    print(crea())
