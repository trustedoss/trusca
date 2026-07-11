"""source_detect — faithful port of BomLens ``docker/lib/source-detect.sh``.

Two contracts are pinned here:

1. **Detection precedence + signals** (``detect_lang``): android > swift > a
   single language signal; two or more → ``mixed``; none → ``unknown``.
2. **env → image map** (``img_for_lang``): a golden table. BomLens'
   source-detect.sh is the external source of truth (different repo, so it
   cannot be imported in CI); this golden MUST be updated in lockstep with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.scan_executor import source_detect


def _touch(root: Path, *relpaths: str) -> Path:
    for rel in relpaths:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# detect_language — single-signal languages
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["requirements.txt"], "python"),
        (["pyproject.toml"], "python"),
        (["package.json"], "node"),
        (["pom.xml"], "java"),
        (["build.gradle"], "java"),
        (["build.gradle.kts"], "java"),
        (["go.mod"], "go"),
        (["Cargo.toml"], "rust"),
        (["Gemfile"], "ruby"),
        (["composer.json"], "php"),
        (["app.csproj"], "dotnet"),
        (["app.sln"], "dotnet"),
        (["conanfile.txt"], "cpp"),
        (["conanfile.py"], "cpp"),
        (["vcpkg.json"], "cpp"),
        (["Package.swift"], "swift"),
        (["Package.resolved"], "swift"),
        (["Podfile"], "swift"),
        (["Podfile.lock"], "swift"),
    ],
)
def test_detect_single_language(
    tmp_path: Path, files: list[str], expected: str
) -> None:
    assert source_detect.detect_language(_touch(tmp_path, *files)) == expected


def test_detect_xcodeproj_dir_is_swift(tmp_path: Path) -> None:
    (tmp_path / "MyApp.xcodeproj").mkdir()
    assert source_detect.detect_language(tmp_path) == "swift"


# --------------------------------------------------------------------------- #
# detect_language — precedence + mixed / unknown
# --------------------------------------------------------------------------- #


def test_empty_tree_is_unknown(tmp_path: Path) -> None:
    assert source_detect.detect_language(tmp_path) == "unknown"


def test_two_signals_are_mixed(tmp_path: Path) -> None:
    _touch(tmp_path, "requirements.txt", "package.json")
    assert source_detect.detect_language(tmp_path) == "mixed"


def test_android_plugin_beats_java(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text(
        "plugins { id 'com.android.application' }", encoding="utf-8"
    )
    assert source_detect.detect_language(tmp_path) == "android"


def test_android_namespace_marker(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "build.gradle.kts").write_text(
        'android {\n  namespace "com.example.app"\n}', encoding="utf-8"
    )
    assert source_detect.detect_language(tmp_path) == "android"


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_android_manifest_within_depth_3(tmp_path: Path, depth: int) -> None:
    nested = tmp_path
    for i in range(depth - 1):
        nested = nested / f"d{i}"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
    assert source_detect.detect_language(tmp_path) == "android"


def test_android_manifest_too_deep_is_not_android(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "AndroidManifest.xml").write_text("<manifest/>", encoding="utf-8")
    # No language signal either → unknown (manifest at depth 4 is out of range).
    assert source_detect.detect_language(tmp_path) == "unknown"


def test_swift_beats_language_signal(tmp_path: Path) -> None:
    _touch(tmp_path, "Package.swift", "package.json")
    assert source_detect.detect_language(tmp_path) == "swift"


def test_plain_gradle_without_android_is_java(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text(
        "dependencies { implementation 'org.foo:bar:1.0' }", encoding="utf-8"
    )
    assert source_detect.detect_language(tmp_path) == "java"


# --------------------------------------------------------------------------- #
# image_for_env — golden map mirroring img_for_lang
# --------------------------------------------------------------------------- #

_GOLDEN_IMAGE = {
    "rust": "ghcr.io/cyclonedx/cdxgen-debian-rust:v12",
    "go": "ghcr.io/cyclonedx/cdxgen-debian-golang124:v12",
    "ruby": "ghcr.io/cyclonedx/cdxgen-debian-ruby34:v12",
    "java": "ghcr.io/cyclonedx/cdxgen-temurin-java21:v12",
    "python": "ghcr.io/cyclonedx/cdxgen-python312:v12",
    "node": "ghcr.io/cyclonedx/cdxgen-node20:v12",
    "php": "ghcr.io/cyclonedx/cdxgen-debian-php84:v12",
    "dotnet": "ghcr.io/cyclonedx/cdxgen-debian-dotnet9:v12",
    "swift": "ghcr.io/cyclonedx/cdxgen-debian-swift:v12",
}
_ALLINONE = "ghcr.io/cyclonedx/cdxgen:v12.5.0"


@pytest.mark.parametrize(("env", "image"), list(_GOLDEN_IMAGE.items()))
def test_image_for_language_env(
    monkeypatch: pytest.MonkeyPatch, env: str, image: str
) -> None:
    monkeypatch.delenv("CDXGEN_IMAGE_TAG", raising=False)
    monkeypatch.delenv("CDXGEN_ALLINONE_IMAGE", raising=False)
    assert source_detect.image_for_env(env) == image


@pytest.mark.parametrize("env", ["android", "cpp", "mixed", "unknown"])
def test_image_for_fallthrough_env_is_allinone(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.delenv("CDXGEN_ALLINONE_IMAGE", raising=False)
    assert source_detect.image_for_env(env) == _ALLINONE


def test_every_detectable_env_maps_to_an_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No detectable env may raise or return empty — routing must be total."""
    monkeypatch.delenv("CDXGEN_IMAGE_TAG", raising=False)
    monkeypatch.delenv("CDXGEN_ALLINONE_IMAGE", raising=False)
    for env in source_detect.DETECTABLE_ENVS:
        assert source_detect.image_for_env(env).startswith("ghcr.io/cyclonedx/cdxgen")


