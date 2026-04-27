---
name: flipper-image
description: >-
  Convert arbitrary photos and graphics into Flipper Zero (128x64, 1-bit
  monochrome) assets ready for FAP `images/` folders, dolphin animation frames,
  Apps Catalog icons, or direct C/mJS embedding via XBM. Produces 1-bit
  Floyd-Steinberg dithered PNGs in any requested size, XBM byte arrays for
  `canvas_draw_xbm`, and 4x nearest-neighbor preview PNGs so the dither can be
  reviewed without flashing the device. Use whenever the user wants to make a
  picture "Flipper-compatible", build a FAP icon (10x10 / 14x14 / custom),
  prepare full-screen artwork for an ST7567S LCD, generate dolphin animation
  frames, convert a logo to .bm/.bmx (via ufbt) or .xbm, dither an image to
  1-bit for the Flipper, or any time a source PNG/JPG needs to land cleanly on
  the Flipper Zero display.
---

# Flipper Image Converter

Turn any picture into Flipper Zero–compatible 1-bit assets. The Flipper LCD is
**128x64 monochrome ST7567S**, so anything destined for the screen has to land
in 1-bit at the target size with intentional dithering.

## When to use

Trigger this skill when the user wants to:

- Convert a photo / logo / screenshot to a Flipper-compatible image
- Generate a FAP icon (`fap_icon` in `application.fam`, typically 10x10)
- Build menu / settings / submenu icons (commonly 14x14)
- Prepare full-screen art for `canvas_draw_icon`
- Make dolphin animation frames
- Embed a bitmap directly in C (`canvas_draw_xbm`) or mJS (`gui` xbm widget)
- Dither anything to 1-bit for the ST7567S

## Quick start

The script lives at `scripts/make_flipper_images.py`. It needs Pillow.

```bash
# one-time setup (script-local venv keeps the user's system Python clean)
python3 -m venv /tmp/flipperimg-venv
/tmp/flipperimg-venv/bin/pip install --quiet Pillow

# default: 128x64 letterbox + 64x64 face-crop + XBM + 4x preview
/tmp/flipperimg-venv/bin/python ~/.cursor/skills/flipper-image/scripts/make_flipper_images.py \
    out/ photo.png
```

To override the output stem (multiple sources, want clean names):

```bash
... make_flipper_images.py out/ kid1=p1.png kid2=p2.png logo=logo.png
```

## Output files

For each source `name`:

| File | What | Where to use |
|---|---|---|
| `<name>_128x64.png` | full-screen 1-bit, letterboxed | drop in FAP `images/`, ufbt compiles to `.bm` |
| `<name>_64x64.png` | square face-cropped 1-bit | menu thumbnails, animation frames |
| `<name>_<W>x<H>.xbm` | XBM byte array | `canvas_draw_xbm` in C, `gui/xbm` in mJS |
| `<name>_preview_4x.png` | 4x nearest-neighbor scale | human review of dither pattern |

## Custom sizes

Replace defaults with `--size WxH[:mode]`. Modes:

- `fit` (default) — letterbox; preserves aspect, pads with white
- `face` — square center crop biased ~5% downward (faces sit lower-center
  because hair fills the top of portraits)

Common Flipper sizes worth knowing:

| Size | Use | Mode |
|---|---|---|
| 128x64 | full-screen | `fit` |
| 64x64 | menu thumbnail / animation | `face` |
| 14x14 | submenu icon, settings row | `face` |
| 10x10 | `fap_icon` for Apps Catalog | `face` |

```bash
... make_flipper_images.py out/ \
    --size 10x10:face --size 14x14:face --size 128x64:fit \
    --xbm-of 14x14 \
    logo=logo.png
```

`--xbm-of WxH` is repeatable. If omitted, an XBM is produced for `128x64` when
that size is present, otherwise for the largest size.

## Tonal pipeline (and why)

Naive grayscale + Floyd-Steinberg crushes faces into dark stippled blobs because
skin sits mid-gray while hair is near-black, so the dither can't separate them.
The script does:

1. Composite onto white (kills RGBA matte fringes from PNG cutouts)
2. `autocontrast(cutoff=5)` to map source range to full 0..255
3. Gamma 2.0 LUT to lift midtones — skin ends up near-white, hair stays black
4. Heavy unsharp (radius 1.4, 200%) so eyes/mouth survive downsampling
5. Floyd-Steinberg dither to 1-bit

If a face still looks too dark, push `--gamma 2.4`. If highlights blow out
(white shirt loses detail), drop to `--gamma 1.6` and `--autocontrast 2`.

## Using the outputs in a FAP

Drop PNGs into `images/` next to `application.fam`:

```python
App(
    appid="myapp",
    name="My App",
    apptype=FlipperAppType.EXTERNAL,
    entry_point="myapp_app",
    fap_icon="images/myapp_10x10.png",
    fap_icon_assets="images",
    stack_size=2 * 1024,
)
```

ufbt generates `<appid>_icons.h` with `I_<filename_without_ext>` symbols:

```c
#include <gui/canvas.h>
#include <myapp_icons.h>

static void draw_callback(Canvas* canvas, void* ctx) {
    canvas_clear(canvas);
    canvas_draw_icon(canvas, 0, 0, &I_kid1_128x64);
}
```

For direct XBM embedding (no asset pipeline):

```c
#include "kid1_128x64.xbm"
canvas_draw_xbm(canvas, 0, 0, kid1_128x64_width, kid1_128x64_height, kid1_128x64_bits);
```

## Workflow

```
- [ ] Identify target sizes (full-screen? icon? both?)
- [ ] Identify naming (one source -> one stem; multiple -> use name=path)
- [ ] Run make_flipper_images.py
- [ ] Open the *_preview_4x.png to verify face/feature readability
- [ ] If unreadable: rerun with adjusted --gamma / --autocontrast
- [ ] Drop *.png into the FAP's images/ folder (or wire up xbm)
```

## Script reference

```
make_flipper_images.py OUT_DIR SOURCE [SOURCE ...]
                       [--size WxH[:mode]]...     replaces defaults
                       [--xbm-of WxH]...          which size(s) get XBM
                       [--no-preview]             skip 4x preview PNG
                       [--gamma N]                midtone lift, default 2.0
                       [--autocontrast N]         cutoff %, default 5

SOURCE := <path> | <name>=<path>
mode   := fit (letterbox) | face (square crop)
```

Defaults if no `--size` is given: `128x64:fit` and `64x64:face`, XBM of
`128x64`, 4x preview enabled.

## Anti-patterns

- **Don't** save grayscale PNGs and expect ufbt to produce a clean `.bm`. ufbt
  wants 1-bit input; this script writes `bits=1` on save.
- **Don't** use ordered/Bayer dither for portraits — Floyd-Steinberg preserves
  facial features dramatically better at 128x64.
- **Don't** skip the white-flatten step on RGBA inputs; alpha mattes leak as
  gray fringes that dither into noisy halos around the subject.
- **Don't** reach for `pip install --break-system-packages` on macOS — the
  quick-start uses a throwaway venv at `/tmp/flipperimg-venv` to avoid PEP 668
  pain.

## Dependencies

- Python 3.10+
- Pillow (any recent version; tested on 12.x)
