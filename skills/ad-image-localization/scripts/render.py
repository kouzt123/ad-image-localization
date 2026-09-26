#!/usr/bin/env python3
"""Render an HTML ad reconstruction to every language x size with headless Chrome.

Each render loads creative.html at exactly WxH, injects window.__AD__ (lang, size,
copy JSON), waits for ad-runtime.js to fill copy, load fonts and fit text, then
screenshots the canvas and collects the runtime's DOM QA (overflow, off-canvas
text, tiny type, overlaps, keep-out collisions, story safe zones).

Usage:
  render.py <creative.html> --plan qa/preflight.json --copy-dir copy --output-root .
  render.py <creative.html> --langs en,de --sizes 1200x1200,320x50 --copy-dir copy \
            --out final --slug my-ad [--date 20260926]
  render.py <creative.html> --preview 1200x628 --lang ar --out work/preview_ar.png
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

SIZE_RE = re.compile(r"^(\d+)x(\d+)$")
# Lets ad-runtime.js read cutout alpha from file:// images for keep-out QA.
CHROME_ARGS = ["--allow-file-access-from-files", "--font-render-hinting=none"]


def parse_size(text: str) -> tuple[int, int]:
    m = SIZE_RE.match(text.strip())
    if not m:
        raise SystemExit(f"invalid size: {text} (expected WIDTHxHEIGHT)")
    return int(m.group(1)), int(m.group(2))


def load_copy(copy_dir: Path, lang: str) -> dict[str, Any]:
    for name in (lang, lang.split("-")[0]):
        path = copy_dir / f"{name}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if not k.startswith("_")}
    raise SystemExit(f"missing copy file for {lang} in {copy_dir}")


def jobs_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    if args.preview:
        w, h = parse_size(args.preview)
        return [{"lang": args.lang or "en", "w": w, "h": h, "output": Path(args.out)}]
    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        root = Path(args.output_root or Path(args.plan).resolve().parents[1])
        for item in plan["deliverables"]:
            if item.get("production_method") not in (None, "html_render"):
                continue
            w, h = parse_size(item["output_size"])
            jobs.append({"lang": item["locale"], "w": w, "h": h, "output": root / item["output_file"]})
        return jobs
    if not (args.langs and args.sizes and args.out and args.slug):
        raise SystemExit("give --plan, or --preview, or all of --langs --sizes --out --slug")
    date = args.date or dt.date.today().strftime("%Y%m%d")
    ext = args.ext.lstrip(".")
    for lang in [x.strip() for x in args.langs.split(",") if x.strip()]:
        for size in [x.strip() for x in args.sizes.split(",") if x.strip()]:
            w, h = parse_size(size)
            jobs.append({"lang": lang, "w": w, "h": h,
                         "output": Path(args.out) / f"{args.slug}_{lang}_{w}x{h}_{date}.{ext}"})
    return jobs


def launch(p, channel: str | None):
    try:
        return p.chromium.launch(channel=channel or "chrome", headless=True, args=CHROME_ARGS)
    except Exception:
        # Fall back to Playwright's bundled Chromium (`playwright install chromium`).
        return p.chromium.launch(headless=True, args=CHROME_ARGS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("html")
    ap.add_argument("--plan", help="preflight.json; renders every html_render deliverable")
    ap.add_argument("--output-root", help="Base dir for plan output paths (default: plan's run folder)")
    ap.add_argument("--langs")
    ap.add_argument("--sizes")
    ap.add_argument("--slug")
    ap.add_argument("--date")
    ap.add_argument("--ext", default="png")
    ap.add_argument("--out", help="Output folder, or output file with --preview")
    ap.add_argument("--copy-dir", default="copy")
    ap.add_argument("--preview", help="Render a single WxH (use with --lang and --out file)")
    ap.add_argument("--lang")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="deviceScaleFactor; >1 supersamples then downsizes to exact WxH")
    ap.add_argument("--qa-out", help="Write combined DOM QA JSON here")
    ap.add_argument("--channel", default="chrome", help="Playwright browser channel (chrome, msedge, chromium)")
    ap.add_argument("--timeout", type=int, default=30000)
    args = ap.parse_args(argv)

    from playwright.sync_api import sync_playwright
    from PIL import Image

    html = Path(args.html).resolve()
    copy_dir = Path(args.copy_dir)
    jobs = jobs_from_args(args)
    results = []
    errors = 0
    with sync_playwright() as p:
        browser = launch(p, args.channel)
        for job in jobs:
            copy = load_copy(copy_dir, job["lang"])
            ctx = browser.new_context(viewport={"width": job["w"], "height": job["h"]},
                                      device_scale_factor=args.scale)
            page = ctx.new_page()
            console: list[str] = []
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            page.add_init_script("window.__AD__ = " + json.dumps(
                {"lang": job["lang"], "w": job["w"], "h": job["h"], "copy": copy}, ensure_ascii=False))
            page.goto(html.as_uri())
            try:
                page.wait_for_function("window.__adReady === true", timeout=args.timeout)
            except Exception:
                console.append("timeout waiting for window.__adReady")
            qa = page.evaluate("window.__adQA || null") or {"issues": [{"level": "error", "issue": "runtime_not_ready"}]}
            out: Path = job["output"]
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": job["w"], "height": job["h"]},
                            type="jpeg" if out.suffix.lower() in (".jpg", ".jpeg") else "png",
                            **({"quality": 92} if out.suffix.lower() in (".jpg", ".jpeg") else {}))
            if args.scale != 1:
                with Image.open(out) as im:
                    im.convert("RGB").resize((job["w"], job["h"]), Image.Resampling.LANCZOS).save(out)
            ctx.close()
            n_err = sum(1 for i in qa["issues"] if i.get("level") == "error") + sum("pageerror" in c for c in console)
            errors += n_err
            results.append({"file": out.name, "lang": job["lang"], "size": f"{job['w']}x{job['h']}",
                            "layout": qa.get("layout"), "issues": qa["issues"], "texts": qa.get("texts", []),
                            "fonts_loaded": qa.get("fonts_loaded", []), "console": console})
            flag = "ERR" if n_err else ("warn" if qa["issues"] else "ok")
            print(f"[{flag:>4}] {out}  layout={qa.get('layout')}  issues={len(qa['issues'])}")
            for issue in qa["issues"]:
                print(f"         - {issue.get('level')}: {issue.get('issue')} ({issue.get('id')})"
                      + (f" value={issue['value']}" if "value" in issue else ""))
            for c in console:
                print(f"         - console {c}")
        browser.close()

    if args.qa_out:
        Path(args.qa_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.qa_out).write_text(json.dumps({
            "schema_version": 1, "html": str(html),
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "summary": {"renders": len(results), "errors": errors,
                        "with_warnings": sum(1 for r in results if r["issues"])},
            "renders": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.qa_out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
