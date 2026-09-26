from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "ad-image-localization"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import ad_image_localization_tools as tools  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover - local environment without requirements.
    Image = None


class DeliveryNameTests(unittest.TestCase):
    def test_parse_delivery_name_allows_underscores_in_creative_slug(self) -> None:
        parsed = tools.parse_delivery_name(Path("rabbit_social_networks_pt-br_1200x628_20260621.jpg"))

        self.assertTrue(parsed["filename_valid"])
        self.assertEqual(parsed["creative"], "rabbit_social_networks")
        self.assertEqual(parsed["language"], "pt-br")
        self.assertEqual(parsed["expected_width"], 1200)
        self.assertEqual(parsed["expected_height"], 628)
        self.assertEqual(parsed["date"], "20260621")

    def test_parse_delivery_name_reports_invalid_pattern(self) -> None:
        parsed = tools.parse_delivery_name(Path("demo-localization-grid.png"))

        self.assertFalse(parsed["filename_valid"])
        self.assertIn("filename must be", parsed["filename_issues"][0])

    def test_parse_size(self) -> None:
        self.assertEqual(tools.parse_size("1200x628"), (1200, 628))
        with self.assertRaises(argparse.ArgumentTypeError):
            tools.parse_size("1200-628")


class CoverCropTests(unittest.TestCase):
    def test_center_crop_offsets_for_1200x628_from_16x9(self) -> None:
        self.assertEqual(tools.crop_offsets(1200, 675, 1200, 628, "center"), (0, 23))

    def test_gravity_offsets(self) -> None:
        self.assertEqual(tools.crop_offsets(1400, 900, 1200, 628, "northwest"), (0, 0))
        self.assertEqual(tools.crop_offsets(1400, 900, 1200, 628, "southeast"), (200, 272))

    @unittest.skipIf(Image is None, "Pillow is required for image tests")
    def test_cover_crop_preserves_aspect_and_writes_target_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            source = tmp / "source.png"
            output = tmp / "rabbit-social-networks_en_1200x628_20260621.jpg"
            Image.new("RGB", (1920, 1080), "#87ceeb").save(source)

            args = argparse.Namespace(
                input=str(source),
                output=str(output),
                size=(1200, 628),
                gravity="center",
                quality=95,
                dry_run=False,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_cover_crop(args), 0)

            with Image.open(output) as image:
                self.assertEqual(image.size, (1200, 628))

    @unittest.skipIf(Image is None, "Pillow is required for image tests")
    def test_normalize_size_accepts_same_ratio_and_rejects_another_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            source = tmp / "square.png"
            output = tmp / "square-1080.png"
            Image.new("RGB", (1200, 1200), "#87ceeb").save(source)
            args = argparse.Namespace(
                input=str(source), output=str(output), size=(1080, 1080), tolerance=0.01,
                quality=95, dry_run=False,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_normalize_size(args), 0)
            with Image.open(output) as image:
                self.assertEqual(image.size, (1080, 1080))
            args.size = (1200, 628)
            with self.assertRaises(SystemExit):
                tools.command_normalize_size(args)


class PreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profiles = json.loads((SKILL_ROOT / "references" / "delivery_profiles.json").read_text())

    def test_eval_cases_match_operation_counts(self) -> None:
        cases = json.loads((ROOT / "evals" / "cases.json").read_text())["cases"]
        for case in cases:
            with self.subTest(case=case["name"]):
                plan = tools.plan_job(case["job"], self.profiles)
                self.assertEqual(plan["summary"], {**case["expected"], "conflicts": 0})

    def test_1200x628_is_a_direct_render_without_16x9_anchor(self) -> None:
        job = {
            "creative_slug": "launch",
            "source": "source.png",
            "languages": ["en"],
            "formats": ["social-link-1.91x1"],
            "date": "20260714",
        }
        plan = tools.plan_job(job, self.profiles)
        self.assertEqual(plan["summary"]["html_render"], 1)
        self.assertEqual(plan["summary"]["derived_crop"], 0)
        self.assertEqual(plan["summary"]["deliverables"], 1)
        self.assertEqual(plan["deliverables"][0]["output_size"], "1200x628")

    def test_full_pack_renders_every_size_once_per_language(self) -> None:
        job = {
            "creative_slug": "launch", "source": "source.png", "languages": ["en", "ar"],
            "profile": "full-pack", "date": "20260714",
        }
        plan = tools.plan_job(job, self.profiles)
        keys = [item["render_key"] for item in plan["operations"]]
        self.assertEqual(len(keys), 24)
        self.assertEqual(len(set(keys)), 24)
        self.assertIn("ar:320x50", keys)
        self.assertEqual(plan["summary"]["deliverables"], 24)



