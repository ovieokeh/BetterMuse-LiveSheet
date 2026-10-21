"""Versioned completion manifest: output presence alone never means ready.

Artifacts retain existing app/web formats. This independent manifest records
integrity and preparation status, not a claim of musical accuracy. Publishing it
last is the commit point. The server verifies it before marking a job ready.
"""

from pathlib import Path
import hashlib
import json
import os
import tempfile

SCHEMA_VERSION = 1
REQUIRED = ("source.pdf", "score.json", "companion.json", "result.json")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def commit_result(folder):
    result = json.loads((folder / "result.json").read_text())
    score = json.loads((folder / "score.json").read_text())
    if (
        result["sourceSHA256"] != digest(folder / "source.pdf")
        or score["sourceSHA256"] != result["sourceSHA256"]
    ):
        raise ValueError("result-source-mismatch")
    if result["companionSHA256"] != digest(folder / "companion.json"):
        raise ValueError("result-companion-mismatch")
    artifacts = list(REQUIRED)
    artifacts += [f"web/score/page-{i+1}.webp" for i in range(result["pageCount"])]
    manifest = dict(
        schemaVersion=SCHEMA_VERSION,
        sourceSHA256=result["sourceSHA256"],
        musicalReview=result.get("musicalReview", "unreviewed"),
        preparation=result["preparation"],
        artifacts={
            name: dict(
                sha256=digest(folder / name), bytes=(folder / name).stat().st_size
            )
            for name in artifacts
        },
    )
    atomic_bytes(
        folder / "completion.json", (json.dumps(manifest, indent=2) + "\n").encode()
    )
    return manifest


def verify_result(folder):
    manifest = json.loads((folder / "completion.json").read_text())
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schemaVersion")) is not int
        or manifest["schemaVersion"] != SCHEMA_VERSION
    ):
        raise ValueError("unsupported-result-manifest")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or not set(REQUIRED) <= artifacts.keys():
        raise ValueError("incomplete-result-manifest")
    for name, expected in artifacts.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid-result-artifact-path")
        path = folder / relative
        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError("invalid-result-artifact-path")
        if (
            path.stat().st_size != expected["bytes"]
            or digest(path) != expected["sha256"]
        ):
            raise ValueError("result-artifact-mismatch: " + name)
    result = json.loads((folder / "result.json").read_text())
    score = json.loads((folder / "score.json").read_text())
    pages = result["pageCount"]
    if not isinstance(pages, int) or isinstance(pages, bool) or not 1 <= pages <= 20:
        raise ValueError("invalid-result-page-count")
    required_pages = {f"web/score/page-{i+1}.webp" for i in range(pages)}
    if set(artifacts) != set(REQUIRED) | required_pages:
        raise ValueError("incomplete-result-pages")
    if not (
        manifest["sourceSHA256"]
        == result["sourceSHA256"]
        == score["sourceSHA256"]
        == artifacts["source.pdf"]["sha256"]
    ):
        raise ValueError("result-source-mismatch")
    if result["companionSHA256"] != artifacts["companion.json"]["sha256"]:
        raise ValueError("result-companion-mismatch")
    if manifest["preparation"] != result["preparation"]:
        raise ValueError("result-preparation-mismatch")
    return manifest
