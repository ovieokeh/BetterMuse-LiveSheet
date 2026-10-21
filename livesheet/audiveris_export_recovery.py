"""Re-export cached recognition with a narrowly scoped Audiveris 5.11.0 fix.

Original OMR/MusicXML/logs stay untouched. The patched exporter omits both ends
of unplaceable hairpins and repairs zero division units using exact OMR durations.
Both repairs are recorded. All note/measure validation stays in the normal
pipeline. No image recognition or musical timing is invented here.
"""

import hashlib
import fcntl
from livesheet.pdf_pipeline.config import AudiverisRuntime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "livesheet/vendor/audiveris/PartwiseBuilder.java"
BUILD = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "livesheet/export-fix"
COMPILER_SHA = "14c46ddc2e511c46c998da728f6a80f13a689c920f3832402f7ad150f6a6188d"
ENGINE_SHA = "9b35257edea808ec80a8bcd48882d174b107152d9640b5ca33e1365593cff2a5"
lock = threading.Lock()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint():
    return sha(SOURCE) + sha(__file__)


def zero_divisions(paths):
    """Inspect the score root only; malformed archives remain ordinary failures."""
    found = []
    for path in paths:
        with ZipFile(path) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(
                e.get("full-path")
                for e in container.iter()
                if e.tag.split("}")[-1] == "rootfile"
            )
            root = ET.fromstring(archive.read(rootfile))
        if any(
            node.text is not None and float(node.text) == 0
            for node in root.findall("./part/measure/attributes/divisions")
        ):
            found.append(Path(path).name)
    return found


def needed(log, paths=()):
    return (
        bool(zero_divisions(paths))
        or "PartwiseBuilder$WedgeIterators" in log
        and "java.lang.NullPointerException" in log
    )


def omissions(log):
    # IDs are local to each sheet, and the same wedge can touch two measures.
    found = {}
    for page, measure, wedge in re.findall(
        r"BM_WEDGE_OMITTED page=\{Page#(\d+)\.\d+\} measure=Measure\{#([^}]+)\} wedge=(\d+)",
        log,
    ):
        item = found.setdefault(
            (page, wedge), dict(page=int(page), wedgeId=wedge, measures=[])
        )
        if measure not in item["measures"]:
            item["measures"].append(measure)
    return list(found.values())


def build_overlay(launcher, *, sources=None, cache_name=None):
    """Compile against the exact supported distribution; never modify its jar."""
    runtime = AudiverisRuntime.from_launcher(launcher)
    jar = runtime.jar
    base = Path(os.environ.get("PDF_EXPORT_CACHE", str(BUILD)))
    build = base / cache_name if cache_name else base
    sources = tuple(sources) if sources is not None else (SOURCE,)
    if sha(jar) != ENGINE_SHA:
        raise ValueError(
            "export recovery requires the verified Audiveris 5.11.0 distribution"
        )
    build.mkdir(parents=True, exist_ok=True)
    with lock, (build / "build.lock").open("a") as build_lock:
        fcntl.flock(build_lock, fcntl.LOCK_EX)
        manifest = build / "build.json"
        key = fingerprint() + sha(jar) + "".join(sha(source) for source in sources)
        prior = json.loads(manifest.read_text()) if manifest.exists() else {}
        if (
            prior.get("fingerprint") == key
            and prior.get("classes")
            and all(
                (build / name).exists() and sha(build / name) == digest
                for name, digest in prior["classes"].items()
            )
        ):
            return build / "classes"
        compiler = base / "ecj-3.44.0.jar"
        if not compiler.exists() or sha(compiler) != COMPILER_SHA:
            raise ValueError(
                "missing verified Eclipse compiler; see livesheet/vendor/audiveris/README.md"
            )
        classes = build / "classes"
        if classes.exists():
            shutil.rmtree(classes)
        cp = os.pathsep.join(str(p) for p in sorted(jar.parent.glob("*.jar")))
        subprocess.run(
            [
                os.environ.get("PDF_COMPILER_JAVA", "java"),
                "-jar",
                str(compiler),
                "-17",
                "-proc:none",
                "-nowarn",
                "-cp",
                cp,
                "-d",
                str(classes),
                *map(str, sources),
            ],
            check=True,
            timeout=120,
        )
        outputs = {str(p.relative_to(build)): sha(p) for p in classes.rglob("*.class")}
        if not outputs:
            raise ValueError("export recovery compiler produced no classes")
        manifest.write_text(
            json.dumps(dict(fingerprint=key, classes=outputs), indent=2) + "\n"
        )
        return classes


def recover(folder, ident, launcher, original_exports, run, env):
    """Return derived MusicXML paths and an auditable recovery record."""
    omr = folder / f"{ident}.omr"
    original_hashes = {e["file"]: sha(folder / e["file"]) for e in original_exports}
    runtime = AudiverisRuntime.from_launcher(launcher)
    key = hashlib.sha256(
        (sha(omr) + fingerprint() + sha(runtime.jar)).encode()
    ).hexdigest()
    output = folder / "export-recovery"
    stamp = output / "recovery.json"
    prior = json.loads(stamp.read_text()) if stamp.exists() else {}
    if (
        prior.get("fingerprint") == key
        and prior.get("files")
        and all(
            (output / e["file"]).exists() and sha(output / e["file"]) == e["sha256"]
            for e in prior["files"]
        )
    ):
        return [output / e["file"] for e in prior["files"]], prior
    classes = build_overlay(launcher)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    # Audiveris is only given a disposable copy, even though this is export-only.
    copied = output / omr.name
    shutil.copy2(omr, copied)
    cp = os.pathsep.join([str(classes), str(runtime.jar.parent / "*")])
    run(
        [
            str(runtime.java),
            "-Xmx2g",
            "-XX:ActiveProcessorCount=2",
            "--add-exports=java.desktop/sun.awt.image=ALL-UNNAMED",
            "--enable-native-access=ALL-UNNAMED",
            "-cp",
            cp,
            "Audiveris",
            "-batch",
            "-export",
            "-output",
            str(output),
            "--",
            str(copied),
        ],
        output / "run.log",
        300,
        env,
    )
    log = (output / "run.log").read_text(errors="replace")
    if "Error visiting Measure" in log or "Exception" in log:
        raise ValueError(
            f'export recovery still reports an engine error; see {output / "run.log"}'
        )
    names = [e["file"] for e in original_exports]
    # A recovery must export exactly the same movements, without silently losing any.
    actual = {p.name for p in output.glob("*.mxl")}
    if actual != set(names):
        raise ValueError("export recovery changed the movement set")
    if zero_divisions([output / n for n in names]):
        raise ValueError("export recovery still contains zero divisions")
    record = dict(
        fingerprint=key,
        engineSHA256=ENGINE_SHA,
        patchSHA256=sha(SOURCE),
        inputOmrSHA256=sha(omr),
        originalMusicxmlSHA256=original_hashes,
        omittedWedges=omissions(log),
        durationGridRepairs=[
            dict(page=page, divisor=int(divisor), divisions=int(divisions))
            for page, divisor, divisions in sorted(
                set(
                    re.findall(
                        r"BM_DURATION_GRID page=(\{Page#[^}]+\}) divisor=(\d+) divisions=(\d+)",
                        log,
                    )
                )
            )
        ],
        files=[dict(file=n, sha256=sha(output / n)) for n in names],
    )
    stamp.write_text(json.dumps(record, indent=2) + "\n")
    copied.unlink()
    return [output / n for n in names], record
