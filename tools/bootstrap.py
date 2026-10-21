#!/usr/bin/env python3
"""Fetch only pinned public tool assets. Never upload source code or scores."""

import hashlib
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    (
        "downloads/Audiveris-5.11.0-ubuntu24.04-x86_64.deb",
        "https://github.com/Audiveris/audiveris/releases/download/5.11.0/Audiveris-5.11.0-ubuntu24.04-x86_64.deb",
        "f20113aaa33b3149ec8d6a09b2a7963360e65fafd92d69389987a85bbc3ec7a3",
    ),
    (
        "downloads/MuseScore-Studio-4.7.4.260706075-x86_64.AppImage",
        "https://github.com/musescore/MuseScore/releases/download/v4.7.4/MuseScore-Studio-4.7.4.260706075-x86_64.AppImage",
        "9233ed1b87d3e6b45722278f3c286dcd41e83da778bd0f80a1dd04949696ad93",
    ),
    (
        ".build/support/eng.traineddata",
        "https://raw.githubusercontent.com/tesseract-ocr/tessdata/4.1.0/eng.traineddata",
        "daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047",
    ),
    (
        ".build/support/ecj-3.44.0.jar",
        "https://repo.maven.apache.org/maven2/org/eclipse/jdt/ecj/3.44.0/ecj-3.44.0.jar",
        "14c46ddc2e511c46c998da728f6a80f13a689c920f3832402f7ad150f6a6188d",
    ),
)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    for relative, url, expected in ASSETS:
        path = ROOT / relative
        if path.is_file() and digest(path) == expected:
            print("Verified", relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".partial")
        try:
            with (
                urllib.request.urlopen(url, timeout=60) as source,
                temp.open("wb") as target,
            ):
                shutil.copyfileobj(source, target, 1024 * 1024)
            if digest(temp) != expected:
                raise ValueError("Asset checksum mismatch: " + relative)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        print("Downloaded and verified", relative)
    (ROOT / "data").mkdir(exist_ok=True)


if __name__ == "__main__":
    main()
