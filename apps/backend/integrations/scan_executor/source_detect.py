"""Source-environment detection + cdxgen image selection.

A faithful Python port of BomLens' ``docker/lib/source-detect.sh`` (SK Telecom,
Apache-2.0). The shell version is the single source of truth that both the CLI
and the web UI use so a source scan resolves transitive dependencies the same
way on either path; we mirror its detection precedence and image map exactly so
TRUSCA routes a given tree to the same per-environment cdxgen image.

Increment 2 only *records* the detected environment (logging + scan_metadata).
No routing branch is wired yet — the LocalDockerExecutor (increment 3) is the
first consumer.

Detection precedence (highest first), matching ``detect_lang``:
  1. android  — an Android Gradle plugin / namespace, or an AndroidManifest.xml
  2. swift    — SwiftPM / CocoaPods / Xcode project
  3. a single-language signal among {rust, go, ruby, java, python, node, php,
     dotnet, cpp}; two or more → ``mixed``; none → ``unknown``.

All tunables resolve at call time (CLAUDE.md core rule #11).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Image tag for the per-language cdxgen images (BomLens CDXGEN_TAG).
_DEFAULT_IMAGE_TAG = "v12"
# All-in-one cdxgen image for mixed / unknown / cpp / android-fallback
# (BomLens CDXGEN_ALLINONE).
_DEFAULT_ALLINONE = "ghcr.io/cyclonedx/cdxgen:v12.5.0"
# Android SDK image prefix + default compileSdk (BomLens ANDROID_IMAGE_PREFIX /
# ANDROID_API_DEFAULT). The SKT android images are API-tagged; routing to them
# is deferred past increment 2.
_DEFAULT_ANDROID_PREFIX = "ghcr.io/sktelecom/sbom-scanner-android-sdk"
_DEFAULT_ANDROID_API = 34

# env → per-language cdxgen image, mirroring ``img_for_lang``. Anything not here
# (android, cpp, mixed, unknown) resolves to the all-in-one image — exactly as
# the shell ``case`` falls through to ``*``.
_LANG_IMAGE_SUFFIX: dict[str, str] = {
    "rust": "cdxgen-debian-rust",
    "go": "cdxgen-debian-golang124",
    "ruby": "cdxgen-debian-ruby34",
    "java": "cdxgen-temurin-java21",
    "python": "cdxgen-python312",
    "node": "cdxgen-node20",
    "php": "cdxgen-debian-php84",
    "dotnet": "cdxgen-debian-dotnet9",
    "swift": "cdxgen-debian-swift",
}

# Every value ``detect_language`` can return.
DETECTABLE_ENVS: tuple[str, ...] = (
    "android",
    "swift",
    "rust",
    "go",
    "ruby",
    "java",
    "python",
    "node",
    "php",
    "dotnet",
    "cpp",
    "mixed",
    "unknown",
)

# Android plugin / namespace markers inside a Gradle build file.
_ANDROID_GRADLE_RE = re.compile(r"com\.android\.(application|library)|namespace +['\"]")
# compileSdk / compileSdkVersion = NN.
_ANDROID_API_RE = re.compile(r"compileSdk(?:Version)?[ =]+(\d+)")

# The four Gradle files BomLens inspects for the Android markers (root + app/).
_GRADLE_BUILD_FILES = (
    "build.gradle",
    "build.gradle.kts",
    "app/build.gradle",
    "app/build.gradle.kts",
)


def _image_tag() -> str:
    return os.getenv("CDXGEN_IMAGE_TAG", _DEFAULT_IMAGE_TAG)


def _allinone_image() -> str:
    return os.getenv("CDXGEN_ALLINONE_IMAGE", _DEFAULT_ALLINONE)


def _android_prefix() -> str:
    return os.getenv("SCAN_ANDROID_IMAGE_PREFIX", _DEFAULT_ANDROID_PREFIX)


def _android_api_default() -> int:
    raw = os.getenv("SCAN_ANDROID_API_DEFAULT")
    if raw and raw.isdigit():
        return int(raw)
    return _DEFAULT_ANDROID_API


def _has_android_manifest(source_dir: Path) -> bool:
    """True if an AndroidManifest.xml exists within depth 3 (BomLens ``find -maxdepth 3``)."""
    if (source_dir / "AndroidManifest.xml").exists():
        return True
    # depth 2 and 3 relative to source_dir.
    for pattern in ("*/AndroidManifest.xml", "*/*/AndroidManifest.xml"):
        for _ in source_dir.glob(pattern):
            return True
    return False


def _gradle_build_text(source_dir: Path) -> str:
    """Concatenated text of the inspected Gradle files (missing files skipped)."""
    chunks: list[str] = []
    for rel in _GRADLE_BUILD_FILES:
        path = source_dir / rel
        if path.is_file():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return "\n".join(chunks)


def _is_android(source_dir: Path) -> bool:
    if _ANDROID_GRADLE_RE.search(_gradle_build_text(source_dir)):
        return True
    return _has_android_manifest(source_dir)


def _is_swift(source_dir: Path) -> bool:
    if any((source_dir / name).is_file() for name in ("Package.swift", "Podfile", "Podfile.lock")):
        return True
    return any(source_dir.glob("*.xcodeproj")) or any(source_dir.glob("*.xcworkspace"))


def _collect_language_signals(source_dir: Path) -> list[str]:
    """Root-level language signals, in BomLens' collection order."""
    langs: list[str] = []
    if (source_dir / "Cargo.toml").is_file():
        langs.append("rust")
    if (source_dir / "go.mod").is_file():
        langs.append("go")
    if (source_dir / "Gemfile").is_file():
        langs.append("ruby")
    if (
        (source_dir / "pom.xml").is_file()
        or any(source_dir.glob("*.gradle"))
        or any(source_dir.glob("*.gradle.kts"))
    ):
        langs.append("java")
    if (source_dir / "requirements.txt").is_file() or (source_dir / "pyproject.toml").is_file():
        langs.append("python")
    if (source_dir / "package.json").is_file():
        langs.append("node")
    if (source_dir / "composer.json").is_file():
        langs.append("php")
    if any(source_dir.glob("*.csproj")) or any(source_dir.glob("*.sln")):
        langs.append("dotnet")
    if any(
        (source_dir / name).is_file()
        for name in ("conanfile.txt", "conanfile.py", "vcpkg.json")
    ):
        langs.append("cpp")
    return langs


