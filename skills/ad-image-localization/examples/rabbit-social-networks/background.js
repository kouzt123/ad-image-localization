/* Background for the rabbit ad: the exact-size plate built by `decompose.py extend`
 * (real source texture, quilted/extended natively per size), plus code-drawn light:
 * the source's soft white haze panel behind the copy, re-fitted to each layout's
 * copy block. Painted on the canvas so the QA contrast check sees what ships. */
SceneKit.register(async (S, c) => {
  const { ctx, W, H, dir, layout } = c;
  const img = await S.loadImage(`work/layers/plates/plate_${W}x${H}.png`, "work/layers/plate.png");
  // RTL: mirror the text-free plate so the untouched sky sits behind the copy.
  S.cover(ctx, img, W, H, { flip: dir === "rtl", fy: layout === "strip" ? 0.2 : 0.5 });
  const copy = S.rectOf(".brand", ".headline");
  const veilAlpha = { story: 0.82, skyscraper: 0.92, strip: 0.5, billboard: 0.5, portrait: 0.62, landscape: 0.35 }[layout] ?? 0.35;
  S.veil(ctx, copy, { alpha: veilAlpha, pad: layout === "story" || layout === "skyscraper" ? 0.14 : 0.08, soft: 0.14 });
});