@unittest.skipIf(Image is None, "Pillow is required for image tests")
class BatchHelperTests(unittest.TestCase):
    def test_contact_sheet_filename_lines_stay_within_card_width(self) -> None:
        canvas = Image.new("RGB", (500, 200), "white")
        draw = tools.ImageDraw.Draw(canvas)
        font = tools.load_font(18, bold=True)
        lines = tools.fit_text_lines(
            draw,
            "rabbit-social-networks-smoke_es_1200x1200_20260714.png",
            font,
            240,
            max_lines=2,
        )
        self.assertLessEqual(len(lines), 2)
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            self.assertLessEqual(box[2] - box[0], 240)

    def test_manifest_verify_and_contact_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            image_path = folder / "rabbit-social-networks_en_1200x628_20260621.jpg"
            Image.new("RGB", (1200, 628), "#ffffff").save(image_path)

            manifest_args = argparse.Namespace(
                folder=str(folder),
                output=None,
                qa_status="pending_visual_qa",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_manifest(manifest_args), 0)

            manifest = json.loads((folder / "manifest.json").read_text())
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(len(manifest["items"]), 1)
            self.assertEqual(manifest["items"][0]["qa_status"], "pending_visual_qa")
            self.assertEqual(manifest["items"][0]["issues"], [])

            verify_args = argparse.Namespace(
                folder=str(folder),
                qa_status="ok",
                json=True,
                no_fail=False,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_verify(verify_args), 0)

            sheet_path = folder / "qa_contact_sheet.jpg"
            sheet_args = argparse.Namespace(
                folder=str(folder),
                output=str(sheet_path),
                cols=1,
                thumb_size=(240, 160),
                background="#f7f8fb",
                quality=90,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_contact_sheet(sheet_args), 0)
            self.assertTrue(sheet_path.exists())

    def test_flag_culture_aware_moves_matching_variant_sizes_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            files = [
                folder / "rabbit-social-networks_ar_1200x628_20260621.jpg",
                folder / "rabbit-social-networks_ar_1200x1200_20260621.jpg",
                folder / "rabbit-social-networks_de_1200x628_20260621.jpg",
            ]
            for path in files:
                Image.new("RGB", (1200, 628), "#ffffff").save(path)

            args = argparse.Namespace(
                folder=str(folder),
                files=["rabbit-social-networks_ar_1200x628_20260621.jpg"],
                destination="Flagged by Culture-Aware QA",
                market="GCC",
                reason="Needs local cultural review",
                scope="variant",
                dry_run=False,
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_flag_culture_aware(args), 0)

            flagged_folder = folder / "Flagged by Culture-Aware QA"
            self.assertTrue((flagged_folder / "rabbit-social-networks_ar_1200x628_20260621.jpg").exists())
            self.assertTrue((flagged_folder / "rabbit-social-networks_ar_1200x1200_20260621.jpg").exists())
            self.assertTrue((folder / "rabbit-social-networks_de_1200x628_20260621.jpg").exists())
            self.assertFalse((folder / "rabbit-social-networks_ar_1200x628_20260621.jpg").exists())

            log = json.loads((flagged_folder / "culture_aware_qa_flags.json").read_text())
            self.assertEqual(len(log), 2)
            self.assertEqual(log[0]["market"], "GCC")
            self.assertEqual(log[0]["reason"], "Needs local cultural review")

    def test_manifest_provenance_and_visual_qa_release_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            folder = root / "final"
            folder.mkdir()
            image_path = folder / "launch_en_1200x628_20260714.png"
            Image.new("RGB", (1200, 628), "#ffffff").save(image_path)
            plan_path = root / "preflight.json"
            plan = {
                "deliverables": [{
                    "output_file": str(image_path), "format": "social-link-1.91x1",
                    "aspect_ratio": "300:157", "production_method": "derived_crop",
                    "derived_crop_from": str(folder / "launch_en_1920x1080_20260714.png"),
                }]
            }
            plan_path.write_text(json.dumps(plan))
            manifest_path = root / "manifest.json"
            manifest_args = argparse.Namespace(
                folder=str(folder), output=str(manifest_path), qa_status="pending_visual_qa",
                plan=str(plan_path),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_manifest(manifest_args), 0)
            item = json.loads(manifest_path.read_text())["items"][0]
            self.assertEqual(item["production_method"], "derived_crop")
            self.assertEqual(item["derived_crop_from"], "launch_en_1920x1080_20260714.png")

            review_path = root / "visual_review.json"
            qa_args = argparse.Namespace(folder=str(folder), output=str(review_path), plan=str(plan_path))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_qa_init(qa_args), 0)
            review = json.loads(review_path.read_text())
            self.assertFalse(tools.evaluate_release(folder, review)["release_ready"])
            review_item = review["items"][0]
            review_item["checks"] = {name: "pass" for name in tools.QA_CHECKS}
            review_item["checks"]["rtl_flow"] = "not_applicable"
            review_item["overall_status"] = "approved"
            self.assertTrue(tools.evaluate_release(folder, review)["release_ready"])

    def test_flagged_review_requires_notes_and_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir)
            path = folder / "launch_ar_1200x1200_20260714.png"
            Image.new("RGB", (1200, 1200), "#ffffff").save(path)
            item = {
                "file": path.name,
                "checks": {name: "pass" for name in tools.QA_CHECKS},
                "overall_status": "flagged", "notes": "", "risk_acknowledged": False,
            }
            self.assertFalse(tools.evaluate_release(folder, {"items": [item]})["release_ready"])
            item["notes"] = "Market reviewer accepted the documented risk."
            item["risk_acknowledged"] = True
            self.assertTrue(tools.evaluate_release(folder, {"items": [item]})["release_ready"])


