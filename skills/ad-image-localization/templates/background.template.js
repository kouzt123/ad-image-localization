/* background.js: paints <canvas id="scene"> for every size (copy to the run folder).
 *
 * A) Texture source: exact-size plate from `decompose.py extend` + code-drawn light.
 * B) Flat/vector/gradient source: delete the plate lines and draw everything with
 *    S.sky / S.glow / S.bokeh / S.ridge ... using colours sampled from the source.
 */
SceneKit.register(async (S, c) => {
  const { ctx, W, H, dir, layout } = c;

  // A) native plate for this exact size (falls back to the base plate)
  const img = await S.loadImage(`work/layers/plates/plate_${W}x${H}.png`, "work/layers/plate.png");
  S.cover(ctx, img, W, H, { flip: dir === "rtl" /* only if the plate has no text/logos/faces */ });

  // Legibility haze behind the copy block, tuned per layout (watch low_contrast QA).
  const alpha = { story: 0.7, skyscraper: 0.8, strip: 0.5, billboard: 0.5, portrait: 0.5 }[layout] ?? 0.35;
  S.veil(ctx, S.rectOf(".brand", ".headline"), { alpha, pad: 0.1, soft: 0.14 });

  // Optional code-drawn accents:
  // S.sparkle(ctx, W * 0.85, H * 0.12, Math.min(W, H) * 0.02);
  // S.bokeh(ctx, W, H, { count: 12, area: [0.5, 0, 1, 0.5] });
});
