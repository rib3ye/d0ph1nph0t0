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

# default: GB-Camera-style, 128x64 letterbox + 64x64 face-crop + XBM + 4x preview
/tmp/flipperimg-venv/bin/python ~/.cursor/skills/flipper-image/scripts/make_flipper_images.py \
    out/ photo.png
```

## Two dither styles

| Style | Dither | Best for | Look |
|---|---|---|---|
| `gbcam` (default) | Bayer 4x4 ordered + edge overlay | Portraits, faces, anything where shape recognition matters | Inky-black features (eyes, mouth, jaw) on a regular "screen door" tonal pattern. Recognizable at 128x64. |
| `fs` | Floyd–Steinberg | Photorealistic non-portrait artwork (landscapes, textures) | Continuous tone, no regular pattern, but small features dissolve into noise. |

Pick with `--style {gbcam,fs}`. Faces *almost always* want `gbcam` — FS turns
midtone skin into random-looking noise and the eye reads it as static.

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

Naive grayscale + Floyd–Steinberg crushes faces into stippled noise because
skin sits mid-gray while hair is near-black; the dither can't separate them
spatially and the eye reads the result as static. The `gbcam` pipeline solves
this with three specific moves:

1. **Composite onto white** — kills RGBA matte fringes from PNG cutouts.
2. **Autocontrast** (default cutoff 3) — map source range to full 0..255.
3. **Optional brightness adjust** (`--exposure`).
4. **Sigmoidal S-curve** (`--contrast`, default 0.15) — crushes shadows toward
   black and highlights toward white *without* lifting midtones into a single
   undifferentiated bucket. Skin and hair stay on different tonal shelves.
5. **Native-resolution unsharp** (radius 1.4, 200%) — protects eye/mouth edges
   through the LANCZOS downscale.
6. **Resize** to target size (letterbox or face-crop).
7. **Post-resize unsharp** (radius 0.8, 300%) — re-sharpens features that the
   downscale blurred. This is GBcam-only.
8. **Edge overlay**: `FIND_EDGES`, threshold (`--edge-threshold`, default 110),
   force black wherever an edge fires. This is the move that makes facial
   features readable — eyes, mouth, jaw line, nostrils all become solid black
   lines on top of the tonal pass.
9. **Bayer 4x4 ordered dither** — produces a regular "screen door" texture
   instead of FS noise; your visual system can group it as shape rather than
   noise.

The `fs` style skips the post-resize sharpen, the edge overlay, and the Bayer
dither — it's a straight gamma-lift + Floyd–Steinberg path for non-portrait
artwork.

### Knobs

| Flag | Default (gbcam / fs) | What it does |
|---|---|---|
| `--style` | `gbcam` | Pick `gbcam` (Bayer + edges) or `fs` (Floyd–Steinberg). |
| `--autocontrast N` | 3 / 5 | Cutoff percent for autocontrast endpoints. |
| `--exposure N` | 1.0 | Linear brightness multiplier before contrast/gamma. |
| `--gamma N` | 2.6 / 2.0 | Midtone lift. In `gbcam` it pushes skin near-white so Bayer paints it as sparse dots; in `fs` it lets FS leave skin mostly-white. |
| `--contrast N` | 0.15 / 0 | S-curve strength (0–1). In `gbcam` a small bump separates hair (black) from skin (white) cleanly. |
| `--edge-threshold N` | 110 | Edge magnitude that triggers a black overlay pixel (gbcam only). 0 disables the overlay. |

The gbcam defaults were tuned on real face sources: gamma 2.6 lifts skin into
the near-white range where Bayer 4x4 leaves it visually clean, and edge
threshold 110 picks up eyes/glasses/mouth/jaw edges without halftoning the
cheeks.

Tuning hints (gbcam):

- Skin shows a regular halftone pattern instead of clean white? → raise
  `--gamma 2.8` or `--exposure 1.1`.
- Face too washed (eyes/mouth disappearing)? → drop `--gamma 2.4` and lower
  `--edge-threshold 90` to pick up softer edges.
- Edges look like a posterized outline drawing? → raise `--edge-threshold 130`.
- Hair flattens to a solid black slab and you want detail back? → drop
  `--contrast 0` and consider `--edge-threshold 80`.

Tuning hints (fs):

- Face too dark → `--gamma 2.4`.
- White shirt blowing out → `--gamma 1.6 --autocontrast 2`.

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
                       [--style {gbcam,fs}]       dither style, default gbcam
                       [--size WxH[:mode]]...     replaces defaults
                       [--xbm-of WxH]...          which size(s) get XBM
                       [--no-preview]             skip 4x preview PNG
                       [--autocontrast N]         cutoff %, default 3 (gbcam)/5 (fs)
                       [--exposure N]             brightness mult, default 1.0
                       [--gamma N]                midtone lift, default 2.6 (gbcam)/2.0 (fs)
                       [--contrast N]             S-curve 0..1, default 0.15 (gbcam)/0 (fs)
                       [--edge-threshold N]       0..255, default 110 (gbcam)

SOURCE := <path> | <name>=<path>
mode   := fit (letterbox) | face (square crop)
```

Defaults if no `--size` is given: `128x64:fit` and `64x64:face`, XBM of
`128x64`, 4x preview enabled.

## Anti-patterns

- **Don't** save grayscale PNGs and expect ufbt to produce a clean `.bm`. ufbt
  wants 1-bit input; this script writes `bits=1` on save.
- **Don't** use `--style fs` for portraits — Floyd–Steinberg dissolves
  midtone skin into noise that reads as static. Use `gbcam` for any image with
  a face in it.
- **Don't** skip the white-flatten step on RGBA inputs; alpha mattes leak as
  gray fringes that dither into noisy halos around the subject.
- **Don't** reach for `pip install --break-system-packages` on macOS — the
  quick-start uses a throwaway venv at `/tmp/flipperimg-venv` to avoid PEP 668
  pain.

## Dependencies

- Python 3.10+
- Pillow (any recent version; tested on 12.x)
