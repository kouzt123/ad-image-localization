#!/usr/bin/env python3
"""Decompose a flat ad creative into reusable layers for HTML reconstruction.

Without an image-generation model, instead of asking a model to repaint the
creative for every language and size, we split the source into:

* a clean background plate (text + movable subjects removed via LaMa inpainting),
  plus outpainted plate variants for very wide / very tall canvases;
* transparent component cutouts (character, product, phone, stickers, logo mark),
  optionally with their own in-component text erased so it can be re-set in HTML;
* a layers.json describing every layer and text slot in normalized coordinates.

The coding agent then writes creative.html from these layers and render.py
screenshots it.

Subcommands:
  grid       overlay a labelled pixel grid on an image (for measuring boxes)
  crop       crop a region (optionally upscaled) for close visual inspection
  decompose  run the full scene.json -> layers pipeline
  outpaint   extend a plate to another aspect ratio with LaMa
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

LANCZOS = Image.Resampling.LANCZOS


# ---------------------------------------------------------------- utilities

def load_font(size: int) -> ImageFont.ImageFont:
    for name in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def box_of(spec: dict[str, Any]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (int(round(v)) for v in spec["box"])
    if x1 <= x0 or y1 <= y0:
        raise SystemExit(f"invalid box for {spec.get('id')}: {spec['box']}")
    return x0, y0, x1, y1


_LAMA = None


def lama():
    global _LAMA
    if _LAMA is None:
        from simple_lama_inpainting import SimpleLama  # heavy import; lazy
        _LAMA = SimpleLama()
    return _LAMA


def pushpull_fill(image: Image.Image, mask: Image.Image) -> Image.Image:
    """Seamless membrane fill for smooth backgrounds (sky, gradients, glows, studio
    sweeps): masked pixels are interpolated from the surrounding colours through a
    weighted image pyramid. No texture is invented, so no ghost rectangles."""
    img = np.asarray(image.convert("RGB")).astype(np.float64)
    known = (np.asarray(mask.convert("L")) < 128).astype(np.float64)
    levels = []
    c, w = img * known[..., None], known
    while min(w.shape) > 2:
        levels.append((c, w))
        h2, w2 = (c.shape[0] + 1) // 2, (c.shape[1] + 1) // 2
        cp = np.zeros((h2 * 2, w2 * 2, 3)); wp = np.zeros((h2 * 2, w2 * 2))
        cp[: c.shape[0], : c.shape[1]] = c; wp[: w.shape[0], : w.shape[1]] = w
        c = cp.reshape(h2, 2, w2, 2, 3).sum(axis=(1, 3))
        w = wp.reshape(h2, 2, w2, 2).sum(axis=(1, 3))
    est = c / np.maximum(w, 1e-9)[..., None]
    for c_l, w_l in reversed(levels):
        up = np.asarray(Image.fromarray(np.clip(est, 0, 255).astype(np.uint8)).resize(
            (c_l.shape[1], c_l.shape[0]), Image.Resampling.BILINEAR)).astype(np.float64)
        col = c_l / np.maximum(w_l, 1e-9)[..., None]
        a = np.clip(w_l, 0, 1)[..., None]
        est = col * a + up * (1 - a)
    out = np.where(known[..., None] > 0, img, est)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def inpaint(image: Image.Image, mask: Image.Image, engine: str = "lama") -> Image.Image:
    """Fill white mask pixels. Returns an RGB image the same size as the input."""
    image = image.convert("RGB")
    mask = mask.convert("L").point(lambda v: 255 if v > 127 else 0)
    if not mask.getbbox():
        return image
    if engine == "smooth":
        return pushpull_fill(image, mask)
    if engine == "opencv":
        import cv2
        out = cv2.inpaint(np.array(image)[:, :, ::-1], np.array(mask), 7, cv2.INPAINT_TELEA)
        return Image.fromarray(out[:, :, ::-1])
    result = lama()(image, mask)
    # LaMa pads to a multiple of 8; crop back and keep untouched pixels exact.
    result = result.crop((0, 0, image.width, image.height)).convert("RGB")
    return Image.composite(result, image, mask)


def inpaint_bounded(image: Image.Image, mask: Image.Image, engine: str, max_side: int = 1280) -> Image.Image:
    """Inpaint at <= max_side (LaMa is memory hungry on 8 GB machines), then upscale
    only the filled pixels back into the full-resolution image."""
    scale = min(1.0, max_side / max(image.size))
    if scale >= 1.0:
        return inpaint(image, mask, engine)
    small = image.resize((int(image.width * scale), int(image.height * scale)), LANCZOS)
    small_mask = mask.resize(small.size, Image.Resampling.NEAREST)
    filled = inpaint(small, small_mask, engine).resize(image.size, LANCZOS)
    return Image.composite(filled, image.convert("RGB"), mask.convert("L"))


def text_mask_in_box(image: Image.Image, box: tuple[int, int, int, int], mode: str, dilate: int) -> Image.Image:
    """Mask for text inside box. 'box' masks the whole rectangle; 'ink' masks only
    pixels that differ from the box's dominant (background) colour, which keeps
    inpainting tight on busy backgrounds."""
    mask = Image.new("L", image.size, 0)
    x0, y0, x1, y1 = box
    if mode == "box":
        ImageDraw.Draw(mask).rectangle(box, fill=255)
    else:
        region = np.asarray(image.convert("RGB").crop(box)).astype(np.int16)
        border = np.concatenate([region[0], region[-1], region[:, 0], region[:, -1]])
        bg = np.median(border, axis=0)
        dist = np.abs(region - bg).sum(axis=2)
        ink = (dist > 60).astype(np.uint8) * 255
        mask.paste(Image.fromarray(ink), (x0, y0))
    if dilate:
        mask = mask.filter(ImageFilter.MaxFilter(dilate * 2 + 1))
    return mask


def alpha_for_component(image: Image.Image, comp: dict[str, Any]) -> Image.Image:
    """Full-canvas alpha mask for a component according to its cutout method."""
    method = comp.get("cutout", "rembg")
    x0, y0, x1, y1 = box_of(comp)
    alpha = Image.new("L", image.size, 0)
    if method == "rect":
        ImageDraw.Draw(alpha).rectangle((x0, y0, x1 - 1, y1 - 1), fill=255)
    elif method.startswith("rounded"):
        radius = int(method.split(":", 1)[1]) if ":" in method else 24
        ImageDraw.Draw(alpha).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radius, fill=255)
    elif method.startswith("ellipse"):
        ImageDraw.Draw(alpha).ellipse((x0, y0, x1 - 1, y1 - 1), fill=255)
    elif method in ("rembg", "bgkey"):
        crop = image.convert("RGB").crop((x0, y0, x1, y1))
        if method == "rembg":
            from rembg import remove
            cut = remove(crop, session=rembg_session(comp.get("rembg_model", "isnet-general-use")),
                         post_process_mask=True)
            local = cut.getchannel("A")
        else:
            local = bgkey_alpha(crop, float(comp.get("key_low", 40)), float(comp.get("key_high", 90)))
        if comp.get("keep_largest", True):
            local = keep_largest_blob(local)
        if comp.get("fill_holes", True):
            local = fill_holes(local)
        alpha.paste(local, (x0, y0))
    elif method.startswith("polygon:"):
        pts = json.loads(method.split(":", 1)[1])
        ImageDraw.Draw(alpha).polygon([tuple(p) for p in pts], fill=255)
    else:
        raise SystemExit(f"unknown cutout method for {comp.get('id')}: {method}")
    # Subtract regions explicitly owned by something in front (e.g. a paw over a phone).
    for sub in comp.get("subtract", []):
        ImageDraw.Draw(alpha).rectangle(tuple(sub), fill=0)
    return alpha


_SESSIONS: dict[str, Any] = {}


def rembg_session(model: str):
    if model not in _SESSIONS:
        from rembg import new_session
        _SESSIONS[model] = new_session(model)
    return _SESSIONS[model]


def bgkey_alpha(crop: Image.Image, low: float, high: float) -> Image.Image:
    """Key out a near-uniform surrounding (sky, studio backdrop, flat colour).
    Background colour = median of the crop border; alpha ramps from low..high
    colour distance. Best for stickers, badges, icons, packshots on flat colour."""
    arr = np.asarray(crop).astype(np.float32)
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.sqrt(((arr - bg) ** 2).sum(axis=2))
    alpha = np.clip((dist - low) / max(1.0, high - low), 0, 1) * 255
    return Image.fromarray(alpha.astype(np.uint8)).filter(ImageFilter.MedianFilter(3))


def keep_largest_blob(alpha: Image.Image) -> Image.Image:
    import cv2
    arr = np.array(alpha)
    binary = (arr > 20).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 2:
        return alpha
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    keep = np.isin(labels, [largest])
    return Image.fromarray(np.where(keep, arr, 0).astype(np.uint8))


def fill_holes(alpha: Image.Image) -> Image.Image:
    """Make enclosed transparent regions opaque (rembg often drops inner ear/screen areas)."""
    import cv2
    arr = np.array(alpha)
    solid = (arr > 127).astype(np.uint8) * 255
    h, w = solid.shape
    flood = solid.copy()
    cv2.floodFill(flood, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 255)
    holes = cv2.bitwise_not(flood)
    return Image.fromarray(np.maximum(arr, holes).astype(np.uint8))


def rel(v: float, total: float) -> float:
    return round(v / total, 5)


# ---------------------------------------------------------------- commands

def command_grid(args: argparse.Namespace) -> int:
    image = Image.open(args.image).convert("RGB")
    step = args.step
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    font = load_font(max(10, step // 4))
    for x in range(0, image.width, step):
        strong = x % (step * 2) == 0
        draw.line((x, 0, x, image.height), fill=(255, 0, 80, 170 if strong else 90), width=1)
        if strong:
            draw.text((x + 2, 2), str(x), fill=(255, 0, 80, 255), font=font, stroke_width=2, stroke_fill="white")
    for y in range(0, image.height, step):
        strong = y % (step * 2) == 0
        draw.line((0, y, image.width, y), fill=(0, 90, 255, 170 if strong else 90), width=1)
        if strong:
            draw.text((2, y + 2), str(y), fill=(0, 90, 255, 255), font=font, stroke_width=2, stroke_fill="white")
    if args.scene:
        scene = read_json(Path(args.scene))
        for item in scene.get("components", []) + scene.get("texts", []):
            box = box_of(item)
            colour = (0, 200, 0, 255) if item in scene.get("components", []) else (255, 140, 0, 255)
            draw.rectangle(box, outline=colour, width=3)
            draw.text((box[0] + 4, box[1] + 4), item["id"], fill=colour, font=font, stroke_width=2, stroke_fill="black")
            for slot in item.get("text_slots", []):
                draw.rectangle(box_of(slot), outline=(255, 140, 0, 255), width=2)
    overlay.save(args.output)
    print(json.dumps({"output": args.output, "size": image.size}))
    return 0


def command_crop(args: argparse.Namespace) -> int:
    image = Image.open(args.image).convert("RGB")
    x0, y0, x1, y1 = (int(v) for v in args.box.split(","))
    crop = image.crop((x0, y0, x1, y1))
    if args.scale != 1:
        crop = crop.resize((int(crop.width * args.scale), int(crop.height * args.scale)), LANCZOS)
    crop.save(args.output)
    print(json.dumps({"output": args.output, "size": crop.size}))
    return 0


def outpaint(plate: Image.Image, target_ratio: float, engine: str, max_side: int = 1024) -> Image.Image:
    """Extend a plate symmetrically to target_ratio (w/h) and fill the new area."""
    w, h = plate.size
    ratio = w / h
    if abs(ratio - target_ratio) < 0.01:
        return plate
    if target_ratio > ratio:
        new_w, new_h = int(round(h * target_ratio)), h
    else:
        new_w, new_h = w, int(round(w / target_ratio))
    ox, oy = (new_w - w) // 2, (new_h - h) // 2
    # Seed the new area with mirrored content so LaMa has sensible colour context.
    canvas = Image.new("RGB", (new_w, new_h))
    mirrored = plate.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if new_w > w else plate.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if new_w > w:
        for x in range(ox - w, new_w, w):
            idx = (x - ox) // w
            canvas.paste(plate if idx % 2 == 0 else mirrored, (x, 0))
    else:
        for y in range(oy - h, new_h, h):
            idx = (y - oy) // h
            canvas.paste(plate if idx % 2 == 0 else mirrored, (0, y))
    canvas.paste(plate, (ox, oy))
    mask = Image.new("L", (new_w, new_h), 255)
    ImageDraw.Draw(mask).rectangle((ox + 4, oy + 4, ox + w - 5, oy + h - 5), fill=0)
    # LaMa is resolution sensitive; work at <= max_side then upscale the fill.
    scale = min(1.0, max_side / max(new_w, new_h))
    if scale < 1.0:
        small = canvas.resize((int(new_w * scale), int(new_h * scale)), LANCZOS)
        small_mask = mask.resize(small.size, Image.Resampling.NEAREST)
        filled = inpaint(small, small_mask, engine).resize((new_w, new_h), LANCZOS)
        return Image.composite(filled, canvas, mask)
    return inpaint(canvas, mask, engine)


def command_decompose(args: argparse.Namespace) -> int:
    scene_path = Path(args.scene)
    scene = read_json(scene_path)
    base = scene_path.parent
    source = Image.open(base / scene["source"]).convert("RGB")
    W, H = source.size
    out = Path(args.output) if args.output else base / "work" / "layers"
    out.mkdir(parents=True, exist_ok=True)
    engine = scene.get("inpaint_engine", args.engine)
    dilate = int(scene.get("mask_dilate", 6))

    layers: dict[str, Any] = {"source": scene["source"], "size": [W, H], "plates": {}, "components": [], "texts": []}

    # 1. Component cutouts (with their own text erased first).
    plate_mask = Image.new("L", (W, H), 0)
    for comp in scene.get("components", []):
        x0, y0, x1, y1 = box_of(comp)
        comp_source = Image.open(base / comp["image"]).convert("RGB") if comp.get("image") else source
        if comp.get("image"):
            comp.setdefault("remove_from_plate", False)
        working = comp_source.copy()
        slot_mask = Image.new("L", (W, H), 0)
        for slot in comp.get("text_slots", []):
            slot_mask = Image.fromarray(np.maximum(np.array(slot_mask), np.array(
                text_mask_in_box(comp_source, box_of(slot), slot.get("mask", "box"), int(slot.get("dilate", 2))))))
        if slot_mask.getbbox():
            pad = 24
            region = (max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad))
            patched = inpaint(working.crop(region), slot_mask.crop(region), engine)
            working.paste(patched, region[:2])
        alpha = alpha_for_component(comp_source, comp)
        rgba = working.convert("RGBA")
        rgba.putalpha(alpha)
        cut = rgba.crop((x0, y0, x1, y1))
        tight = cut.getchannel("A").getbbox() or (0, 0, cut.width, cut.height)
        cut = cut.crop(tight)
        ax0, ay0 = x0 + tight[0], y0 + tight[1]
        cw, ch = cut.size
        name = f"{comp['id']}.png"
        cut.save(out / name)
        if comp.get("remove_from_plate", True):
            plate_mask = Image.fromarray(np.maximum(np.array(plate_mask), np.array(alpha)))
        entry = {
            "id": comp["id"], "file": name, "role": comp.get("role", "subject"),
            "px": [ax0, ay0, cw, ch],
            "rel": {"left": rel(ax0, W), "top": rel(ay0, H), "width": rel(cw, W), "height": rel(ch, H)},
            "aspect": round(cw / ch, 5),
            "text_slots": [],
        }
        for slot in comp.get("text_slots", []):
            sx0, sy0, sx1, sy1 = box_of(slot)
            entry["text_slots"].append({
                "id": slot["id"], "source_text": slot.get("text", ""),
                "rotate": slot.get("rotate", 0), "style": slot.get("style", {}),
                # Position relative to the component cutout, as percentages.
                "rel": {"left": rel(sx0 - ax0, cw), "top": rel(sy0 - ay0, ch),
                        "width": rel(sx1 - sx0, cw), "height": rel(sy1 - sy0, ch)},
            })
        layers["components"].append(entry)

    # 2. Canvas-level text: erase from plate, record slots. "fill": "smooth" erases
    #    via push-pull (best on sky/gradients); everything else goes to LaMa.
    smooth_mask = Image.new("L", (W, H), 0)
    for text in scene.get("texts", []):
        x0, y0, x1, y1 = box_of(text)
        tmask = text_mask_in_box(source, (x0, y0, x1, y1), text.get("mask", "box"), int(text.get("dilate", 4)))
        if text.get("fill") == "smooth":
            smooth_mask = Image.fromarray(np.maximum(np.array(smooth_mask), np.array(tmask)))
        else:
            plate_mask = Image.fromarray(np.maximum(np.array(plate_mask), np.array(tmask)))
        layers["texts"].append({
            "id": text["id"], "role": text.get("role", "body"), "source_text": text.get("text", ""),
            "style": text.get("style", {}),
            "rel": {"left": rel(x0, W), "top": rel(y0, H), "width": rel(x1 - x0, W), "height": rel(y1 - y0, H)},
        })

    # 3. Clean plate. Extra masks: [x0,y0,x1,y1] rectangles or [[x,y],...] polygons.
    for extra in scene.get("plate_extra_masks", []):
        if extra and isinstance(extra[0], (list, tuple)):
            ImageDraw.Draw(plate_mask).polygon([tuple(p) for p in extra], fill=255)
        else:
            ImageDraw.Draw(plate_mask).rectangle(tuple(extra), fill=255)
    if dilate:
        plate_mask = plate_mask.point(lambda v: 255 if v > 20 else 0).filter(ImageFilter.MaxFilter(dilate * 2 + 1))
    plate_mask.save(out / "plate_mask.png")
    # Big holes inpaint far more coherently at low resolution (LaMa's receptive
    # field is relative), so fill at plate_work_side and upscale only the hole.
    base_img = source
    if smooth_mask.getbbox():
        if dilate:
            smooth_mask = smooth_mask.point(lambda v: 255 if v > 20 else 0).filter(ImageFilter.MaxFilter(dilate * 2 + 1))
        base_img = pushpull_fill(source, Image.fromarray(np.maximum(np.array(smooth_mask), np.array(plate_mask))))
        smooth_mask.save(out / "plate_smooth_mask.png")
    plate = inpaint_bounded(base_img, plate_mask, engine, int(scene.get("plate_work_side", 512)))
    soften = float(scene.get("plate_soften", 0))
    if soften:
        # Uniform shallow depth-of-field hides the sharp/soft seam around fills.
        plate = plate.filter(ImageFilter.GaussianBlur(soften))
    plate.save(out / "plate.png")
    layers["plates"]["native"] = {"file": "plate.png", "aspect": round(W / H, 5)}

    # 4. Outpainted plates for extreme aspect classes.
    # Opt-in only: LaMa outpainting of large areas is usually murky. Prefer cover-
    # cropping the plate with object-position, or CSS sky/ground extensions.
    for name, ratio in scene.get("plate_variants", {}).items():
        variant = outpaint(plate, float(ratio), engine)
        file = f"plate_{name}.png"
        variant.save(out / file)
        layers["plates"][name] = {"file": file, "aspect": round(variant.width / variant.height, 5)}

    write_json(out / "layers.json", layers)
    preview_sheet(out, layers)
    print(json.dumps({"layers": str(out / "layers.json"), "components": len(layers["components"]),
                      "texts": len(layers["texts"]), "plates": list(layers["plates"])}, indent=2))
    return 0


def preview_sheet(out: Path, layers: dict[str, Any]) -> None:
    """One image showing every extracted layer on a checkerboard, for visual QA."""
    files = [p["file"] for p in layers["plates"].values()] + [c["file"] for c in layers["components"]]
    thumbs = []
    for f in files:
        im = Image.open(out / f).convert("RGBA")
        im.thumbnail((360, 360), LANCZOS)
        board = Image.new("RGBA", (380, 400), (255, 255, 255, 255))
        d = ImageDraw.Draw(board)
        for y in range(0, 360, 16):
            for x in range(0, 380, 16):
                if (x // 16 + y // 16) % 2:
                    d.rectangle((x, y, x + 15, y + 15), fill=(220, 220, 220, 255))
        board.alpha_composite(im, ((380 - im.width) // 2, (360 - im.height) // 2))
        d.text((8, 368), f, fill=(0, 0, 0, 255), font=load_font(16))
        thumbs.append(board)
    cols = 4
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 380, rows * 400), "white")
    for i, t in enumerate(thumbs):
        sheet.paste(t.convert("RGB"), ((i % cols) * 380, (i // cols) * 400))
    sheet.save(out / "layers_preview.jpg", quality=88)


# ---------------------------------------------------------------- native plate extension
#
# Instead of stretching (soft) or LaMa-outpainting (murky) the plate for other
# aspect ratios, build an exact-size plate per delivery size:
#   * taller targets: fit width, keep the plate at the bottom and continue the sky
#     upward per column (smooth gradient toward a sampled zenith colour + grain);
#   * wider targets: fit height and grow the sides with image quilting: column
#     strips of the plate itself are stitched along minimum-error seams, so every
#     new pixel is real source texture (mountains, grass, flowers), never a smear;
#   * extreme strips (ratio beyond cover_above): plain cover crop at the band given
#     by strip_focus, since only a thin slice is visible anyway.

def _min_cut_seam(err: np.ndarray) -> np.ndarray:
    """Vertical min-error path through an (h, ov) error surface -> column per row."""
    h, w = err.shape
    cost = err.copy()
    back = np.zeros((h, w), dtype=np.int64)
    for y in range(1, h):
        prev = cost[y - 1]
        left = np.r_[np.inf, prev[:-1]]
        right = np.r_[prev[1:], np.inf]
        stack = np.vstack([left, prev, right])
        idx = np.argmin(stack, axis=0)
        cost[y] += stack[idx, np.arange(w)]
        back[y] = np.arange(w) + idx - 1
    seam = np.zeros(h, dtype=np.int64)
    seam[-1] = int(np.argmin(cost[-1]))
    for y in range(h - 1, 0, -1):
        seam[y - 1] = back[y, seam[y]]
    return seam


def quilt_right(img: np.ndarray, extra: int, rng: np.random.Generator, patch: int, overlap: int,
                source_ranges: list[tuple[int, int]] | None = None) -> np.ndarray:
    """Grow img (h, w, 3 float32) to the right by `extra` px with column-strip quilting.
    Candidate strips are taken only from `source_ranges` (x ranges of clean, original
    texture; default: whole image). Seams: min-error cut where there is texture,
    wide crossfade where the overlap is smooth (sky), so neither reads as an edge."""
    h, w, _ = img.shape
    ranges = source_ranges or [(0, w)]
    starts = [x for a, b in ranges for x in range(a, max(a + 1, b - patch + 1), 3) if x + patch <= w]
    if not starts:
        raise SystemExit("quilting source ranges are narrower than the patch; lower patch or widen ranges")
    out = img
    recent: list[int] = []
    ramp = np.linspace(0, 1, overlap, dtype=np.float32)[None, :]
    while out.shape[1] < w + extra:
        target = out[:, -overlap:]
        ts = target[::4, ::2]
        scored = []
        for x in starts:
            cand = img[::4, x:x + overlap][:, ::2]
            scored.append((float(((cand - ts) ** 2).mean()), x))
        scored.sort()
        pool = [b for b in scored[:24] if all(abs(b[1] - r) > patch // 2 for r in recent)] or \
               [b for b in scored[:16] if all(abs(b[1] - r) > patch // 2 for r in recent[-2:])] or scored[:1]
        _, x = pool[int(rng.integers(0, min(4, len(pool))))]
        recent.append(x)
        piece = img[:, x:x + patch]
        err = ((piece[:, :overlap] - target) ** 2).sum(axis=2)
        seam = _min_cut_seam(err)
        cut = (np.arange(overlap)[None, :] >= seam[:, None]).astype(np.float32)
        cut = np.asarray(Image.fromarray((cut * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5))) / 255.0
        # Per-row texture energy of the overlap decides cut vs. crossfade.
        energy = np.abs(np.diff(target, axis=1)).mean(axis=(1, 2))
        energy = np.convolve(energy, np.ones(15) / 15, mode="same")
        tex = np.clip((energy - 1.0) / 3.0, 0, 1)[:, None]
        mask = tex * cut + (1 - tex) * ramp
        blended = target * (1 - mask[..., None]) + piece[:, :overlap] * mask[..., None]
        out = np.concatenate([out[:, :-overlap], blended, piece[:, overlap:]], axis=1)
    return out[:, : w + extra]


def extend_sky_up(img: np.ndarray, extra: int, zenith: tuple[float, float, float] | None, rng: np.random.Generator) -> np.ndarray:
    """Continue each column upward, C1-continuous with the plate: start at the top
    row's colour and slope, then ease toward an asymptote (optionally `zenith`).
    Only for sky-like tops."""
    h, w, _ = img.shape
    k = max(8, h // 25)
    smooth = np.asarray(Image.fromarray(np.clip(img[: k * 2], 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(max(6, w // 60)))).astype(np.float32)
    row0, rowk = smooth[1], smooth[k]
    slope = (row0 - rowk) / k                      # colour change per px going up
    L = max(60.0, extra * 0.35)
    d = np.arange(extra, 0, -1, dtype=np.float32)[:, None, None]   # distance above the plate
    ext = row0[None] + slope[None] * L * (1 - np.exp(-d / L))
    if zenith is not None:
        t = (d / extra) ** 2
        ext = ext * (1 - t * 0.6) + np.array(zenith, dtype=np.float32)[None, None, :] * (t * 0.6)
    # Horizontal smoothing of the extension so per-column noise doesn't streak.
    ext = np.asarray(Image.fromarray(np.clip(ext, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(3))).astype(np.float32)
    ext += rng.normal(0, 1.1, ext.shape).astype(np.float32)
    return np.concatenate([ext, img], axis=0)


def sky_model_fill(canvas: np.ndarray, known: np.ndarray, sky_rows: int, zenith: np.ndarray | None,
                   rng: np.random.Generator) -> np.ndarray:
    """Fill unknown pixels as  row_profile(y) + residual * decay(distance).
    The row profile (median colour of known pixels per row) captures the sky's
    vertical gradient; rows with nothing known are extrapolated toward `zenith`.
    The residual (local glows, haze) is spread by push-pull and fades with distance,
    so extensions continue the gradient instead of smearing or going flat."""
    import cv2
    th, tw, _ = canvas.shape
    prof = np.full((th, 3), np.nan, np.float32)
    for y in range(th):
        m = known[y] > 0
        if m.sum() > 4:
            prof[y] = np.median(canvas[y, m], axis=0)
    rows = np.where(~np.isnan(prof[:, 0]))[0]
    if len(rows) == 0:
        return canvas
    first = rows[0]
    ref = prof[first:first + max(4, th // 50)]
    ref = np.nanmean(ref, axis=0)
    if zenith is None:
        zenith = ref * np.array([0.72, 0.88, 1.0], np.float32)
    # Extrapolate upward C1-continuously: keep the plate's own vertical slope at the
    # seam (no Mach band), let it decay, and ease toward the zenith colour higher up.
    k = max(6, min(len(rows) - 1, th // 40))
    below = prof[first + k] if first + k < th and not np.isnan(prof[first + k, 0]) else ref
    slope = (prof[first] - below) / k
    L = max(30.0, first * 0.3)
    for y in range(first - 1, -1, -1):
        d = first - y
        t = d / max(1, first)
        cont = prof[first] + slope * L * (1 - np.exp(-d / L))
        w = 0.85 * t ** 1.5
        prof[y] = cont * (1 - w) + zenith * w
    for c in range(3):                           # fill interior gaps, then smooth
        col = prof[:, c]
        idx = np.where(~np.isnan(col))[0]
        prof[:, c] = np.interp(np.arange(th), idx, col[idx])
    prof = cv2.GaussianBlur(prof[:, None, :], (1, 0), sigmaX=0.1, sigmaY=max(3, th / 40))[:, 0, :] if th > 8 else prof
    base = np.repeat(prof[:, None, :], tw, axis=1)
    resid = np.where(known[..., None] > 0, canvas - base, 0)
    rimg = Image.fromarray(np.clip(resid + 128, 0, 255).astype(np.uint8))
    rfill = np.asarray(pushpull_fill(rimg, Image.fromarray(((1 - known) * 255).astype(np.uint8)))).astype(np.float32) - 128
    scale = max(th, tw)
    # Low-frequency residual only: fine detail must not be smeared into new sky.
    rfill = cv2.GaussianBlur(rfill, (0, 0), sigmaX=max(4, scale / 60))
    dist = cv2.distanceTransform(((1 - known) * 255).astype(np.uint8), cv2.DIST_L2, 5)
    decay = np.exp(-dist / max(20.0, 0.12 * scale))[..., None]
    fill = base + rfill * decay + rng.normal(0, 1.1, canvas.shape).astype(np.float32)
    # Feather known sky into the model over a band inside the known edge, so
    # neither vertical (plate side) nor horizontal (plate top / band top) seams show.
    inside = cv2.distanceTransform((known * 255).astype(np.uint8), cv2.DIST_L2, 5)
    feather = max(12.0, scale * 0.06)
    alpha = np.clip(inside / feather, 0, 1)
    alpha = (alpha * alpha * (3 - 2 * alpha))[..., None]
    if sky_rows:
        alpha[sky_rows:] = 1.0      # below the horizon, quilting already handled seams
    return canvas * alpha + fill * (1 - alpha)


def build_plate(plate: Image.Image, tw: int, th: int, opts: dict[str, Any], seed: int = 7) -> tuple[Image.Image, str]:
    """Exact-size plate for one delivery size. See the section comment above.
    opts: horizon (0..1 of plate height where textured ground begins; sky above),
          clean_x ([[x0,x1],...] original-texture columns in plate px),
          zenith ([r,g,b] colour the sky deepens toward when extended upward),
          trim_top (0..1 of plate height to discard at the top edge, e.g. vignette),
          grow (both|left|right), patch (fraction of width), cover_above, strip_focus."""
    rng = np.random.default_rng(seed + tw * 7 + th)
    pw, ph = plate.size
    pa, ta = pw / ph, tw / th
    cover_above = float(opts.get("cover_above", 3.0))
    zenith = np.array(opts["zenith"], np.float32) if opts.get("zenith") else None
    if ta >= cover_above or abs(ta - pa) < 0.02:
        s = max(tw / pw, th / ph)
        rs = plate.resize((max(tw, round(pw * s)), max(th, round(ph * s))), LANCZOS)
        fy = float(opts.get("strip_focus", 0.3)) if ta >= cover_above else 0.5
        ox, oy = (rs.width - tw) // 2, int((rs.height - th) * fy)
        return rs.crop((ox, oy, ox + tw, oy + th)), "cover"

    canvas = np.zeros((th, tw, 3), np.float32)
    known = np.zeros((th, tw), np.float32)
    if ta < pa:   # taller: fit width, plate at the bottom, sky above
        s = tw / pw
        rs = np.asarray(plate.resize((tw, round(ph * s)), LANCZOS)).astype(np.float32)
        trim = int(float(opts.get("trim_top", 0.04)) * rs.shape[0])
        oy = th - rs.shape[0]
        canvas[oy:] = rs
        known[oy + trim:] = 1
        hy = oy + trim
        method = "sky-extend"
    else:         # wider: fit height, quilt the ground band sideways, sky by model
        s = th / ph
        rs = np.asarray(plate.resize((round(pw * s), th), LANCZOS)).astype(np.float32)
        rw = rs.shape[1]
        extra = tw - rw
        side = opts.get("grow", "both")
        left = extra // 2 if side == "both" else (extra if side == "left" else 0)
        right = extra - left
        hy = int(float(opts.get("horizon", 0.0)) * th)
        patch = max(48, int(rw * float(opts.get("patch", 0.12))))
        overlap = max(16, patch // 2)
        ranges = [(int(a * s), int(b * s)) for a, b in opts.get("clean_x", [])] or None
        band = rs[hy:]
        if right:
            band = quilt_right(band, right, rng, patch, overlap, ranges)
        if left:
            w0 = band.shape[1]
            # Plate columns sit at 0..rw in `band`; mirror them into flipped coordinates.
            fr = [(w0 - b, w0 - a) for a, b in (ranges or [(0, rw)])]
            band = quilt_right(band[:, ::-1], left, rng, patch, overlap, fr)[:, ::-1]
        canvas[hy:] = band[:, :tw]
        known[hy:] = 1
        canvas[:hy, left:left + rw] = rs[:hy]
        known[:hy, left:left + rw] = 1
        method = "quilt"
    sky_rows = hy + int(max(12.0, max(th, tw) * 0.06)) + 2 if method == "quilt" else th
    out = sky_model_fill(canvas, known, sky_rows, zenith, rng)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)), method


def command_extend(args: argparse.Namespace) -> int:
    layers_dir = Path(args.layers)
    plate = Image.open(layers_dir / args.plate).convert("RGB")
    opts: dict[str, Any] = json.loads(args.options) if args.options else {}
    sizes: list[str] = []
    if args.plan:
        plan = read_json(Path(args.plan))
        sizes = sorted({d["output_size"] for d in plan["deliverables"]})
    if args.sizes:
        sizes += [s.strip() for s in args.sizes.split(",") if s.strip()]
    out_dir = layers_dir / "plates"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for size in dict.fromkeys(sizes):
        tw, th = (int(v) for v in size.split("x"))
        img, method = build_plate(plate, tw, th, opts)
        img.save(out_dir / f"plate_{size}.png")
        report[size] = method
        print(f"plate_{size}.png  {method}")
    write_json(out_dir / "plates.json", report)
    return 0


def command_rotate(args: argparse.Namespace) -> int:
    """Deskew a copy of the source (positive = counter-clockwise) so tilted UI or
    packaging can be measured and cropped axis-aligned."""
    image = Image.open(args.image).convert("RGB")
    rotated = image.rotate(args.degrees, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    rotated.save(args.output)
    print(json.dumps({"output": args.output, "degrees": args.degrees, "size": rotated.size}))
    return 0


def command_outpaint(args: argparse.Namespace) -> int:
    plate = Image.open(args.image).convert("RGB")
    w, h = (int(v) for v in args.ratio.split(":")) if ":" in args.ratio else (float(args.ratio), 1)
    result = outpaint(plate, float(w) / float(h), args.engine)
    result.save(args.output)
    print(json.dumps({"output": args.output, "size": result.size}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("grid", help="Overlay a labelled coordinate grid (and scene boxes).")
    g.add_argument("image")
    g.add_argument("output")
    g.add_argument("--step", type=int, default=50)
    g.add_argument("--scene", help="Optional scene.json whose boxes are drawn on top.")
    g.set_defaults(func=command_grid)

    c = sub.add_parser("crop", help="Crop x0,y0,x1,y1 for close inspection.")
    c.add_argument("image")
    c.add_argument("output")
    c.add_argument("--box", required=True)
    c.add_argument("--scale", type=float, default=1.0)
    c.set_defaults(func=command_crop)

    d = sub.add_parser("decompose", help="scene.json -> plate(s), cutouts, layers.json.")
    d.add_argument("scene")
    d.add_argument("--output", help="Layer folder (default: <scene dir>/work/layers).")
    d.add_argument("--engine", choices=["lama", "opencv"], default="lama")
    d.set_defaults(func=command_decompose)

    e = sub.add_parser("extend", help="Build an exact-size plate per delivery size (sky continuation / quilting / cover).")
    e.add_argument("layers", help="work/layers folder")
    e.add_argument("--plate", default="plate.png")
    e.add_argument("--plan", help="preflight.json (all delivery sizes)")
    e.add_argument("--sizes", help="Extra WxH list")
    e.add_argument("--options", help='JSON, e.g. {"grow":"both","cover_above":3,"strip_focus":0.25,"zenith":[28,156,250]}')
    e.set_defaults(func=command_extend)

    r = sub.add_parser("rotate", help="Write a deskewed copy of an image for measuring/cropping tilted parts.")
    r.add_argument("image")
    r.add_argument("output")
    r.add_argument("--degrees", type=float, required=True)
    r.set_defaults(func=command_rotate)

    o = sub.add_parser("outpaint", help="Extend a plate to another ratio, e.g. 4:1 or 9:16.")
    o.add_argument("image")
    o.add_argument("output")
    o.add_argument("--ratio", required=True)
    o.add_argument("--engine", choices=["lama", "opencv"], default="lama")
    o.set_defaults(func=command_outpaint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
