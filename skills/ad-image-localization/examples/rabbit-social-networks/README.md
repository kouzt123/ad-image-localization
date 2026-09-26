# Example: Rabbit Social Networks

Source: `source/rabbit-social-networks.png`, the 1200x1200 English output of the
[GPT-Image edition](https://github.com/kouzt123/ad-image-localization-codex). Target: en / de / ja / ar ×
the `full-pack` profile (12 sizes), 48 files in total.

What's here:

| File | Role |
|---|---|
| `scene.json` | layer spec written by the agent after measuring the source |
| `copy/*.json` | per-language copy (UI strings included, names transliterated for ja/ar) |
| `creative.html` | the responsive reconstruction: 7 aspect layouts, RTL, phone UI rebuilt in HTML |
| `background.js` | per-size canvas background: native plate + haze veil |
| `extend.options.json` | options for `decompose.py extend` |
| `work/layers/` | cutouts, the clean plate and `layers.json` |
| `final-samples/` | a few of the 48 rendered files |
| `qa/` | per-language contact sheets and the recorded visual review |

Reproduce from a checkout (about 2–3 minutes, plus a one-time model download):

```bash
SK=../..                       # skills/ad-image-localization
PY=$SK/.venv/bin/python        # bash $SK/scripts/setup.sh if missing
$PY $SK/scripts/decompose.py rotate source/rabbit-social-networks.png work/source_deskew.png --degrees 3.5
$PY $SK/scripts/decompose.py decompose scene.json
$PY $SK/scripts/ad_image_localization_tools.py preflight job.json --output qa/preflight.json
$PY $SK/scripts/decompose.py extend work/layers --plan qa/preflight.json --options "$(cat extend.options.json)"
$PY $SK/scripts/render.py creative.html --plan qa/preflight.json --copy-dir copy --qa-out qa/render_qa.json
```
