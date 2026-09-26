---
name: ad-image-localization
description: Localize ad/marketing image creatives into other languages and every delivery size (1:1, 16:9, 4:5, 9:16, 1200x628 and IAB banners down to 320x50) without an image-generation model, using the coding agent itself. The agent decomposes the flat source into layers (clean plate via LaMa/push-pull, subject cutouts via rembg/keying), builds a native-resolution background per size (texture quilting, sky modelling, or procedural code drawing), rebuilds the creative as a responsive HTML page with live text, then renders each language x size with headless Chrome. Handles RTL (Arabic, Hebrew, Persian, Urdu), CJK phrase breaking, brand-term memory, DOM + visual QA, culture-aware flagging, manifests and a release gate. Use when asked to translate text inside an ad image, resize a creative into ad sizes, or produce a multi-language/multi-size ad delivery folder.
---

# Ad Image Localization (code-reconstruction edition)

## Why this works without image generation

The GPT-Image edition ([ad-image-localization-codex](https://github.com/kouzt123/ad-image-localization-codex)) asks an image model to repaint the creative for every language and ratio. This edition needs no image model: the coding agent **reconstructs** the ad with code instead. It works in any agent that can run shell commands, read and write files, and view images: Claude Code, Codex, Cursor, Gemini CLI, and others.

```
source.png ──recognize──▶ scene.json ──decompose.py──▶ plate.png + cutouts/*.png + layers.json
                                              └─extend──▶ plates/plate_<WxH>.png (native per size)
                                                           │
     copy/<lang>.json + background.js (scene-kit) ──▶ creative.html (responsive, live text) ──render.py──▶ final/*.png
```

- Pixels that must look native (character, product, photo, logo mark, background) come **from the source itself**, so they match it exactly.
- Everything that changes per language or size (headline, CTA, UI labels, layout) is **HTML/CSS**. Text is set in real fonts, so it's sharp and correctly shaped: no garbled glyphs, and Arabic joins properly. It can go RTL and be refitted for any size.
- Every size is a first-class render. `320x50` and `160x600` get their own layout, not a crop.
- **The background is also rebuilt natively for every size.** No stretching and no murky outpainting. Ground texture is quilted from untouched source pixels, sky is continued with a gradient model, and light, haze, particles or an entire flat background are drawn in code (`scene-kit.js`). See **Background strategy**.

Know the limits and tell the user up front. See **Limitations**.

## Tooling

All scripts live in `scripts/` next to this file. Run them with the skill's venv:

```bash
SK=<directory containing this SKILL.md>             # e.g. ~/.claude/skills/ad-image-localization
PY=$SK/.venv/bin/python                              # created by: bash $SK/scripts/setup.sh
```

If `$PY` does not exist, run `bash $SK/scripts/setup.sh` first. It needs `uv` or python3.10+. It installs Pillow, numpy, OpenCV, rembg, simple-lama-inpainting (torch) and Playwright, and uses the local Google Chrome. The first decompose downloads the models (~180 MB rembg + ~200 MB LaMa).

| Script | Purpose |
|---|---|
| `decompose.py grid / crop / rotate` | Measure boxes: labelled coordinate grid, zoomed crops, and a deskewed copy for tilted UI |
| `decompose.py decompose scene.json` | Cutouts + clean plate + `layers.json` + `layers_preview.jpg` |
| `decompose.py extend layers/ --plan …` | **Exact-size plate per delivery size**: taller sizes continue the sky (C1-continuous row-profile model), wider sizes quilt the ground band from clean source columns, extreme strips cover-crop |
| `decompose.py outpaint` | Legacy LaMa canvas extension. Murky; prefer `extend` |
| `render.py` | Renders HTML to PNG/JPG per language x size and collects the runtime DOM QA |
| `ad_image_localization_tools.py` | preflight, manifest, verify, contact-sheet, qa-init, release-check, flag-culture-aware, memory-add/list, cover-crop, normalize-size |
| `templates/ad-runtime.js` | Page runtime: copy injection, RTL, layout classes, auto-fit text, DOM QA (overflow, keep-outs, overlaps, **WCAG contrast vs the actual background**) |
| `templates/scene-kit.js` | Procedural canvas painter: plate `cover`, `veil`/`haloBehind` (legibility haze fitted to the copy box), `sky`, `glow`, `cloud`, `ridge`, `trees`, `grass`, `flowers`, `bokeh`, `sparkle`, `haze`, `grain`. Seeded, so identical across languages |

## Workflow

### 1. Set up the run folder and preflight

```
ad-localization-runs/<creative-slug>_<yyyymmdd_hhmm>/
├── source/            original, untouched
├── scene.json         your layer spec (step 3)
├── creative.html      your reconstruction (step 5)
├── ad-runtime.js      copied from templates/
├── scene-kit.js       copied from templates/
├── background.js      your background painter (step 5)
├── copy/<lang>.json   one file per language (step 4)
├── job.json
├── final/             upload-ready files only
├── qa/                preflight, render_qa, manifest, contact sheets, visual_review
├── work/layers/       plate, cutouts, layers.json, previews, plates/plate_<WxH>.png
└── Flagged by Culture-Aware QA/
```

Write `job.json` using the `references/job.schema.json` shape. Profiles are in `references/delivery_profiles.json`: `standard-delivery`, `social-core`, `google-ads-image-assets`, `iab-display`, `full-pack`. Or list `formats` explicitly. Then run:

```bash
$PY $SK/scripts/ad_image_localization_tools.py preflight job.json --output qa/preflight.json
```

Show the user the deliverable count, the languages, and any conflicts before you start the heavy work.

### 2. Recognize the source

Look at the image (Read it). Then run `decompose.py grid source/x.png work/grid.png --step 50` and Read the grid. Use `crop --scale 2` on dense areas and `--step 20` grids for precision. Record:

- **Text blocks.** Content, role (brand / headline / sub / cta / legal / UI), colour, weight, approximate font size, alignment, and the words that carry accent colour.
- **Movable subjects.** Character, product, device, stickers, logo mark. Note their z-order and what overlaps what.
- **Background.** Is it simple (sky, gradient, studio, bokeh) or complex (a photographic scene the subjects sit in)?
- **Tilted surfaces** such as phones, packaging or signs. Estimate the angle, then use `rotate --degrees N` to get a deskewed copy you can measure and crop.
- **Font match.** Pick the closest Google Font per script. The page loads it from Google Fonts: rounded geometric → Nunito / M PLUS Rounded 1c / Baloo Bhaijaan 2; neutral UI → Inter / Noto Sans JP / Noto Sans Arabic.

Decide per element whether it will be **raster** (cut from the source) or **rebuilt** (HTML/CSS):
- Brand wordmark: rebuild if it's plain type in a matchable font. Otherwise keep it raster (a logo lockup).
- UI mockups with text (phones, dashboards, chat bubbles): **rebuild in HTML** from cropped photos and avatars. This is what makes their text translatable and RTL-able.
- Buttons, badges and pills: always rebuild in CSS.
- Text baked into complex imagery (text on a 3D can, a shirt print): keep it raster and use `text_slots` (the slot is inpainted, then re-set in HTML over the component with `rotate`), or tell the user it stays untranslated.

### 3. Write `scene.json` and decompose

```jsonc
{
  "source": "source/x.png",
  "plate_work_side": 384,       // LaMa resolution for the plate; lower = more coherent big fills
  "plate_soften": 1.2,          // uniform blur that hides fill seams (shallow depth of field)
  "mask_dilate": 8,
  "components": [
    {"id": "hero", "box": [x0,y0,x1,y1], "cutout": "rembg"},            // organic subjects
    {"id": "logo_mark", "box": [...], "cutout": "rounded:34"},          // app icons, cards
    {"id": "sticker", "box": [...], "cutout": "bgkey"},                 // objects on flat sky/colour
    {"id": "photo", "image": "work/source_deskew.png", "box": [...], "cutout": "rect"},  // assets from deskewed copy (not removed from plate)
    {"id": "can", "box": [...], "cutout": "rembg",
     "text_slots": [{"id": "can_label", "box": [...], "rotate": -4, "text": "..."}]}
  ],
  "texts": [
    {"id": "headline", "box": [...], "mask": "ink", "dilate": 8, "fill": "smooth"},  // "ink" on smooth bg, "box" for buttons/busy bg;
                                                                                     // fill "smooth" = push-pull (seamless on sky/gradients), default LaMa
    {"id": "cta", "box": [...], "mask": "box"}
  ],
  "plate_extra_masks": [[[x,y],[x,y],...]]   // polygons/rects to also erase (e.g. a device you will rebuild)
}
```

Cutout methods: `rembg` (holes are filled and the largest blob kept by default), `bgkey` (keys out a uniform surround; `key_low`/`key_high`), `rect`, `rounded:R`, `ellipse`, `polygon:[[x,y],...]`, and `subtract: [[x0,y0,x1,y1]]`.

```bash
$PY $SK/scripts/decompose.py decompose scene.json
```

**Read `work/layers/layers_preview.jpg` and the plate.** Iterate until the layers are clean:
- If a cutout is missing parts (for example a pink inner ear), `fill_holes` is on by default. Try a different model (`"rembg_model": "u2net"`) or switch method.
- If a sticker cutout grabbed its background, rembg is failing on the flat colour. Use `bgkey`. If `bgkey` is grabbing a busy background, use `rembg`.
- If a device's rembg mask comes out as only its frame, erase the device with a polygon in `plate_extra_masks` and rebuild it.
- If there are leftovers on the plate (edges, shadows), widen the polygons or dilation.
- If the plate is smeary, lower `plate_work_side` (384 → 320) and add `plate_soften`.
- If text over sky or gradients leaves ghost rectangles, use `"fill": "smooth"` for those texts.
- Soft panels or glows that survive text removal are often **part of the design** (a legibility haze behind the copy). Don't fight them. Reproduce them per layout with `S.veil` in step 5.

### 3b. Build native plates for every size (Background strategy)

Pick the background strategy from what the source background *is*. Tell the user which one you chose.

| Source background | Strategy |
|---|---|
| Flat colour, gradient, vector shapes, abstract/bokeh, simple studio sweep | **Redraw it entirely in code** with `scene-kit.js` (sample the colours from the source). It's pixel-perfect at every size, and code beats any plate here. |
| Painterly or photographic *texture* ground (meadow, sand, water, foliage, city blur) under a sky or gradient | **`extend`**: real texture is quilted sideways, the sky continued upward, and code-drawn light (veil, glow, sparkles) goes on top. |
| Structured scene (room, street, shelf) where geometry must stay coherent | Plate + cover-crop per size. Lay the composition out so the subjects cover the filled areas. Tell the user and ask for a layered PSD/Figma file or a clean background if quality matters. |

Don't fully code-draw a background under a painterly or 3D-rendered subject. It reads as vector clip-art next to the real render. Code drawing is for flat/vector sources and for the *light* layers of any source.

```bash
$PY $SK/scripts/decompose.py extend work/layers --plan qa/preflight.json --options \
  '{"clean_x":[[0,360],[1135,1200]], "horizon":0.58, "patch":0.1, "zenith":[70,175,250], "strip_focus":0.2}'
```

- `clean_x`: plate column ranges of **untouched original** texture (not inpainted). Quilting samples only these.
- `horizon`: where textured ground begins, as a fraction of plate height. Put it slightly *above* the tallest ridge, so the band's top rows are sky.
- `zenith`: the colour the sky tends toward when a tall format extends upward. Keep it light enough for the copy that will sit there (the contrast QA will tell you).
- Read `plates/plate_<WxH>.png` for the widest and tallest sizes. Look for repeated flowers or objects (lower `patch` or widen `clean_x`), seams, and banding.

### 4. Write the copy files

Write one `copy/<lang>.json` per language, and keep the keys identical across languages. Include the source language too, as the baseline render.

- Copy strings may contain `<em>` (accent words), `<strong>`, `<br>` and `<span class="nowrap">`. Put the accent on whichever word carries the meaning in *that* language. Word order changes, so don't mirror the source positions.
- Apply terminology memory first. Precedence: bundled `brand_term_memory.json` < workspace `.ad-image-localization/brand_term_memory.json` < job `protected_terms` < the user's explicit instruction. Protected terms stay verbatim, including inside RTL copy.
- Write ad copy, not literal translation. Keep it short, because strips like 320x50 must still fit. Localize UI strings the way the local platforms phrase them ("Gefällt mir", "いいね！", "أعجبني"). Transliterate names in UI mockups when the script differs (ja katakana, ar Arabic script) and use native numerals where local apps do (Arabic-Indic in GCC).
- Only save rules to memory when the user approves them (`memory-add`).

### 5. Build `creative.html`

Start from `templates/creative.template.html`. For a complete reference, see `examples/rabbit-social-networks/creative.html`. Copy `templates/ad-runtime.js` beside it. Rules:

- The canvas is the viewport. Size everything in `vw`/`vh`/`vmin`, never px, so one file serves every size. Use `cqw` inside rebuilt components such as phones (`container-type: inline-size`).
- Keep the **source composition as the square/"native" layout**. Measure positions from the grid so the ratio closest to the source reproduces it faithfully. Group subjects that overlap in the source into a `.hero` box with %-positioned children, so they move as one.
- Write rules for every `html[data-layout]` the job needs: `strip` (≥5:1), `billboard` (≥2.3), `landscape` (≥1.4), `square` (≥0.9), `portrait` (≥0.7), `story` (≥0.4), `skyscraper` (<0.4). Refine with `data-tier` (xs/sm/lg by short side) and `data-wb`/`data-hb` (s/m/l width/height buckets).
- Small formats drop secondary layers (the device, extra stickers, the wordmark) rather than shrinking them to illegibility. Use `.hero { position: static }` to place a single subject directly on the canvas.
- Use `inset-inline-start/end` and `text-align: start` everywhere, so `dir="rtl"` mirrors the layout automatically. Add `[dir="rtl"]` overrides for rotations and sticker positions. You may mirror a text-free plate with `scaleX(-1)`. **Never** mirror logos, wordmarks, faces or products.
- Font stacks go per script with `:lang(ja)`, `:lang(ar)` etc. For CJK add `word-break: auto-phrase; line-break: strict`.
- **Background.** Paint it on `<canvas class="plate" id="scene">` from a `background.js` that uses `SceneKit.register(async (S, c) => …)`. Load `plates/plate_${W}x${H}.png` (falling back to `plate.png`), `S.cover()` it (use `flip: c.dir === "rtl"` only for text-free plates), then add the code-drawn light: `S.veil(ctx, S.rectOf(".brand", ".headline"), {alpha})` per layout for legibility, plus sparkles, glows and so on. Painting on the canvas means the contrast QA measures exactly what ships. Script order: `scene-kit.js` → `background.js` → `ad-runtime.js`.
- **`cqw` pitfall.** `cqw` resolves against an *ancestor* container. Put `container-type: inline-size` on a wrapper (for example `.phone-wrap`), not only on the element whose own border or radius uses `cqw`. Otherwise those fall back to viewport units, and bezels turn huge at large sizes.
- Runtime attributes: `data-copy="key"`, `data-fit` / `data-fit="line"` (the CSS font-size is the max; `data-fit-min` sets the floor), `data-qa="keepout"` (faces, products, stickers; alpha-aware on `<img>`), `data-qa-solid` (buttons), `data-qa-decor` (decorative UI text QA should skip), `data-qa-group` (text that's allowed to touch).

Preview single renders while you iterate:

```bash
$PY $SK/scripts/render.py creative.html --preview 1200x628 --lang ar --copy-dir copy --out work/preview_ar.png
$PY $SK/scripts/render.py creative.html --langs en --sizes 1200x1200,1200x628,320x50 --copy-dir copy --out work/preview --slug <slug>
```

Iterate on the **baseline language first** until the source-ratio render is close to the source. Then check the longest language (usually de, fi, ru), CJK and RTL.

### 6. Render the delivery

```bash
$PY $SK/scripts/render.py creative.html --plan qa/preflight.json --copy-dir copy --qa-out qa/render_qa.json
```

Every `error` in the output must be fixed: text outside the canvas, overflow after fit, missing copy key, the runtime not being ready, or a page error in the painter. Treat `warn`s as real unless you can see they're harmless:
- keep-out collisions and overlaps
- type below the floor
- story safe zone
- fonts that failed to load
- `low_contrast`: the 10th-percentile WCAG ratio of each text run against the canvas pixels behind it, with a default floor of 3:1. Accent-coloured words over sky are the usual offender. Fix it with the layout's `veil` alpha, a lighter `zenith`, or by moving the copy.

Fix the CSS or background and re-render.

### 7. QA

```bash
T=$SK/scripts/ad_image_localization_tools.py
$PY $T manifest final --plan qa/preflight.json --output qa/manifest.json
$PY $T verify final
# one contact sheet per language keeps thumbnails readable:
for l in <langs>; do mkdir -p work/sheets/$l && cp final/*_${l}_* work/sheets/$l/ && $PY $T contact-sheet work/sheets/$l qa/qa_contact_sheet_$l.jpg --cols 6; done
$PY $T qa-init final --plan qa/preflight.json --output qa/visual_review.json
```

**Actually look** at every contact sheet, and at any size where something is doubtful at full resolution. DOM QA cannot see:
- Bad line breaks: a word split in two, CJK phrases split, orphans, or a German compound hyphenated badly.
- Accent colour on the wrong word, or wrong meaning or tone, or a mistranslated UI label.
- Awkward crops: a face cut at the eyes, a product cut off, a sticker half off-canvas.
- Plate artifacts that end up exposed in some ratio, such as smears or ghost edges. Fix these by moving layers over them, changing `--plate-pos`, or re-decomposing.
- Tofu or fallback fonts, and Arabic letters that aren't joined.
- Balance: dead empty areas, or everything crammed to one side.

**RTL is a hard gate.** The primary copy must be right-aligned, the reading path must start at the right, and the CTA must follow the RTL flow. Mixed Arabic and Latin must have natural order and spacing. The brand wordmark stays LTR. If the true RTL layout looks worse, fall back to copy-only (set `dir="rtl"` only on the text blocks and keep the composition) and say which mode you used.

**Culture-Aware QA.** Judge the actual creative per market: religious references, symbols, maps and borders, gestures, animals and food, alcohol, gambling, medical and financial claims, modesty norms, and local ad rules. Search the web if the answer depends on current law. Don't edit the creative by default. Move risky files with `flag-culture-aware`, give a reason, and tell the user.

Fill in `qa/visual_review.json` honestly (pass/fail/not_applicable plus notes), then run:

```bash
$PY $T release-check final --review qa/visual_review.json
```

Don't call the pack ready until this prints READY. If an output fails, fix it and re-render once. If it still fails, deliver it with a clear warning.

### 8. Deliver

Report the path to `final/`, the file count per language, the per-language contact sheets, any fallbacks or flags, and the limitations that apply to this creative. `creative.html` + `copy/` are the reusable master: adding a language later only needs a new `copy/<lang>.json` and a re-render.

## Limitations (say these plainly when they apply)

- **Areas the subjects used to cover are still inpainted.** `extend` fixes stretching and new canvas area with real texture, but the region LaMa filled where subjects were removed stays softer. Lay subjects back over it, as the source did. Structured scenes (rooms, streets) can't be quilted. For those, ask for a layered PSD/Figma file or a clean background.
- Quilting needs enough clean texture. If `clean_x` is narrow, big distinctive objects such as a large flower can repeat. Check the wide plates visually.
- **Characters and products cannot be re-posed or extended.** A cropped leg stays cropped. Where the Codex edition could outpaint, this edition re-composes instead.
- **Text integrated into 3D surfaces or photos** (packaging, shirts, signage) can only be patched flat with `text_slots`. On strongly curved surfaces, say so and leave it in the source language.
- **Font matching** is closest-Google-Font. Exact brand fonts require the user to supply the font files: put them in the run folder and use `@font-face`.
- **Renders need network access** for Google Fonts, unless the fonts are self-hosted.