def detect_language(source_dir: Path) -> str:
    """Return the detected environment for ``source_dir`` (one of DETECTABLE_ENVS).

    Mirrors BomLens ``detect_lang``: android and swift take precedence; otherwise
    exactly one language signal returns that language, two or more return
    ``mixed``, and none returns ``unknown``.
    """
    if _is_android(source_dir):
        return "android"
    if _is_swift(source_dir):
        return "swift"

    langs = _collect_language_signals(source_dir)
    if len(langs) == 1:
        return langs[0]
    if not langs:
        return "unknown"
    return "mixed"


def image_for_env(
    env: str,
    *,
    tag: str | None = None,
    allinone: str | None = None,
) -> str:
    """Return the cdxgen image for ``env``, mirroring BomLens ``img_for_lang``.

    Per-language images use ``tag`` (default ``CDXGEN_IMAGE_TAG``); android, cpp,
    mixed, and unknown fall through to the all-in-one image (default
    ``CDXGEN_ALLINONE_IMAGE``) — android is special-cased to its API-tagged image
    in the orchestration layer, not here, exactly as in source-detect.sh.
    """
    suffix = _LANG_IMAGE_SUFFIX.get(env)
    if suffix is None:
        return allinone or _allinone_image()
    return f"ghcr.io/cyclonedx/{suffix}:{tag or _image_tag()}"


def android_compile_sdk(source_dir: Path) -> int:
    """Return the Android compileSdk from the Gradle files, or the default.

    Mirrors BomLens ``android_api`` — the first ``compileSdk[Version] = NN`` wins.
    """
    match = _ANDROID_API_RE.search(_gradle_build_text(source_dir))
    if match:
        return int(match.group(1))
    return _android_api_default()


def android_image(api: int, *, prefix: str | None = None, tag: str | None = None) -> str:
    """Return the API-tagged Android scanner image (deferred routing target)."""
    return f"{prefix or _android_prefix()}{api}:{tag or _image_tag()}"


__all__ = [
    "DETECTABLE_ENVS",
    "android_compile_sdk",
    "android_image",
    "detect_language",
    "image_for_env",
]
