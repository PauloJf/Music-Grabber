"""Regenerate the web UI's icons (frontend/public/). Needs Pillow; the PNGs are committed, so this
only runs when the artwork changes:  python scripts/make-icons.py

The mark is the download arrow of the old favicon in the app's own colours (ink ground, vermilion
arrow, the same two as the wordmark's dot)."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public"
INK, SIGNAL = (23, 21, 15), (255, 90, 60)
SS = 4                                            # supersampling for smooth edges


def mark(size: int, *, scale: float, rounded: bool) -> Image.Image:
    """The icon at `size` px. `scale` is the share of the canvas the arrow's box takes (a maskable
    icon keeps it inside the central 80% safe zone); `rounded` clips the ground to a rounded square."""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22) if rounded else 0, fill=INK + (255,))
    box = s * scale                               # arrow box, centred
    x0 = (s - box) / 2
    cx, w = s / 2, box * 0.105                    # stroke width
    top, tip = x0 + box * 0.04, x0 + box * 0.64
    base = x0 + box * 0.94
    d.line((cx, top, cx, tip), fill=SIGNAL, width=int(w))             # shaft
    head = box * 0.30
    d.line((cx - head, tip - head, cx, tip), fill=SIGNAL, width=int(w))
    d.line((cx + head, tip - head, cx, tip), fill=SIGNAL, width=int(w))
    for px, py in ((cx - head, tip - head), (cx + head, tip - head), (cx, top), (cx, tip)):
        d.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=SIGNAL)     # round caps/joins
    d.line((cx - box * 0.36, base, cx + box * 0.36, base), fill=SIGNAL, width=int(w))   # the tray
    for px in (cx - box * 0.36, cx + box * 0.36):
        d.ellipse((px - w / 2, base - w / 2, px + w / 2, base + w / 2), fill=SIGNAL)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    mark(192, scale=0.62, rounded=True).save(OUT / "icon-192.png")
    mark(512, scale=0.62, rounded=True).save(OUT / "icon-512.png")
    mark(512, scale=0.46, rounded=False).save(OUT / "icon-maskable-512.png")    # full-bleed, safe zone
    mark(180, scale=0.62, rounded=False).convert("RGB").save(OUT / "apple-touch-icon.png")  # iOS rounds it itself
    mark(48, scale=0.7, rounded=True).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    (OUT / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#17150f"/>'
        '<path d="M16 7.5v11m0 0-4.6-4.6m4.6 4.6 4.6-4.6M9.5 23.5h13" stroke="#ff5a3c" stroke-width="2.6" fill="none" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>\n', encoding="utf-8")


if __name__ == "__main__":
    main()
