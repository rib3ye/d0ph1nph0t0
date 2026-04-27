"""Convert arbitrary images into Flipper Zero (1-bit) assets.

Default outputs per source image (matches what the Flipper image skill ships):
  - <name>_128x64.png        full-screen 1-bit
  - <name>_64x64.png         square face-cropped 1-bit icon
  - <name>_128x64.xbm        XBM byte array for canvas_draw_xbm / mJS gui
  - <name>_preview_4x.png    4x nearest-neighbor preview of the largest size

Style:
  --style gbcam   (default) Game Boy Camera-ish: S-curve contrast, post-resize
                  unsharp, FIND_EDGES overlay forced to solid black, Bayer 4x4
                  ordered dither. Faces stay recognizable at 128x64 because
                  eyes/mouth/jaw appear as inky black lines on top of a regular
                  screen-door tonal pattern, instead of being lost in FS noise.
  --style fs      Floyd-Steinberg: gamma-lift midtones then dither. Better for
                  photorealistic non-portrait artwork where you want continuous
                  tone rather than chunky GB-Camera-style features.

Custom sizes / modes:
  --size WxH[:mode]   add an output size. mode is "fit" (letterbox, default)
                      or "face" (square crop biased toward faces).
                      Repeat to add multiple. Replaces the defaults.
  --xbm-of WxH        emit an XBM for that size (default: 128x64). Repeatable.
  --no-preview        skip the 4x preview PNG.

Tonal knobs (style-aware defaults; explicit flag wins):
  --autocontrast N    autocontrast cutoff percent. gbcam=3, fs=5.
  --contrast N        S-curve strength 0..1 (gbcam only). Higher = chunkier.
                      Default 0.55. Set to 0 for a linear ramp.
  --gamma N           midtone lift (fs only). Default 2.0. Lower = darker face.
  --edge-threshold N  0..255, gbcam only. Edges with magnitude >= threshold are
                      forced black on top of the tonal pass. Default 64.
                      Set to 0 to disable the edge overlay entirely.
  --exposure N        brightness multiplier applied before contrast/gamma.
                      Default 1.0.

Source files are passed as positional args. Each may be either a bare path
or `name=path` to override the output stem.

Examples:
  python make_flipper_images.py out/ photo.png
  python make_flipper_images.py out/ kid1=p1.png kid2=p2.png
  python make_flipper_images.py out/ --size 10x10:face --size 128x64:fit logo=logo.png
  python make_flipper_images.py out/ --style fs photo.png   # legacy FS look
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageOps, ImageFilter, ImageChops, ImageEnhance

DEFAULT_SIZES: list[tuple[int, int, str]] = [
    (128, 64, "fit"),
    (64, 64, "face"),
]

# 4x4 Bayer matrix (values 0..15). Used to build a per-pixel threshold so the
# dither pattern is regular ("screen door") instead of FS-noise-shaped. This is
# the single biggest reason GB Camera images stay readable at low resolution.
BAYER_4X4: tuple[tuple[int, ...], ...] = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


def flatten_on_white(img: Image.Image) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, img).convert("RGB")


def gamma_lut(gamma: float) -> list[int]:
    if gamma == 1.0:
        return list(range(256))
    inv = 1.0 / gamma
    return [min(255, int(round(255 * ((i / 255) ** inv)))) for i in range(256)]


def scurve_lut(strength: float) -> list[int]:
    """Sigmoidal contrast LUT. strength 0 = identity, 1 = very chunky.

    Crushes shadows toward black and highlights toward white while preserving
    a usable midtone ramp — unlike a pure gamma which lifts everything.
    """
    if strength <= 0:
        return list(range(256))
    k = 1.0 + 9.0 * strength  # k=1 ~mild, k=10 ~chunky
    a = 1.0 / (1.0 + math.exp(k * 0.5))
    b = 1.0 / (1.0 + math.exp(-k * 0.5))
    span = b - a or 1e-9
    out: list[int] = []
    for i in range(256):
        x = i / 255.0
        y = (1.0 / (1.0 + math.exp(-k * (x - 0.5))) - a) / span
        out.append(min(255, max(0, int(round(y * 255)))))
    return out


def prep_grayscale(
    rgb: Image.Image,
    *,
    cutoff: int,
    exposure: float,
    gamma: float,
    contrast: float,
) -> Image.Image:
    """Pre-resize tonal pipeline shared by all styles."""
    g = ImageOps.grayscale(rgb)
    g = ImageOps.autocontrast(g, cutoff=cutoff)
    if exposure != 1.0:
        g = ImageEnhance.Brightness(g).enhance(exposure)
    if gamma != 1.0:
        g = g.point(gamma_lut(gamma))
    if contrast > 0:
        g = g.point(scurve_lut(contrast))
    g = g.filter(ImageFilter.UnsharpMask(radius=1.4, percent=200, threshold=1))
    return g


def fit_letterbox_content(g: Image.Image, w: int, h: int) -> tuple[Image.Image, int, int]:
    """Resize preserving aspect; return (content, ox, oy) — caller pastes into
    a w x h white canvas. Splitting paste from resize lets edge detection see
    only the content (no spurious frame edge from the letterbox border)."""
    sw, sh = g.size
    scale = min(w / sw, h / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    content = g.resize((nw, nh), Image.LANCZOS)
    return content, (w - nw) // 2, (h - nh) // 2


def square_face_content(g: Image.Image, w: int, h: int) -> tuple[Image.Image, int, int]:
    """Center crop biased ~5% downward (faces in portraits sit lower-center
    because hair fills the top), then resize to w x h. Always fills canvas."""
    sw, sh = g.size
    s = min(sw, sh)
    x = (sw - s) // 2
    y = max(0, min(sh - s, (sh - s) // 2 + int(s * 0.05)))
    cropped = g.crop((x, y, x + s, y + s))
    return cropped.resize((w, h), Image.LANCZOS), 0, 0


def bayer_dither(g: Image.Image) -> Image.Image:
    """4x4 ordered dither of an L image to PIL "1" mode. Pure Pillow."""
    g = g.convert("L")
    w, h = g.size
    src = g.tobytes()
    # Pre-flatten the threshold matrix to 16 entries in 0..255. We use
    # (n + 0.5) * 16 so the smallest threshold is 8 (any value <8 is always
    # black) and largest is 248 (any value >=248 is always white) — keeps the
    # extremes truly black/white instead of stippled.
    thresh = [int((v + 0.5) * 16) for row in BAYER_4X4 for v in row]
    out = bytearray(w * h)
    for y in range(h):
        ymod = (y & 3) * 4
        row_off = y * w
        for x in range(w):
            t = thresh[ymod + (x & 3)]
            out[row_off + x] = 0 if src[row_off + x] < t else 255
    return Image.frombytes("L", (w, h), bytes(out)).convert("1")


def edge_overlay(g: Image.Image, threshold: int) -> Image.Image:
    """Detect edges in g (L mode) and return an L image where strong edges
    are black (0) and everything else is white (255). Suitable for
    ImageChops.darker() over a tonal dither.

    PIL's FIND_EDGES smears the 1px image border, so we zero those rows/cols
    explicitly to avoid a black frame.
    """
    edges = g.filter(ImageFilter.FIND_EDGES).point(
        lambda v: 0 if v >= threshold else 255
    )
    w, h = edges.size
    if w >= 2 and h >= 2:
        clean = Image.new("L", (w, h), 255)
        inner = edges.crop((1, 1, w - 1, h - 1))
        clean.paste(inner, (1, 1))
        edges = clean
    return edges


def render_gbcam(
    gray: Image.Image,
    w: int,
    h: int,
    mode: str,
    *,
    edge_threshold: int,
) -> Image.Image:
    """GB-Camera-style: post-resize sharpen, edge overlay, Bayer dither."""
    if mode == "fit":
        content, ox, oy = fit_letterbox_content(gray, w, h)
    elif mode == "face":
        content, ox, oy = square_face_content(gray, w, h)
    else:
        raise ValueError(f"unknown mode {mode!r} (use fit or face)")

    # Second sharpen at target resolution. Critical: features that survived the
    # native-res sharpen still get blurred by the LANCZOS downscale. Re-sharpen
    # at the dither resolution so eye/mouth boundaries are crisp before we
    # quantize to 1 bit.
    content = content.filter(
        ImageFilter.UnsharpMask(radius=0.8, percent=300, threshold=1)
    )

    tonal = bayer_dither(content)

    if edge_threshold > 0:
        edges = edge_overlay(content, edge_threshold)
        combined = ImageChops.darker(tonal.convert("L"), edges)
    else:
        combined = tonal.convert("L")

    canvas = Image.new("L", (w, h), 255)
    canvas.paste(combined, (ox, oy))
    return canvas.convert("1")


def render_fs(gray: Image.Image, w: int, h: int, mode: str) -> Image.Image:
    """Legacy Floyd-Steinberg path (no edge overlay, no Bayer)."""
    if mode == "fit":
        content, ox, oy = fit_letterbox_content(gray, w, h)
    elif mode == "face":
        content, ox, oy = square_face_content(gray, w, h)
    else:
        raise ValueError(f"unknown mode {mode!r} (use fit or face)")
    canvas = Image.new("L", (w, h), 255)
    canvas.paste(content, (ox, oy))
    return canvas.convert("1", dither=Image.FLOYDSTEINBERG)


def write_xbm(bw: Image.Image, path: Path, name: str) -> None:
    """Standard XBM (LSB-first per byte). Compatible with canvas_draw_xbm."""
    w, h = bw.size
    px = bw.load()
    row_bytes = (w + 7) // 8
    out: list[int] = []
    for y in range(h):
        for bx in range(row_bytes):
            byte = 0
            for bit in range(8):
                x = bx * 8 + bit
                if x < w and px[x, y] == 0:
                    byte |= 1 << bit
            out.append(byte)
    lines = [
        f"#define {name}_width {w}",
        f"#define {name}_height {h}",
        f"static unsigned char {name}_bits[] = {{",
    ]
    chunks = [
        ", ".join(f"0x{b:02x}" for b in out[i : i + 12])
        for i in range(0, len(out), 12)
    ]
    lines.append("    " + ",\n    ".join(chunks))
    lines.append("};")
    path.write_text("\n".join(lines) + "\n")


def parse_size(spec: str) -> tuple[int, int, str]:
    parts = spec.split(":", 1)
    dims = parts[0].lower().split("x")
    if len(dims) != 2:
        raise argparse.ArgumentTypeError(f"bad size {spec!r}; want WxH[:mode]")
    return (int(dims[0]), int(dims[1]), parts[1] if len(parts) > 1 else "fit")


def parse_xbm_of(spec: str) -> tuple[int, int]:
    dims = spec.lower().split("x")
    if len(dims) != 2:
        raise argparse.ArgumentTypeError(f"bad xbm size {spec!r}; want WxH")
    return (int(dims[0]), int(dims[1]))


def safe_stem(s: str) -> str:
    s = "".join(c if c.isalnum() else "_" for c in s).strip("_")
    return s or "img"


def process(
    src_path: Path,
    out_dir: Path,
    *,
    sizes: list[tuple[int, int, str]],
    xbm_sizes: list[tuple[int, int]],
    style: str,
    cutoff: int,
    exposure: float,
    gamma: float,
    contrast: float,
    edge_threshold: int,
    preview: bool,
    name: str | None,
) -> list[Path]:
    if name is None:
        name = src_path.stem.split("-")[0]
    name = safe_stem(name.lower())

    rgb = flatten_on_white(Image.open(src_path))
    gray = prep_grayscale(
        rgb, cutoff=cutoff, exposure=exposure, gamma=gamma, contrast=contrast
    )

    rendered: dict[tuple[int, int], Image.Image] = {}
    written: list[Path] = []
    for w, h, mode in sizes:
        if style == "gbcam":
            bw = render_gbcam(gray, w, h, mode, edge_threshold=edge_threshold)
        else:
            bw = render_fs(gray, w, h, mode)
        rendered[(w, h)] = bw
        p = out_dir / f"{name}_{w}x{h}.png"
        bw.save(p, optimize=True, bits=1)
        written.append(p)

    for w, h in xbm_sizes:
        bw = rendered.get((w, h))
        if bw is None:
            if style == "gbcam":
                bw = render_gbcam(gray, w, h, "fit", edge_threshold=edge_threshold)
            else:
                bw = render_fs(gray, w, h, "fit")
            rendered[(w, h)] = bw
        p = out_dir / f"{name}_{w}x{h}.xbm"
        write_xbm(bw, p, f"{name}_{w}x{h}")
        written.append(p)

    if preview and rendered:
        (pw, ph), pbw = max(rendered.items(), key=lambda kv: kv[0][0] * kv[0][1])
        scale = 4
        prev = pbw.convert("L").resize((pw * scale, ph * scale), Image.NEAREST)
        p = out_dir / f"{name}_preview_{scale}x.png"
        prev.save(p)
        written.append(p)

    return written


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="make_flipper_images",
        description="Convert images to Flipper Zero 1-bit assets.",
    )
    ap.add_argument("out_dir", help="output directory")
    ap.add_argument(
        "sources",
        nargs="+",
        help="input image (PNG/JPG/...) or name=path to set output stem",
    )
    ap.add_argument(
        "--style",
        choices=("gbcam", "fs"),
        default="gbcam",
        help="dither style. gbcam (default): Bayer + edge overlay (faces "
        "readable). fs: Floyd-Steinberg (continuous tone).",
    )
    ap.add_argument(
        "--size",
        action="append",
        type=parse_size,
        metavar="WxH[:mode]",
        help="add an output size; mode is fit or face. Repeat for multiple.",
    )
    ap.add_argument(
        "--xbm-of",
        action="append",
        type=parse_xbm_of,
        metavar="WxH",
        help="emit XBM for this size (default: 128x64 if it exists). Repeatable.",
    )
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--autocontrast", type=int, default=None, dest="cutoff")
    ap.add_argument("--exposure", type=float, default=1.0)
    ap.add_argument("--gamma", type=float, default=None)
    ap.add_argument("--contrast", type=float, default=None,
                    help="S-curve strength 0..1 (gbcam style only).")
    ap.add_argument("--edge-threshold", type=int, default=110,
                    help="0..255 edge magnitude that triggers a black overlay "
                         "pixel (gbcam style only). 0 disables the overlay.")
    args = ap.parse_args(argv[1:])

    if args.style == "gbcam":
        # Empirically tuned on real portrait sources (4-26 kid sweep). The
        # gamma lifts skin to near-white so Bayer paints it as sparse dots,
        # the mild S-curve crushes hair to solid black, and edge_threshold
        # 110 keeps eye/glasses/mouth lines without halftoning the cheeks.
        cutoff = 3 if args.cutoff is None else args.cutoff
        gamma = 2.6 if args.gamma is None else args.gamma
        contrast = 0.15 if args.contrast is None else args.contrast
    else:
        cutoff = 5 if args.cutoff is None else args.cutoff
        gamma = 2.0 if args.gamma is None else args.gamma
        contrast = 0.0 if args.contrast is None else args.contrast

    sizes = args.size if args.size else list(DEFAULT_SIZES)
    if args.xbm_of:
        xbm_sizes = args.xbm_of
    elif any(s[:2] == (128, 64) for s in sizes):
        xbm_sizes = [(128, 64)]
    else:
        biggest = max(sizes, key=lambda s: s[0] * s[1])
        xbm_sizes = [biggest[:2]]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for spec in args.sources:
        if "=" in spec:
            name, src = spec.split("=", 1)
        else:
            name, src = None, spec
        written = process(
            Path(src),
            out_dir,
            sizes=sizes,
            xbm_sizes=xbm_sizes,
            style=args.style,
            cutoff=cutoff,
            exposure=args.exposure,
            gamma=gamma,
            contrast=contrast,
            edge_threshold=args.edge_threshold,
            preview=not args.no_preview,
            name=name,
        )
        print(f"  {Path(src).name}")
        for p in written:
            try:
                rel = p.relative_to(out_dir.parent)
            except ValueError:
                rel = p
            print(f"    -> {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
