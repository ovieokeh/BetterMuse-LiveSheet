"""Fault-injection tests for preparation boundaries, not musical heuristics."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from unittest.mock import patch

from livesheet.pdf_pipeline.artifacts import atomic_bytes, commit_result, digest, verify_result
from livesheet.pdf_pipeline.cache import read_cache_object
from livesheet.pdf_pipeline.config import JobConfig, Toolchain, job_exports
from livesheet.pdf_pipeline.stages import RepairStage, STAGES, run_stage, validate_order
from livesheet.pdf_rhythm_repair import Decline

XML = b'<score-partwise><part><measure number="1"/></part></score-partwise>'


class PipelineBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self):
        path = self.folder / "source.mxl"
        with ZipFile(path, "w") as z:
            z.writestr(
                "META-INF/container.xml",
                '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>',
            )
            z.writestr("score.xml", XML)
        return path

    def test_declined_stage_cannot_leak_partial_mapping_or_notation(self):
        source = self.source()
        target = self.folder / "target.xml"
        original = dict(measures=[dict(number=1)], notes=[dict(midi=60)])

        def decline(a, b, pdf, mapping):
            mapping["notes"][0]["midi"] = 61
            b.write_bytes(b"partial")
            raise Decline("ambiguous-source")

        updated, report, diagnostic = run_stage(
            STAGES[0], source, target, None, original, decline
        )
        self.assertEqual(original["notes"][0]["midi"], 60)
        self.assertEqual(updated["notes"][0]["midi"], 60)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(diagnostic["outcome"], "declined")
        self.assertEqual(report["declined"], "ambiguous-source")

    def test_bug_missing_output_and_changed_inventory_abort_candidate(self):
        source = self.source()
        for fault in ("exception", "missing", "inventory"):
            target = self.folder / (fault + ".xml")
            target.write_bytes(source.read_bytes())  # Retained output from a retry.

            def operation(a, b, pdf, mapping):
                if fault == "exception":
                    raise RuntimeError("bug")
                if fault == "inventory":
                    b.write_bytes(a.read_bytes())
                    mapping["measures"].append({})
                return mapping, {}

            with self.subTest(fault=fault), self.assertRaises(Exception):
                run_stage(
                    STAGES[0], source, target, None, dict(measures=[{}]), operation
                )

    def test_mutated_input_is_restored_and_rejected_even_on_decline(self):
        source = self.source()
        original = source.read_bytes()

        def faulty(a, b, pdf, mapping):
            a.write_bytes(b"damaged")
            raise Decline("uncertain")

        with self.assertRaisesRegex(ValueError, "mutated-input"):
            run_stage(STAGES[0], source, self.folder / "out.mxl", None, {}, faulty)
        self.assertEqual(source.read_bytes(), original)

    def test_mandatory_stage_decline_still_aborts_whole_candidate(self):
        source = self.source()

        def decline(*args):
            raise Decline("required-evidence-missing")

        with self.assertRaises(Decline):
            run_stage(
                STAGES[0],
                source,
                self.folder / "out.mxl",
                None,
                {},
                decline,
                decline_policy="abort",
            )

    def test_stage_order_is_explicit_and_dependency_checked(self):
        validate_order(STAGES)
        for stages in ((STAGES[1], STAGES[0]), (STAGES[0], STAGES[0])):
            with self.assertRaisesRegex(ValueError, "stage-order"):
                validate_order(stages)

    def test_job_settings_are_independent_and_immutable(self):
        from dataclasses import FrozenInstanceError, replace

        tools = Toolchain(Path("/a"), "b", Path("/c"), Path("/d"))
        first = job_exports(JobConfig(self.folder / "one", tools), "v1")
        second = job_exports(JobConfig(self.folder / "two", tools), "v2")
        candidate = replace(first.playback, cache=self.folder / "candidate")
        self.assertEqual(first.cache, (self.folder / "one/recognition").resolve())
        self.assertNotEqual(first.playback.cache, second.playback.cache)
        self.assertNotEqual(candidate.cache, first.playback.cache)
        self.assertEqual(second.playback.engine_version, "v2")
        with self.assertRaises(FrozenInstanceError):
            first.playback.cache = candidate.cache

    def seed_artifacts(self):
        f = self.folder
        (f / "source.pdf").write_bytes(b"original")
        (f / "companion.json").write_bytes(b"{}")
        score = dict(sourceSHA256=digest(f / "source.pdf"))
        (f / "score.json").write_text(json.dumps(score))
        (f / "web/score").mkdir(parents=True)
        (f / "web/score/page-1.webp").write_bytes(b"image")
        result = dict(
            sourceSHA256=score["sourceSHA256"],
            companionSHA256=digest(f / "companion.json"),
            pageCount=1,
            preparation=dict(status="applied"),
            musicalReview="unreviewed",
        )
        (f / "result.json").write_text(json.dumps(result))
        return result

    def test_completion_requires_all_artifacts_and_detects_changed_bytes(self):
        self.seed_artifacts()
        manifest = commit_result(self.folder)
        self.assertEqual(verify_result(self.folder), manifest)
        (self.folder / "companion.json").write_bytes(b"{x")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            verify_result(self.folder)

    def test_missing_page_never_commits(self):
        self.seed_artifacts()
        (self.folder / "web/score/page-1.webp").unlink()
        with self.assertRaises(FileNotFoundError):
            commit_result(self.folder)
        self.assertFalse((self.folder / "completion.json").exists())

    def test_manifest_cannot_omit_pages_or_escape_job_folder(self):
        self.seed_artifacts()
        original = commit_result(self.folder)
        for fault in ("page", "path", "schema", "source", "list", "artifacts", "bool"):
            manifest = deepcopy(original)
            if fault == "page":
                manifest["artifacts"].pop("web/score/page-1.webp")
            if fault == "path":
                manifest["artifacts"]["../outside"] = {"bytes": 0, "sha256": "x"}
            if fault == "schema":
                manifest["schemaVersion"] = 999
            if fault == "source":
                manifest["sourceSHA256"] = "wrong"
            if fault == "list":
                manifest = []
            if fault == "artifacts":
                manifest["artifacts"] = []
            if fault == "bool":
                manifest["schemaVersion"] = True
            (self.folder / "completion.json").write_text(json.dumps(manifest))
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                verify_result(self.folder)

    def test_atomic_write_failure_preserves_previous_complete_bytes(self):
        target = self.folder / "result"
        target.write_bytes(b"previous")
        with patch(
            "livesheet.pdf_pipeline.artifacts.os.replace", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                atomic_bytes(target, b"next")
        self.assertEqual(target.read_bytes(), b"previous")
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_corrupt_cache_is_a_miss_and_original_bytes_are_retained(self):
        path = self.folder / "cache.json"
        self.assertEqual(read_cache_object(path), {})
        for raw in (b"partial{", b"[]", b"null", b"\xff"):
            path.write_bytes(raw)
            self.assertEqual(read_cache_object(path), {})
            self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(b'{"ok":true}')
        self.assertEqual(read_cache_object(path), {"ok": True})

    def test_reference_checks_reject_inconsistent_meters_rests_and_ties(self):
        from livesheet.testing.pdf_reference_validation import validate_authored
        from livesheet.testing.pdf_performance_fixtures import CASES

        for name, spec in CASES.items():
            if name == "meter-ties":
                with self.assertRaisesRegex(ValueError, "Measure 3"):
                    validate_authored(spec)
            else:
                validate_authored(spec)
        for fault in ("rest", "tie", "duplicate", "duration"):
            spec = deepcopy(CASES["opening-tempo"])
            event = list(spec["measures"][0]["voices"][0]["events"][0])
            spec["measures"][0]["voices"][0]["events"][0] = event
            if fault == "rest":
                spec["measures"][0]["voices"][0]["events"].pop(0)
            if fault == "tie":
                event.append(["stop"])
            if fault == "duplicate":
                event[0].append(event[0][0])
            if fault == "duration":
                event[2] = 0
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                validate_authored(spec)


if __name__ == "__main__":
    unittest.main()
