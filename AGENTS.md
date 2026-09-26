# Agent instructions

This repository is an agent skill for localizing ad image creatives by rebuilding them in code.

When the user asks to translate text inside an ad image, resize a creative into ad or social sizes, or produce a multi-language delivery pack:

1. Read `skills/ad-image-localization/SKILL.md` and follow its workflow exactly.
2. Run the scripts with the skill's own Python env. If `skills/ad-image-localization/.venv` is missing, create it with `bash skills/ad-image-localization/scripts/setup.sh`.
3. You must be able to **view images** (the source, the layer previews, the renders, the contact sheets). Visual review is a required step, not an optional one.

Development:
- Tests: `skills/ad-image-localization/.venv/bin/python -m unittest discover -s tests`
- End-to-end example: `skills/ad-image-localization/examples/rabbit-social-networks/` (see its `README.md`).
