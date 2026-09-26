#!/usr/bin/env python3
"""Deterministic helpers for the ad-image-localization skill (code-reconstruction edition).

Forked from the GPT-Image (Codex) edition. Here the pixels come from an HTML
reconstruction rendered by render.py (production_method "html_render"), so every
size is a first-class render rather than a model generation. This script keeps the
repeatable last-mile operations: delivery preflight, safe crop/resize, manifests,
recorded visual-QA gates, contact sheets, culture-aware flags, and term memory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:  # pragma: no cover - exercised only on missing dependency.
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageOps = None


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
RESAMPLE_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None) if Image else None
SIZE_RE = re.compile(r"^(\d+)x(\d+)$")
DATE_RE = re.compile(r"^\d{8}$")
LANG_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]+)?$", re.IGNORECASE)
CREATIVE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)
RTL_LANGS = {"ar", "fa", "he", "ur"}
QA_CHECKS = [
    "copy_accuracy",
    "brand_preservation",
    "crop_safety",
    "layout_legibility",
    "rtl_flow",
    "culture_awareness",
]
DEFAULT_PROFILES = Path(__file__).resolve().parents[1] / "references" / "delivery_profiles.json"


def require_pillow() -> None:
    if Image is None:
        raise SystemExit(
            "This helper requires Pillow. Run scripts/setup.sh, or: python3 -m pip install pillow"
        )


def parse_size(value: str) -> tuple[int, int]:
    match = SIZE_RE.match(value.lower())
    if not match:
        raise argparse.ArgumentTypeError("size must use WIDTHxHEIGHT, e.g. 1200x628")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("width and height must be positive")
    return width, height


def size_text(size: tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"


def same_ratio(left: tuple[int, int], right: tuple[int, int], tolerance: float = 0.01) -> bool:
    left_ratio = left[0] / left[1]
    right_ratio = right[0] / right[1]
    return abs(left_ratio - right_ratio) / left_ratio <= tolerance


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_languages(raw_languages: Any) -> list[dict[str, str | None]]:
    if not isinstance(raw_languages, list) or not raw_languages:
        raise SystemExit("job.languages must be a non-empty list")
    languages = []
    for entry in raw_languages:
        if isinstance(entry, str):
            locale, market = entry.lower(), None
        elif isinstance(entry, dict):
            locale = str(entry.get("locale", "")).lower()
            market = entry.get("market")
        else:
            raise SystemExit("each language must be a locale string or an object with locale/market")
        if not LANG_RE.match(locale):
            raise SystemExit(f"invalid language locale: {locale or entry}")
        languages.append({"locale": locale, "market": str(market) if market else None})
    return languages


def plan_job(job: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    """Resolve a job into unique model generations and deterministic derivatives."""

    creative = str(job.get("creative_slug", ""))
    if not CREATIVE_RE.match(creative):
        raise SystemExit("job.creative_slug must use English letters, digits, hyphen, or underscore")
    if not job.get("source"):
        raise SystemExit("job.source is required")
    languages = normalize_languages(job.get("languages"))
    formats = profiles.get("formats", {})
    profile_name = job.get("profile", profiles.get("default_profile"))

    if "formats" in job:
        requested = list(dict.fromkeys(job["formats"]))
    else:
        profile = profiles.get("profiles", {}).get(profile_name)
        if not profile:
            raise SystemExit(f"unknown delivery profile: {profile_name}")
        requested = list(dict.fromkeys(profile.get("formats", [])))
    if not requested:
        raise SystemExit("job must resolve to at least one format")
    unknown = [name for name in requested if name not in formats]
    if unknown:
        raise SystemExit(f"unknown format(s): {', '.join(unknown)}")

    # A generated anchor used by a derivative is also a user deliverable.
    delivery_formats = list(requested)
    for name in list(delivery_formats):
        source_format = formats[name].get("source_format")
        if source_format and source_format not in delivery_formats:
            delivery_formats.insert(delivery_formats.index(name), source_format)

    date = str(job.get("date") or dt.date.today().strftime("%Y%m%d"))
    if not DATE_RE.match(date):
        raise SystemExit("job.date must use yyyymmdd")
    output_spec = job.get("output", {})
    if output_spec and not isinstance(output_spec, dict):
        raise SystemExit("job.output must be an object with dir and extension")
    output_dir = str(output_spec.get("dir", job.get("output_dir", "final")))
    extension = str(output_spec.get("extension", job.get("output_extension", "png"))).lower().lstrip(".")
    if f".{extension}" not in IMAGE_EXTS:
        raise SystemExit("job output extension must be png, jpg, jpeg, or webp")
    operations: list[dict[str, Any]] = []
    deliverables: list[dict[str, Any]] = []
    conflicts: list[str] = []

    for language in languages:
        locale = str(language["locale"])
        by_format: dict[str, dict[str, Any]] = {}
        for format_name in delivery_formats:
            spec = formats[format_name]
            output_size = spec["default_size"]
            filename = f"{creative}_{locale}_{output_size}_{date}.{extension}"
            method = spec["production_method"]
            item: dict[str, Any] = {
                "locale": locale,
                "market": language["market"],
                "format": format_name,
                "aspect_ratio": spec["aspect_ratio"],
                "output_size": output_size,
                "production_method": method,
                "output_file": str(Path(output_dir) / filename),
            }
            if method == "model_native":
                item["generation_key"] = f"{locale}:{format_name}"
            elif method == "html_render":
                item["render_key"] = f"{locale}:{output_size}"
            elif method == "derived_crop":
                source_format = spec["source_format"]
                source = by_format.get(source_format)
                if not source:
                    raise SystemExit(f"format {format_name} needs unresolved source {source_format}")
                item["derived_crop_from"] = source["output_file"]
            else:
                raise SystemExit(f"unsupported production_method for {format_name}: {method}")
            operations.append(dict(item))
            deliverables.append(dict(item))
            by_format[format_name] = item

        for extra in job.get("extra_exports", []):
            if not isinstance(extra, dict) or extra.get("format") not in by_format or not extra.get("size"):
                raise SystemExit("each extra_export needs an existing format and size")
            format_name = str(extra["format"])
            source = by_format[format_name]
            target_size = parse_size(str(extra["size"]))
            source_size = parse_size(source["output_size"])
            if not same_ratio(source_size, target_size):
                raise SystemExit(
                    f"extra export {size_text(target_size)} does not match {format_name} ratio; request another format"
                )
            if target_size == source_size:
                continue
            filename = f"{creative}_{locale}_{size_text(target_size)}_{date}.{extension}"
            item = {
                "locale": locale,
                "market": language["market"],
                "format": format_name,
                "aspect_ratio": source["aspect_ratio"],
                "output_size": size_text(target_size),
                "production_method": "normalized_resize",
                "normalized_from": source["output_file"],
                "output_file": str(Path(output_dir) / filename),
            }
            operations.append(dict(item))
            deliverables.append(dict(item))

    output_files = [item["output_file"] for item in deliverables]
    for filename in sorted(set(output_files)):
        if output_files.count(filename) > 1:
            conflicts.append(f"duplicate output path: {filename}")

    counts = {
        method: sum(item["production_method"] == method for item in operations)
        for method in ("html_render", "model_native", "derived_crop", "normalized_resize")
    }
    return {
        "schema_version": 1,
        "profile": profile_name,
        "job": {
            "creative_slug": creative,
            "source": job["source"],
            "languages": languages,
            "protected_terms": job.get("protected_terms", []),
            "rtl_mode": job.get("rtl_mode", "auto"),
            "output_dir": output_dir,
            "output_extension": extension,
            "date": date,
        },
        "summary": {
            **counts,
            "deliverables": len(deliverables),
            "conflicts": len(conflicts),
        },
        "conflicts": conflicts,
        "operations": operations,
        "deliverables": deliverables,
    }


def command_preflight(args: argparse.Namespace) -> int:
    job = read_json(Path(args.job))
    profiles = read_json(Path(args.profiles))
    plan = plan_job(job, profiles)
    if args.output:
        write_json(Path(args.output), plan)
        print(f"Wrote {args.output} with {plan['summary']['deliverables']} deliverables.")
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 1 if plan["conflicts"] else 0


def image_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_EXTS
        and not path.name.startswith("qa_contact_sheet")
    )


def parse_delivery_name(path: Path) -> dict[str, Any]:
    """Parse <creative>_<lang>_<width>x<height>_<yyyymmdd>.<ext>.

    The creative slug may contain underscores; parsing works from the right.
    """

    parts = path.stem.rsplit("_", 3)
    result: dict[str, Any] = {
        "creative": None,
        "language": None,
        "expected_width": None,
        "expected_height": None,
        "date": None,
        "filename_valid": False,
        "filename_issues": [],
    }
    if len(parts) != 4:
        result["filename_issues"].append(
            "filename must be <creative>_<language>_<width>x<height>_<yyyymmdd>"
        )
        return result

    creative, language, size_text, date_text = parts
    result["creative"] = creative
    result["language"] = language.lower()
    result["date"] = date_text

    if not CREATIVE_RE.match(creative):
        result["filename_issues"].append("creative slug should use English letters, digits, hyphen, or underscore")
    if not LANG_RE.match(language):
        result["filename_issues"].append("language should be a short locale code such as en, de, ja, ar, pt-br")
    size_match = SIZE_RE.match(size_text.lower())
    if size_match:
        result["expected_width"] = int(size_match.group(1))
        result["expected_height"] = int(size_match.group(2))
    else:
        result["filename_issues"].append("resolution should use WIDTHxHEIGHT")
    if not DATE_RE.match(date_text):
        result["filename_issues"].append("date should use yyyymmdd")

    result["filename_valid"] = not result["filename_issues"]
    return result


def inspect_image(path: Path) -> tuple[int, int]:
    require_pillow()
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        return image.size


def analyze_file(path: Path, default_status: str = "pending_visual_qa") -> dict[str, Any]:
    parsed = parse_delivery_name(path)
    issues = list(parsed["filename_issues"])
    width = height = None

    try:
        width, height = inspect_image(path)
    except Exception as exc:  # noqa: BLE001 - surface file-specific issue.
        issues.append(f"cannot read image: {exc}")

    expected_width = parsed["expected_width"]
    expected_height = parsed["expected_height"]
    if width and height and expected_width and expected_height:
        if (width, height) != (expected_width, expected_height):
            issues.append(
                f"dimension mismatch: file is {width}x{height}, filename says {expected_width}x{expected_height}"
            )

    qa_status = "needs_review" if issues else default_status
    return {
        "file": path.name,
        "creative": parsed["creative"],
        "language": parsed["language"],
        "resolution": f"{width}x{height}" if width and height else None,
        "expected_resolution": (
            f"{expected_width}x{expected_height}" if expected_width and expected_height else None
        ),
        "date": parsed["date"],
        "qa_status": qa_status,
        "issues": issues,
    }


def crop_offsets(
    resized_width: int,
    resized_height: int,
    target_width: int,
    target_height: int,
    gravity: str,
) -> tuple[int, int]:
    x_overflow = resized_width - target_width
    y_overflow = resized_height - target_height

    if "west" in gravity:
        left = 0
    elif "east" in gravity:
        left = x_overflow
    else:
        left = x_overflow // 2

    if "north" in gravity:
        top = 0
    elif "south" in gravity:
        top = y_overflow
    else:
        top = y_overflow // 2

    return max(0, left), max(0, top)


def command_cover_crop(args: argparse.Namespace) -> int:
    require_pillow()
    source = Path(args.input)
    output = Path(args.output)
    target_width, target_height = args.size

    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        source_width, source_height = image.size
        scale = max(target_width / source_width, target_height / source_height)
        resized_width = round(source_width * scale)
        resized_height = round(source_height * scale)
        left, top = crop_offsets(
            resized_width, resized_height, target_width, target_height, args.gravity
        )
        crop_box = (left, top, left + target_width, top + target_height)

        plan = {
            "input": str(source),
            "output": str(output),
            "source_size": f"{source_width}x{source_height}",
            "target_size": f"{target_width}x{target_height}",
            "scale": scale,
            "resized_size": f"{resized_width}x{resized_height}",
            "crop_box": crop_box,
            "gravity": args.gravity,
            "non_uniform_resize": False,
            "blur_padding": False,
        }

        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0

        resized = image.resize((resized_width, resized_height), RESAMPLE_LANCZOS)
        cropped = resized.crop(crop_box)
        output.parent.mkdir(parents=True, exist_ok=True)

        save_kwargs: dict[str, Any] = {}
        suffix = output.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            cropped = cropped.convert("RGB")
            save_kwargs["quality"] = args.quality
            save_kwargs["optimize"] = True
        elif suffix == ".webp":
            save_kwargs["quality"] = args.quality
        cropped.save(output, **save_kwargs)

    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def command_normalize_size(args: argparse.Namespace) -> int:
    """Resize within one aspect-ratio family; refuse crop or distortion."""

    require_pillow()
    source = Path(args.input)
    output = Path(args.output)
    target_size = args.size
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if not same_ratio(image.size, target_size, args.tolerance):
            raise SystemExit(
                f"source {size_text(image.size)} and target {size_text(target_size)} are not the same ratio"
            )
        plan = {
            "input": str(source),
            "output": str(output),
            "source_size": size_text(image.size),
            "target_size": size_text(target_size),
            "production_method": "normalized_resize",
            "non_uniform_resize": False,
            "crop": False,
        }
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        resized = image.resize(target_size, RESAMPLE_LANCZOS)
        output.parent.mkdir(parents=True, exist_ok=True)
        save_kwargs: dict[str, Any] = {}
        if output.suffix.lower() in {".jpg", ".jpeg"}:
            resized = resized.convert("RGB")
            save_kwargs = {"quality": args.quality, "optimize": True}
        elif output.suffix.lower() == ".webp":
            save_kwargs = {"quality": args.quality}
        resized.save(output, **save_kwargs)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def command_manifest(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    output = Path(args.output) if args.output else folder / "manifest.json"
    items = [analyze_file(path, args.qa_status) for path in image_files(folder)]
    provenance: dict[str, dict[str, Any]] = {}
    plan_path = getattr(args, "plan", None)
    if plan_path:
        plan = read_json(Path(plan_path))
        for deliverable in plan.get("deliverables", []):
            provenance[Path(deliverable["output_file"]).name] = deliverable
    for item in items:
        planned = provenance.get(item["file"])
        if not planned:
            continue
        for key in (
            "format",
            "aspect_ratio",
            "production_method",
            "generation_key",
            "render_key",
            "derived_crop_from",
            "normalized_from",
        ):
            if key in planned:
                value = planned[key]
                if key.endswith("_from"):
                    value = Path(value).name
                item[key] = value
    manifest = {
        "schema_version": 2,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "folder": str(folder),
        "preflight_plan": str(plan_path) if plan_path else None,
        "items": items,
    }
    write_json(output, manifest)
    print(f"Wrote {output} with {len(items)} items.")
    return 0


def command_qa_init(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    output = Path(args.output) if args.output else folder / "visual_review.json"
    plan_by_name: dict[str, dict[str, Any]] = {}
    if args.plan:
        plan = read_json(Path(args.plan))
        plan_by_name = {
            Path(item["output_file"]).name: item for item in plan.get("deliverables", [])
        }
    items = []
    for path in image_files(folder):
        parsed = parse_delivery_name(path)
        locale = str(parsed.get("language") or "")
        base_language = locale.split("-", 1)[0]
        checks = {name: "pending" for name in QA_CHECKS}
        if base_language not in RTL_LANGS:
            checks["rtl_flow"] = "not_applicable"
        planned = plan_by_name.get(path.name, {})
        items.append(
            {
                "file": path.name,
                "locale": locale or None,
                "market": planned.get("market"),
                "production_method": planned.get("production_method"),
                "overall_status": "pending",
                "checks": checks,
                "notes": "",
                "risk_acknowledged": False,
            }
        )
    review = {
        "schema_version": 1,
        "folder": str(folder),
        "instructions": (
            "Record an actual visual review. Use pass, fail, or not_applicable for every check; "
            "set overall_status to approved, rejected, or flagged. Flagged requires notes and explicit risk acknowledgement."
        ),
        "items": items,
    }
    write_json(output, review)
    print(f"Wrote {output} with {len(items)} pending visual reviews.")
    return 0


def evaluate_release(folder: Path, review: dict[str, Any]) -> dict[str, Any]:
    paths = image_files(folder)
    actual_files = {path.name for path in paths}
    review_items = review.get("items", [])
    by_name = {item.get("file"): item for item in review_items if item.get("file")}
    issues = []
    if not actual_files:
        issues.append({"file": None, "issue": "no image outputs found"})
    for path in paths:
        for structural_issue in analyze_file(path, "ok")["issues"]:
            issues.append({"file": path.name, "issue": structural_issue})
    for filename in sorted(actual_files - set(by_name)):
        issues.append({"file": filename, "issue": "missing visual review"})
    for filename in sorted(set(by_name) - actual_files):
        issues.append({"file": filename, "issue": "review references a missing output"})
    allowed_checks = {"pass", "not_applicable"}
    for filename in sorted(actual_files & set(by_name)):
        item = by_name[filename]
        checks = item.get("checks", {})
        for check in QA_CHECKS:
            status = checks.get(check, "missing")
            if status not in allowed_checks:
                issues.append({"file": filename, "issue": f"{check} is {status}"})
        overall = item.get("overall_status")
        if overall == "approved":
            continue
        if overall == "flagged" and item.get("risk_acknowledged") and str(item.get("notes", "")).strip():
            continue
        issues.append({"file": filename, "issue": f"overall_status is {overall or 'missing'}"})
    return {
        "folder": str(folder),
        "total_outputs": len(actual_files),
        "reviewed_outputs": len(actual_files & set(by_name)),
        "release_ready": not issues,
        "issues": issues,
    }


def command_release_check(args: argparse.Namespace) -> int:
    summary = evaluate_release(Path(args.folder), read_json(Path(args.review)))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        verdict = "READY" if summary["release_ready"] else "BLOCKED"
        print(f"Release {verdict}: {summary['reviewed_outputs']}/{summary['total_outputs']} outputs reviewed.")
        for issue in summary["issues"]:
            print(f"- {issue['file']}: {issue['issue']}")
    return 0 if summary["release_ready"] else 1


def command_verify(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    items = [analyze_file(path, args.qa_status) for path in image_files(folder)]
    failed = [item for item in items if item["issues"]]
    summary = {
        "folder": str(folder),
        "total": len(items),
        "passed": len(items) - len(failed),
        "failed": len(failed),
        "items": items,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"Checked {summary['total']} image(s): {summary['passed']} passed, {summary['failed']} failed.")
        for item in failed:
            print(f"- {item['file']}")
            for issue in item["issues"]:
                print(f"  - {issue}")
    return 1 if failed and not args.no_fail else 0


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}__flagged-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def culture_aware_scope_files(folder: Path, selected: list[Path], scope: str) -> list[Path]:
    if scope == "selected":
        return selected

    scoped: dict[Path, None] = {path: None for path in selected}
    all_files = image_files(folder)
    for path in selected:
        parsed = parse_delivery_name(path)
        creative = parsed.get("creative")
        language = parsed.get("language")
        date = parsed.get("date")
        if not creative or not language or not date:
            continue
        for candidate in all_files:
            candidate_parsed = parse_delivery_name(candidate)
            if (
                candidate_parsed.get("creative") == creative
                and candidate_parsed.get("language") == language
                and candidate_parsed.get("date") == date
            ):
                scoped[candidate] = None
    return sorted(scoped)


def command_flag_culture_aware(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if not folder.exists() or not folder.is_dir():
        raise SystemExit(f"Folder not found: {folder}")

    selected = []
    for file_name in args.files:
        path = Path(file_name)
        if not path.is_absolute():
            path = folder / path
        path = path.resolve()
        try:
            path.relative_to(folder.resolve())
        except ValueError as exc:
            raise SystemExit(f"Refusing to move file outside folder: {file_name}") from exc
        if not path.exists() or not path.is_file():
            raise SystemExit(f"File not found: {path}")
        if path.suffix.lower() not in IMAGE_EXTS:
            raise SystemExit(f"Not an image file: {path}")
        selected.append(path)

    files = culture_aware_scope_files(folder.resolve(), selected, args.scope)
    destination_folder = folder / args.destination
    records = []
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    for source in files:
        destination = unique_destination(destination_folder / source.name)
        record = {
            "file": destination.name,
            "original_file": source.name,
            "market": args.market,
            "reason": args.reason,
            "source_path": str(source),
            "destination_path": str(destination),
            "flagged_at": now,
        }
        records.append(record)
        if not args.dry_run:
            destination_folder.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))

    if not args.dry_run:
        log_path = destination_folder / "culture_aware_qa_flags.json"
        existing = []
        if log_path.exists():
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        existing.extend(records)
        log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "folder": str(folder),
        "destination": str(destination_folder),
        "scope": args.scope,
        "dry_run": args.dry_run,
        "flagged": records,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def load_font(size: int, bold: bool = False) -> Any:
    require_pillow()
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def fit_text_lines(
    draw: Any,
    value: str,
    font: Any,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    """Fit long unbroken filenames into a fixed-width card without overlap."""

    def width(text: str) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]

    def fitted_prefix(text: str, suffix: str = "") -> str:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if width(text[:middle] + suffix) <= max_width:
                low = middle
            else:
                high = middle - 1
        return text[:low]

    remaining = value
    lines: list[str] = []
    while remaining and len(lines) < max_lines:
        if width(remaining) <= max_width:
            lines.append(remaining)
            break
        is_last = len(lines) == max_lines - 1
        if is_last:
            lines.append(fitted_prefix(remaining, "…") + "…")
            break
        prefix = fitted_prefix(remaining)
        break_at = max(prefix.rfind("_"), prefix.rfind("-"))
        if break_at >= max(1, len(prefix) // 2):
            prefix = prefix[: break_at + 1]
        lines.append(prefix)
        remaining = remaining[len(prefix) :]
    return lines


def command_contact_sheet(args: argparse.Namespace) -> int:
    require_pillow()
    folder = Path(args.folder)
    output = Path(args.output)
    thumb_width, thumb_height = args.thumb_size
    files = image_files(folder)
    if not files:
        raise SystemExit(f"No image files found in {folder}")

    cols = max(1, args.cols)
    rows = (len(files) + cols - 1) // cols
    padding = 24
    label_height = 82
    cell_width = thumb_width + padding * 2
    cell_height = thumb_height + label_height + padding * 2
    sheet = Image.new("RGB", (cell_width * cols, cell_height * rows), args.background)
    draw = ImageDraw.Draw(sheet)
    label_font = load_font(18, bold=True)
    meta_font = load_font(14)

    for index, path in enumerate(files):
        col = index % cols
        row = index // cols
        x = col * cell_width + padding
        y = row * cell_height + padding
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            original_size = image.size
            image.thumbnail((thumb_width, thumb_height), RESAMPLE_LANCZOS)
            thumb_x = x + (thumb_width - image.width) // 2
            thumb_y = y + (thumb_height - image.height) // 2
            draw.rounded_rectangle(
                (x - 8, y - 8, x + thumb_width + 8, y + thumb_height + 8),
                radius=14,
                fill="#ffffff",
                outline="#d7deea",
            )
            sheet.paste(image, (thumb_x, thumb_y))
        label_y = y + thumb_height + 14
        label_lines = fit_text_lines(draw, path.name, label_font, thumb_width, max_lines=2)
        for line_index, line in enumerate(label_lines):
            draw.text((x, label_y + line_index * 21), line, fill="#1f2937", font=label_font)
        draw.text(
            (x, label_y + 48),
            f"{original_size[0]}x{original_size[1]}",
            fill="#64748b",
            font=meta_font,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".jpg", ".jpeg"}:
        sheet.save(output, quality=args.quality, optimize=True)
    else:
        sheet.save(output)
    print(f"Wrote {output} for {len(files)} image(s).")
    return 0


def load_memory(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "brands": {}}


def resolve_term_rules(
    memory: dict[str, Any],
    brand: str | None = None,
    product: str | None = None,
    job_rules: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve rules by term: brand < product < explicit current-job rule."""

    resolved: dict[str, dict[str, Any]] = {}
    brand_data = memory.get("brands", {}).get(brand, {}) if brand else {}
    layers = [brand_data.get("rules", [])]
    if product:
        layers.append(brand_data.get("products", {}).get(product, {}).get("rules", []))
    layers.append(job_rules or [])
    for layer in layers:
        for rule in layer:
            term = rule.get("term")
            if term:
                resolved[str(term).casefold()] = dict(rule)
    return list(resolved.values())