class MemoryTests(unittest.TestCase):
    def test_memory_add_creates_brand_product_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_file = Path(tmpdir) / "brand_term_memory.json"
            args = argparse.Namespace(
                memory_file=str(memory_file),
                brand="openai",
                brand_display="OpenAI",
                product="codex",
                product_display="Codex",
                term="Codex",
                action="preserve",
                translation=None,
                notes="Product name",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tools.command_memory_add(args), 0)

            memory = json.loads(memory_file.read_text())
            product_rules = memory["brands"]["openai"]["products"]["codex"]["rules"]
            self.assertEqual(product_rules[0]["term"], "Codex")
            self.assertEqual(product_rules[0]["action"], "preserve")

    def test_term_rule_priority_is_brand_then_product_then_job(self) -> None:
        memory = {
            "brands": {"acme": {
                "rules": [{"term": "Launch", "action": "translate", "translation": "Brand"}],
                "products": {"pro": {"rules": [
                    {"term": "Launch", "action": "translate", "translation": "Product"}
                ]}},
            }}
        }
        rules = tools.resolve_term_rules(
            memory, "acme", "pro",
            [{"term": "Launch", "action": "preserve", "notes": "Current job wins"}],
        )
        self.assertEqual(rules, [{"term": "Launch", "action": "preserve", "notes": "Current job wins"}])


if __name__ == "__main__":
    unittest.main()
