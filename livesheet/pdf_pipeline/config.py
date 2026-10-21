"""Immutable per-job settings shared by recognition and export adapters."""

from dataclasses import dataclass
from pathlib import Path
import os

# Output provenance version, not a selectable preparation mode.
PREPARATION_PROFILE = "experimental-v1"


@dataclass(frozen=True)
class AudiverisRuntime:
    jar: Path
    java: Path

    @classmethod
    def from_launcher(cls, launcher):
        prefix = Path(launcher).resolve().parent.parent
        # Official jpackage layouts: macOS Contents/app and Linux lib/app.
        default = prefix / "app/audiveris.jar"
        linux = prefix / "lib/app/audiveris.jar"
        if linux.is_file():
            default = linux
        jar = Path(os.environ.get("PDF_AUDIVERIS_JAR", str(default))).resolve()
        runtime = jar.parent.parent / "runtime"
        java = runtime / (
            "bin/java" if (runtime / "bin/java").is_file() else "Contents/Home/bin/java"
        )
        return cls(jar, Path(os.environ.get("PDF_AUDIVERIS_JAVA", str(java))).resolve())


@dataclass(frozen=True)
class Toolchain:
    audiveris: Path
    musescore: str
    tessdata: Path
    audiveris_jar: Path

    @classmethod
    def configured(cls, audiveris, musescore, tessdata):
        binary = Path(os.environ.get("PDF_AUDIVERIS", str(audiveris))).resolve()
        return cls(
            binary,
            os.environ.get("PDF_MUSESCORE", str(musescore)),
            Path(os.environ.get("PDF_TESSDATA", str(tessdata))).resolve(),
            AudiverisRuntime.from_launcher(binary).jar,
        )

    def validate(self):
        for path in (
            self.audiveris,
            Path(self.musescore),
            self.audiveris_jar,
            self.tessdata / "eng.traineddata",
        ):
            if not path.is_file():
                raise ValueError("preparation_tools_unavailable: " + str(path))


@dataclass(frozen=True)
class JobConfig:
    folder: Path
    tools: Toolchain

    def __post_init__(self):
        object.__setattr__(self, "folder", self.folder.resolve())


@dataclass(frozen=True)
class ExportConfig:
    bundle: Path
    cache: Path
    public: Path
    trial: Path
    musescore: str
    engine_version: str


@dataclass(frozen=True)
class RecognitionConfig:
    cache: Path
    results: Path
    staging: Path
    tessdata: Path
    playback: ExportConfig


def job_exports(config, engine_version):
    folder = config.folder
    playback = ExportConfig(
        folder,
        folder / "performance",
        folder / "web",
        folder / "unused-trial",
        config.tools.musescore,
        engine_version,
    )
    return RecognitionConfig(
        folder / "recognition",
        folder / "reports",
        folder / "staging",
        config.tools.tessdata,
        playback,
    )
