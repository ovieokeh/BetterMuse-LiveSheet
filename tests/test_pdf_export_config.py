"""Exporter settings must stay local even when jobs overlap in one process."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
import mido
from livesheet import playback as exporter
from livesheet.pdf_pipeline.config import ExportConfig, AudiverisRuntime


class ExportConfigTests(unittest.TestCase):
    def config(self, folder, label):
        folder = folder / label
        folder.mkdir()
        return ExportConfig(
            folder, folder / "cache", folder / "public", folder / "trial", label, label
        )

    def test_simultaneous_page_exports_do_not_cross_job_directories(self):
        old = (
            exporter.BUNDLE,
            exporter.CACHE,
            exporter.PUBLIC,
            exporter.ENGINE_VERSION,
        )
        with tempfile.TemporaryDirectory() as temp:
            configs = [self.config(Path(temp), name) for name in ("one", "two")]
            for config, color in zip(configs, ("red", "blue")):
                (config.bundle / "score.pdf").write_bytes(color.encode())
                Image.new("RGB", (8, 8), color).save(config.bundle / "page.png")

            def prepare(config):
                entry = dict(
                    id="score",
                    format="pdf",
                    assetPath="score.pdf",
                    sha256=hashlib.sha256(
                        (config.bundle / "score.pdf").read_bytes()
                    ).hexdigest(),
                    previews=["page.png"],
                )
                exporter.prepare(entry, None, False, config=config)
                with Image.open(config.public / "score/page-1.webp") as image:
                    return image.getpixel((0, 0))

            with ThreadPoolExecutor(2) as pool:
                self.assertEqual(
                    list(pool.map(prepare, configs)), [(255, 0, 0), (0, 0, 255)]
                )
        self.assertEqual(
            old,
            (exporter.BUNDLE, exporter.CACHE, exporter.PUBLIC, exporter.ENGINE_VERSION),
        )

    def test_simultaneous_performances_use_their_own_engine_and_cache(self):
        gate = threading.Barrier(2)
        with tempfile.TemporaryDirectory() as temp:
            configs = [self.config(Path(temp), name) for name in ("one", "two")]
            for config in configs:
                (config.bundle / "score.musicxml").write_text("<score-partwise/>")

            def render(command, **kwargs):
                job = json.loads(Path(command[-1]).read_text())[0]
                gate.wait(timeout=5)
                cache = Path(job["out"][0]).parent
                midi = mido.MidiFile()
                track = mido.MidiTrack()
                midi.tracks.append(track)
                pitch = 60 if command[0] == "one" else 72
                track.extend(
                    [
                        mido.Message("note_on", note=pitch, velocity=64),
                        mido.Message("note_off", note=pitch, time=480),
                    ]
                )
                midi.save(cache / "score.mid")
                (cache / "score.mpos").write_text(
                    '<score><elements><element id="0" page="0" x="0" y="0" sx="1" sy="1"/></elements><events><event elid="0" position="0"/></events></score>'
                )
                from types import SimpleNamespace

                return SimpleNamespace(returncode=0, stdout="", stderr="")

            def prepare(config):
                return exporter.export_performance(
                    config.bundle / "score.musicxml", config.cache, True, config=config
                )[0][0]["midi"]

            with patch.object(
                exporter.subprocess, "run", side_effect=render
            ), ThreadPoolExecutor(2) as pool:
                self.assertEqual(list(pool.map(prepare, configs)), [60, 72])
            self.assertNotEqual(
                (configs[0].cache / "fingerprint.txt").read_text(),
                (configs[1].cache / "fingerprint.txt").read_text(),
            )

    def test_linux_and_mac_runtime_layouts(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {}, clear=True
        ):
            base = Path(temp)
            for layout, directory, runtime in [
                ("mac", "app", "runtime/Contents/Home/bin/java"),
                ("linux", "lib/app", "lib/runtime/bin/java"),
            ]:
                root = base / layout
                jar = root / directory / "audiveris.jar"
                jar.parent.mkdir(parents=True)
                jar.touch()
                java = root / runtime
                java.parent.mkdir(parents=True)
                java.touch()
                paths = AudiverisRuntime.from_launcher(root / "bin/Audiveris")
                self.assertEqual(paths.jar, jar.resolve())
                self.assertEqual(paths.java, java.resolve())


if __name__ == "__main__":
    unittest.main()