def command_memory_add(args: argparse.Namespace) -> int:
    path = Path(args.memory_file)
    memory = load_memory(path)
    brands = memory.setdefault("brands", {})
    brand = brands.setdefault(
        args.brand,
        {"display_name": args.brand_display or args.brand, "rules": [], "products": {}},
    )
    if args.brand_display:
        brand["display_name"] = args.brand_display

    target = brand
    if args.product:
        products = brand.setdefault("products", {})
        target = products.setdefault(
            args.product,
            {"display_name": args.product_display or args.product, "rules": []},
        )
        if args.product_display:
            target["display_name"] = args.product_display

    rule = {
        "term": args.term,
        "action": args.action,
    }
    if args.translation:
        rule["translation"] = args.translation
    if args.notes:
        rule["notes"] = args.notes

    rules = target.setdefault("rules", [])
    existing = next((item for item in rules if item.get("term") == args.term), None)
    if existing:
        existing.update(rule)
    else:
        rules.append(rule)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    scope = f"{args.brand}/{args.product}" if args.product else args.brand
    print(f"Saved terminology rule for {scope}: {args.term} -> {args.action}")
    return 0


def command_memory_list(args: argparse.Namespace) -> int:
    path = Path(args.memory_file)
    print(json.dumps(load_memory(path), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministic last-mile helpers for ad-image-localization outputs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser(
        "preflight", help="Resolve a job into generations, derivatives, and deliverables."
    )
    preflight.add_argument("job", help="Job JSON file.")
    preflight.add_argument("--profiles", default=str(DEFAULT_PROFILES))
    preflight.add_argument("--output", help="Write the plan to JSON instead of stdout.")
    preflight.set_defaults(func=command_preflight)

    cover = subparsers.add_parser("cover-crop", help="Aspect-preserving resize and crop.")
    cover.add_argument("input")
    cover.add_argument("output")
    cover.add_argument("--size", type=parse_size, required=True, help="Target size, e.g. 1200x628.")
    cover.add_argument(
        "--gravity",
        choices=[
            "center",
            "north",
            "south",
            "east",
            "west",
            "northwest",
            "northeast",
            "southwest",
            "southeast",
        ],
        default="center",
        help="Crop anchor. Use north/south/east/west when QA shows safer empty space.",
    )
    cover.add_argument("--quality", type=int, default=95)
    cover.add_argument("--dry-run", action="store_true")
    cover.set_defaults(func=command_cover_crop)

    normalize = subparsers.add_parser(
        "normalize-size", help="Resize within the same aspect-ratio family without cropping."
    )
    normalize.add_argument("input")
    normalize.add_argument("output")
    normalize.add_argument("--size", type=parse_size, required=True)
    normalize.add_argument("--tolerance", type=float, default=0.01)
    normalize.add_argument("--quality", type=int, default=95)
    normalize.add_argument("--dry-run", action="store_true")
    normalize.set_defaults(func=command_normalize_size)

    manifest = subparsers.add_parser("manifest", help="Write manifest.json for a folder.")
    manifest.add_argument("folder")
    manifest.add_argument("--output", help="Defaults to <folder>/manifest.json.")
    manifest.add_argument("--qa-status", default="pending_visual_qa")
    manifest.add_argument("--plan", help="Optional preflight JSON used to attach provenance.")
    manifest.set_defaults(func=command_manifest)

    qa_init = subparsers.add_parser(
        "qa-init", help="Create a machine-readable visual-review checklist."
    )
    qa_init.add_argument("folder")
    qa_init.add_argument("--output", help="Defaults to <folder>/visual_review.json.")
    qa_init.add_argument("--plan", help="Optional preflight JSON for market and provenance context.")
    qa_init.set_defaults(func=command_qa_init)

    release = subparsers.add_parser(
        "release-check", help="Block release until recorded visual QA is complete."
    )
    release.add_argument("folder")
    release.add_argument("--review", required=True, help="Completed visual_review.json.")
    release.add_argument("--json", action="store_true")
    release.set_defaults(func=command_release_check)

    verify = subparsers.add_parser("verify", help="Check filenames and dimensions.")
    verify.add_argument("folder")
    verify.add_argument("--qa-status", default="ok")
    verify.add_argument("--json", action="store_true")
    verify.add_argument("--no-fail", action="store_true", help="Return 0 even if issues are found.")
    verify.set_defaults(func=command_verify)

    sheet = subparsers.add_parser("contact-sheet", help="Create a visual QA contact sheet.")
    sheet.add_argument("folder")
    sheet.add_argument("output")
    sheet.add_argument("--cols", type=int, default=4)
    sheet.add_argument("--thumb-size", type=parse_size, default=parse_size("280x180"))
    sheet.add_argument("--background", default="#f7f8fb")
    sheet.add_argument("--quality", type=int, default=92)
    sheet.set_defaults(func=command_contact_sheet)

    culture_aware = subparsers.add_parser(
        "flag-culture-aware",
        help="Move Culture-Aware QA flagged outputs into a review folder without editing them.",
    )
    culture_aware.add_argument("folder")
    culture_aware.add_argument("files", nargs="+", help="Image file names or paths to flag.")
    culture_aware.add_argument("--destination", default="Flagged by Culture-Aware QA")
    culture_aware.add_argument("--market", default="unspecified")
    culture_aware.add_argument("--reason", required=True)
    culture_aware.add_argument(
        "--scope",
        choices=["variant", "selected"],
        default="variant",
        help="variant moves all sizes with the same creative/language/date; selected moves only named files.",
    )
    culture_aware.add_argument("--dry-run", action="store_true")
    culture_aware.set_defaults(func=command_flag_culture_aware)

    mem_add = subparsers.add_parser("memory-add", help="Add or update a terminology memory rule.")
    mem_add.add_argument("memory_file")
    mem_add.add_argument("--brand", required=True, help="Brand slug, e.g. openai.")
    mem_add.add_argument("--brand-display")
    mem_add.add_argument("--product", help="Optional product slug, e.g. codex.")
    mem_add.add_argument("--product-display")
    mem_add.add_argument("--term", required=True)
    mem_add.add_argument("--action", choices=["preserve", "translate"], required=True)
    mem_add.add_argument("--translation")
    mem_add.add_argument("--notes")
    mem_add.set_defaults(func=command_memory_add)

    mem_list = subparsers.add_parser("memory-list", help="Print terminology memory JSON.")
    mem_list.add_argument("memory_file")
    mem_list.set_defaults(func=command_memory_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
