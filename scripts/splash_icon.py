"""Build the Android splash icon, adaptive launcher foreground and header wordmark from chrome renders.

Usage:
    python scripts/splash_icon.py <source-ring.png> [--fill 0.97] [--launcher-fill 0.92]
    python scripts/splash_icon.py --wordmark <source-wordmark.png>

Writes:
  android/app/src/main/res/drawable-{mdpi..xxxhdpi}/splash_ovrly.webp
  android/app/src/main/res/mipmap-{mdpi..xxxhdpi}/ic_launcher_foreground.webp
  android/app/src/main/res/drawable-{mdpi..xxxhdpi}/wordmark_ovrly.webp   (with --wordmark)
and fails if the splash would not render correctly as an Android 12+ splash icon, or if any
edge still carries the red anti-aliasing fringe the source renders come with.

Why these shapes: the platform inflates the splash icon on a 288dp canvas but only shows its
central 192dp under the device icon mask (AdaptiveForegroundDrawable insets to 2/3), so the
ring sits inside that inner circle at 4x density (1152px canvas, 768px mask). Adaptive launcher
layers are 108dp with a 66dp safe zone that every mask shape keeps visible.

Requires Pillow and numpy (dev-only; not part of the Android build or CI).
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

CANVAS_DP = 288              # platform splash icon canvas
CANVAS = CANVAS_DP * 4       # 1152px at xxxhdpi, used for the pixel checks
MASK = CANVAS * 2 // 3       # 768px = 192dp visible circle
QUALITY = 95                 # lossy WebP, alpha kept exact
MAX_EDGE_RED_BIAS = 6.0      # mean R - max(G,B) over anti-aliased edge pixels
RES = Path(__file__).resolve().parents[1] / "android/app/src/main/res"
# Adaptive launcher foreground: 108dp canvas, ring inside the 66dp safe zone (ratio 66/108).
LAUNCHER_DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
LAUNCHER_CANVAS_DP = 108
LAUNCHER_SAFE_DP = 66
# In-app header wordmark: rendered chrome "ovrly" cropped tight, exported at this dp height.
WORDMARK_HEIGHT_DP = 40


def defringe(rgba: np.ndarray) -> np.ndarray:
    """Refill translucent, red-tinted or near-edge pixels from the clean interior, keeping alpha."""
    alpha = rgba[..., 3]
    rgb = rgba[..., :3].copy()
    opaque = alpha >= 250
    red_dominant = (rgb[..., 0] - np.maximum(rgb[..., 1], rgb[..., 2]) > 20) & (alpha > 0)
    boundary = ~opaque
    for _ in range(2):
        grown = boundary.copy()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= np.roll(np.roll(boundary, dy, axis=0), dx, axis=1)
        boundary = grown
    unknown = (alpha > 0) & (boundary | red_dominant)
    known = (alpha > 0) & ~unknown
    for _ in range(12):
        acc = np.zeros_like(rgb)
        cnt = np.zeros(alpha.shape, dtype=np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    k = np.roll(np.roll(known, dy, axis=0), dx, axis=1)
                    acc += np.roll(np.roll(rgb, dy, axis=0), dx, axis=1) * k[..., None]
                    cnt += k
        fill = unknown & ~known & (cnt > 0)
        if not fill.any():
            break
        rgb[fill] = acc[fill] / cnt[fill][:, None]
        known |= fill
    rgba[..., :3] = rgb
    rgba[alpha == 0, :3] = 0
    return rgba


def clean_crop(source: Path) -> Image.Image:
    """Defringed artwork cropped to its tight bounding box (not squared)."""
    rgba = defringe(np.array(Image.open(source).convert("RGBA")).astype(np.float32))
    ys, xs = np.where(rgba[..., 3] > 8)
    return Image.fromarray(rgba.astype(np.uint8)).crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def clean_ring(source: Path) -> Image.Image:
    """Defringed ring cropped to a tight, centred square."""
    ring = clean_crop(source)
    side = max(ring.size)
    square = Image.new("RGBA", (side, side))
    square.paste(ring, ((side - ring.width) // 2, (side - ring.height) // 2))
    return square


def place(ring: Image.Image, canvas: int, diameter: int) -> Image.Image:
    out = Image.new("RGBA", (canvas, canvas))
    out.paste(ring.resize((diameter, diameter), Image.LANCZOS), ((canvas - diameter) // 2,) * 2)
    return out


def compose(source: Path, fill: float) -> Image.Image:
    return place(clean_ring(source), CANVAS, round(MASK * fill))


def check(image: Image.Image) -> None:
    a = np.array(image).astype(int)
    alpha = a[..., 3]
    c = CANVAS / 2
    yy, xx = np.where(alpha > 8)
    reach = np.sqrt((xx + 0.5 - c) ** 2 + (yy + 0.5 - c) ** 2).max()
    edge = a[(alpha > 8) & (alpha < 250)]
    bias = (edge[:, 0] - np.maximum(edge[:, 1], edge[:, 2])).mean()
    problems = []
    if reach >= MASK / 2:
        problems.append(f"ring reaches {reach:.1f}px, outside the {MASK / 2:.0f}px mask")
    if reach < MASK / 2 * 0.95:
        problems.append(f"ring only reaches {reach:.1f}px; it will look small")
    if bias > MAX_EDGE_RED_BIAS:
        problems.append(f"edge pixels are red-tinted (mean R - max(G,B) = {bias:.1f})")
    if alpha[0, 0] or alpha[CANVAS // 2, CANVAS // 2]:
        problems.append("background or ring hole is not transparent")
    if problems:
        sys.exit("splash icon rejected:\n  " + "\n  ".join(problems))
    print(f"ring reach {reach / (MASK / 2):.3f} of mask, edge red bias {bias:+.1f}")


def encode(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "WEBP", quality=QUALITY, method=6, exact=True)
    decoded = np.array(Image.open(io.BytesIO(buf.getvalue())).convert("RGBA"))
    if not np.array_equal(decoded[..., 3], np.array(image)[..., 3]):
        sys.exit("WebP encoding altered the alpha channel")
    return buf.getvalue()


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"wrote {path.relative_to(RES.parents[3])} ({len(data) / 1024:.0f} KB)")


def check_edges(image: Image.Image, label: str) -> None:
    a = np.array(image).astype(int)
    edge = a[(a[..., 3] > 8) & (a[..., 3] < 250)]
    bias = (edge[:, 0] - np.maximum(edge[:, 1], edge[:, 2])).mean()
    if bias > MAX_EDGE_RED_BIAS:
        sys.exit(f"{label} rejected: edge pixels are red-tinted (mean R - max(G,B) = {bias:.1f})")
    print(f"{label}: edge red bias {bias:+.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, nargs="?", help="rendered ring PNG with transparent background")
    parser.add_argument("--fill", type=float, default=0.97, help="splash ring diameter as a fraction of the mask")
    parser.add_argument("--launcher-fill", type=float, default=0.92,
                        help="launcher ring diameter as a fraction of the 66dp adaptive-icon safe zone")
    parser.add_argument("--wordmark", type=Path, help="rendered 'ovrly' wordmark PNG for the in-app header")
    args = parser.parse_args()
    if args.source is None and args.wordmark is None:
        parser.error("give a ring source, --wordmark, or both")

    if args.source is not None:
        ring = clean_ring(args.source)
        for density, scale in LAUNCHER_DENSITIES.items():
            canvas = round(CANVAS_DP * scale)
            splash = place(ring, canvas, round(canvas * 2 / 3 * args.fill))
            if density == "xxxhdpi":
                check(splash)
            write(RES / f"drawable-{density}/splash_ovrly.webp", encode(splash))
            lc = round(LAUNCHER_CANVAS_DP * scale)
            diameter = round(LAUNCHER_SAFE_DP * scale * args.launcher_fill)
            write(RES / f"mipmap-{density}/ic_launcher_foreground.webp", encode(place(ring, lc, diameter)))

    if args.wordmark is not None:
        art = clean_crop(args.wordmark)
        check_edges(art, "wordmark")
        for density, scale in LAUNCHER_DENSITIES.items():
            height = round(WORDMARK_HEIGHT_DP * scale)
            width = round(art.width * height / art.height)
            write(RES / f"drawable-{density}/wordmark_ovrly.webp",
                  encode(art.resize((width, height), Image.LANCZOS)))


if __name__ == "__main__":
    main()