def test_image_tag_and_allinone_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDXGEN_IMAGE_TAG", "v13")
    monkeypatch.setenv("CDXGEN_ALLINONE_IMAGE", "registry.local/cdxgen:pinned")
    assert source_detect.image_for_env("python") == "ghcr.io/cyclonedx/cdxgen-python312:v13"
    assert source_detect.image_for_env("mixed") == "registry.local/cdxgen:pinned"


# --------------------------------------------------------------------------- #
# android_compile_sdk
# --------------------------------------------------------------------------- #


def test_compile_sdk_extracted_from_gradle(tmp_path: Path) -> None:
    (tmp_path / "build.gradle").write_text("android { compileSdk 33 }", encoding="utf-8")
    assert source_detect.android_compile_sdk(tmp_path) == 33


def test_compile_sdk_version_form_in_kts(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "build.gradle.kts").write_text(
        "android {\n  compileSdkVersion = 31\n}", encoding="utf-8"
    )
    assert source_detect.android_compile_sdk(tmp_path) == 31


def test_compile_sdk_defaults_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SCAN_ANDROID_API_DEFAULT", raising=False)
    (tmp_path / "build.gradle").write_text("android { }", encoding="utf-8")
    assert source_detect.android_compile_sdk(tmp_path) == 34


def test_compile_sdk_default_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SCAN_ANDROID_API_DEFAULT", "35")
    assert source_detect.android_compile_sdk(tmp_path) == 35


def test_android_image_is_api_tagged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Android images use the `latest` / semver tag, distinct from the cdxgen v12
    # language-image tag.
    monkeypatch.delenv("SCAN_ANDROID_IMAGE_TAG", raising=False)
    monkeypatch.delenv("SCAN_ANDROID_IMAGE_PREFIX", raising=False)
    assert (
        source_detect.android_image(34)
        == "ghcr.io/sktelecom/sbom-scanner-android-sdk34:latest"
    )


def test_android_image_tag_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_ANDROID_IMAGE_TAG", "v1.2.3")
    assert (
        source_detect.android_image(35)
        == "ghcr.io/sktelecom/sbom-scanner-android-sdk35:v1.2.3"
    )
