# d0ph1nph0t0

> A Cursor [Agent Skill](https://cursor.com/docs/agent/skills) that turns any
> picture into Flipper Zero–compatible 1-bit assets (128×64 ST7567S, FAP icons,
> XBM, dolphin animation frames).

## What it does

Drop in a photo, logo, or screenshot and out comes:

- **`<name>_128x64.png`** — full-screen 1-bit Floyd-Steinberg dithered, ready
  for a FAP `images/` folder (ufbt compiles to `.bm`)
- **`<name>_64x64.png`** — square face-cropped 1-bit icon (menu thumbs,
  animation frames)
- **`<name>_<W>x<H>.xbm`** — XBM byte array for `canvas_draw_xbm` in C or
  the `gui` xbm widget in mJS
- **`<name>_preview_4x.png`** — 4× nearest-neighbor preview so you can verify
  the dither without flashing the device

Custom sizes (10×10 Apps Catalog icons, 14×14 menu icons, full-screen art,
animation frames) are first-class via `--size WxH[:mode]`.

## Why a tonal pipeline matters

Naive grayscale + Floyd-Steinberg crushes faces because skin sits mid-gray
while hair is near-black — the dither can't separate them and you get a
stippled blob. This skill ships a tuned pipeline (autocontrast → gamma 2.0
midtone lift → unsharp → FS dither) that paints faces mostly-white with sparse
dots while leaving hair as a clean black silhouette. Eyes, glasses, and
smiles survive at 64×64.

## Install

This is a Cursor Agent Skill, so it lives at a known path on disk:

```bash
git clone https://github.com/rib3ye/d0ph1nph0t0.git ~/.cursor/skills/flipper-image
```

Cursor will pick it up automatically next session. The agent discovers it
whenever you ask anything that smells like Flipper image work — "make this
Flipper-compatible", "1-bit dither for the LCD", "FAP icon", "convert for my
Flipper", etc.

### Dependencies

- Python 3.10+
- [Pillow](https://pillow.readthedocs.io/) (any recent version)

A throwaway venv keeps macOS PEP 668 happy:

```bash
python3 -m venv /tmp/flipperimg-venv
/tmp/flipperimg-venv/bin/pip install --quiet Pillow
```

## Quick start

```bash
# defaults: 128x64 letterbox + 64x64 face-crop + XBM + 4x preview
/tmp/flipperimg-venv/bin/python ~/.cursor/skills/flipper-image/scripts/make_flipper_images.py \
    out/ photo.png

# Apps Catalog icon set + full-screen splash
/tmp/flipperimg-venv/bin/python ~/.cursor/skills/flipper-image/scripts/make_flipper_images.py \
    out/ \
    --size 10x10:face --size 14x14:face --size 128x64:fit \
    --xbm-of 14x14 \
    logo=logo.png

# multiple sources with explicit stems
/tmp/flipperimg-venv/bin/python ~/.cursor/skills/flipper-image/scripts/make_flipper_images.py \
    out/ kid1=p1.png kid2=p2.png
```

## CLI

```
make_flipper_images.py OUT_DIR SOURCE [SOURCE ...]
                       [--size WxH[:mode]]...   replaces defaults; repeatable
                       [--xbm-of WxH]...        which size(s) get XBM
                       [--no-preview]           skip the 4x preview PNG
                       [--gamma N]              midtone lift (default 2.0)
                       [--autocontrast N]       cutoff % (default 5)

SOURCE := <path> | <name>=<path>
mode   := fit (letterbox, default) | face (square crop, biased for portraits)
```

If the face still looks too dark, push `--gamma 2.4`. If highlights blow out,
drop to `--gamma 1.6 --autocontrast 2`.

## Common Flipper sizes

| Size | Use | Mode |
|---|---|---|
| 128×64 | full-screen art | `fit` |
| 64×64 | menu thumbnail / animation frame | `face` |
| 14×14 | submenu icon, settings row | `face` |
| 10×10 | `fap_icon` for the Apps Catalog | `face` |

## Using outputs in a FAP

```python
# application.fam
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

ufbt generates `<appid>_icons.h` with `I_<filename>` symbols:

```c
#include <gui/canvas.h>
#include <myapp_icons.h>

static void draw_callback(Canvas* canvas, void* ctx) {
    canvas_clear(canvas);
    canvas_draw_icon(canvas, 0, 0, &I_kid1_128x64);
}
```

Or skip the asset pipeline entirely with the XBM:

```c
#include "kid1_128x64.xbm"
canvas_draw_xbm(canvas, 0, 0,
    kid1_128x64_width, kid1_128x64_height, kid1_128x64_bits);
```

## Repo layout

```
.
├── SKILL.md                 # the skill itself; Cursor reads frontmatter
├── README.md                # you are here
├── LICENSE
└── scripts/
    └── make_flipper_images.py
```

## License

MIT — see [LICENSE](LICENSE).
