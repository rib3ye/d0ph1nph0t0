"""Convert arbitrary images into Flipper Zero (1-bit) assets.

Default outputs per source image (matches what the Flipper image skill ships):
  - <name>_128x64.png        full-screen 1-bit FS-dithered (letterbox fit)
  - <name>_64x64.png         square face-cropped 1-bit icon
  - <name>_128x64.xbm        XBM byte array for canvas_draw_xbm / mJS gui
  - <name>_preview_4x.png    4x nearest-neighbor preview of the 128x64

Custom sizes / modes:
  --size WxH[:mode]   add an output size. mode is "fit" (letterbox, default)
                      or "face" (square crop biased toward faces).
                      Repeat to add multiple. Replaces the defaults.
  --xbm-of WxH        emit an XBM for that size (default: 128x64). Repeatable.
  --no-preview        skip the 4x preview PNG.
  --gamma N           midtone lift (default 2.0). Lower = darker face.
  --autocontrast N    autocontrast cutoff percent (default 5).

Source files are passed as positional args. Each may be either a bare path
or `name=path` to override the output stem.

Examples:
  python make_flipper_images.py out/ photo.png
  python make_flipper_images.py out/ kid1=p1.png kid2=p2.png
  python make_flipper_images.py out/ --size 10x10:face --size 128x64:fit logo=logo.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps, ImageEnhance, ImageFilter

DEFAULT_SIZES: list[tuple[int, int, str]] = [
    (128, 64, "fit"),
    (64, 64, "face"),
]


def flatten_on_white(img: Image.Image) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, img).convert("RGB")


def prep_grayscale(rgb: Image.Image, gamma: float, cutoff: int) -> Image.Image:
    """Grayscale tuned so faces survive 1-bit Floyd-Steinberg dither.

    Faces sit mid-gray and get eaten by the dither while hair is near-black
    and dominates. We autocontrast aggressively, then apply gamma > 1 to lift
    midtones (face) toward white so dither paints them as sparse dots, leaving
    hair as a solid black silhouette. Heavy unsharp keeps eye/mouth edges.
    """
    g = ImageOps.grayscale(rgb)
    g = ImageOps.autocontrast(g, cutoff=cutoff)
    inv = 1.0 / gamma
    lut = [min(255, int(round(255 * ((i / 255) ** inv)))) for i in range(256)]
    g = g.point(lut)
    g = g.filter(ImageFilter.UnsharpMask(radius=1.4, percent=200, threshold=1))
    return g


def fit_letterbox(g: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = g.size
    scale = min(w / sw, h / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = g.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("L", (w, h), 255)
    canvas.paste(resized, ((w - nw) // 2, (h - nh) // 2))
    return canvas


def square_face_crop(g: Image.Image, w: int, h: int) -> Image.Image:
    """Center crop biased ~5% downward (faces in portraits sit lower-center
    because hair fills the top). Resamples to w x h."""
    sw, sh = g.size
    s = min(sw, sh)
    x = (sw - s) // 2
    y = max(0, min(sh - s, (sh - s) // 2 + int(s * 0.05)))
    cropped = g.crop((x, y, x + s, y + s))
    return cropped.resize((w, h), Image.LANCZOS)


def to_1bit(g: Image.Image) -> Image.Image:
    return g.convert("1", dither=Image.FLOYDSTEINBERG)


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


def render(
    gray: Image.Image, w: int, h: int, mode: str
) -> Image.Image:
    if mode == "fit":
        return to_1bit(fit_letterbox(gray, w, h))
    if mode == "face":
        return to_1bit(square_face_crop(gray, w, h))
    raise ValueError(f"unknown mode {mode!r} (use fit or face)")


def parse_size(spec: str) -> tuple[int, int, str]:
    parts = spec.split(":", 1)
    dims = parts[0].lower().split("x")
    if len(dims) != 2:
        raise argparse.ArgumentTypeError(f"bad size {spec!r}; want WxH[:mode]")
    w, h = int(dims[0]), int(dims[1])
    mode = parts[1] if len(parts) > 1 else "fit"
    return (w, h, mode)


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
    sizes: list[tuple[int, int, str]],
    xbm_sizes: list[tuple[int, int]],
    gamma: float,
    cutoff: int,
    preview: bool,
    name: str | None,
) -> list[Path]:
    if name is None:
        name = src_path.stem.split("-")[0]
    name = safe_stem(name.lower())

    rgb = flatten_on_white(Image.open(src_path))
    gray = prep_grayscale(rgb, gamma=gamma, cutoff=cutoff)

    rendered: dict[tuple[int, int], Image.Image] = {}
    written: list[Path] = []
    for w, h, mode in sizes:
        bw = render(gray, w, h, mode)
        rendered[(w, h)] = bw
        p = out_dir / f"{name}_{w}x{h}.png"
        bw.save(p, optimize=True, bits=1)
        written.append(p)

    for w, h in xbm_sizes:
        bw = rendered.get((w, h)) or render(gray, w, h, "fit")
        rendered.setdefault((w, h), bw)
        p = out_dir / f"{name}_{w}x{h}.xbm"
        write_xbm(bw, p, f"{name}_{w}x{h}")
        written.append(p)

    if preview and rendered:
        # preview the largest rendered size
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
    ap.add_argument("--gamma", type=float, default=2.0)
    ap.add_argument("--autocontrast", type=int, default=5, dest="cutoff")
    args = ap.parse_args(argv[1:])

    sizes = args.size if args.size else list(DEFAULT_SIZES)
    if args.xbm_of:
        xbm_sizes = args.xbm_of
    else:
        # default: XBM of 128x64 if present, else the largest
        if any(s[:2] == (128, 64) for s in sizes):
            xbm_sizes = [(128, 64)]
        else:
            biggest = max(sizes, key=lambda s: s[0] * s[1])
            xbm_sizes = [biggest[:2]]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for spec in args.sources:
        name: str | None
        if "=" in spec:
            name, src = spec.split("=", 1)
        else:
            name, src = None, spec
        written = process(
            Path(src),
            out_dir,
            sizes=sizes,
            xbm_sizes=xbm_sizes,
            gamma=args.gamma,
            cutoff=args.cutoff,
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
