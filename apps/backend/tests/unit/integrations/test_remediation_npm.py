"""
Pure-unit tests for the npm manifest-remediation adapter (v2.2 2.2-b2).

No DB, no network — the adapter is a pure text transform. We cover:

  * the range-rewrite policy matrix (caret / tilde / pinned / relop / x-range /
    wildcard / v-prefix),
  * multi-section bumps + only-recommended-packages-touched,
  * idempotent re-run (already-satisfied → no change; re-edit output → no-op),
  * format / key-order / trailing-newline / CRLF / BOM preservation,
  * ADVERSARIAL package.json (untrusted input): malformed JSON, non-object root,
    non-object dependencies, non-string version values, prototype-pollution keys,
    oversized files, duplicate keys, unicode names, semver junk / aliases / VCS
    sources — asserting clean handling (skip / flag, never crash).
"""

from __future__ import annotations

import json

import pytest

from integrations.remediation import (
    ManifestParseError,
    VersionBump,
    edit_npm_manifest,
)


def _bump(pkg: str, target: str) -> VersionBump:
    return VersionBump(package=pkg, target=target)


def _codes(result) -> set[str]:
    return {w.code for w in result.warnings}


# ---------------------------------------------------------------------------
# Range-rewrite policy matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "target", "after"),
    [
        ("^1.2.3", "1.3.0", "^1.3.0"),  # caret keeps caret
        ("~1.2.3", "1.3.0", "~1.3.0"),  # tilde keeps tilde
        ("1.2.3", "1.3.0", "1.3.0"),  # pinned stays pinned
        (">=1.2.3", "1.3.0", ">=1.3.0"),  # relop preserved
        (">1.2.3", "1.3.0", ">1.3.0"),
        ("v1.2.3", "1.3.0", "v1.3.0"),  # v-prefix preserved
        ("1.2.x", "1.3.0", "^1.3.0"),  # x-range widened to caret
        ("1.x", "1.3.0", "^1.3.0"),
        ("=1.2.3", "1.3.0", "=1.3.0"),  # explicit = preserved (operator style)
    ],
)
def test_range_rewrite_policy(before: str, target: str, after: str) -> None:
    text = json.dumps({"dependencies": {"pkg": before}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", target)])
    assert result.changed is True
    parsed = json.loads(result.edited_text)
    assert parsed["dependencies"]["pkg"] == after
    assert result.changes[0].before == before
    assert result.changes[0].after == after


def test_lower_bound_already_satisfied_is_noop() -> None:
    # ^2.0.0 already permits 1.5.0's fix? No — target lower than current → already
    # satisfied (current 2.0.0 >= target 1.5.0).
    text = json.dumps({"dependencies": {"pkg": "^2.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert result.changed is False
    assert "already_satisfied" in _codes(result)
    assert result.edited_text == text  # byte-identical


def test_pinned_equal_target_is_noop() -> None:
    text = json.dumps({"dependencies": {"pkg": "1.3.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.3.0")])
    assert result.changed is False
    assert "already_satisfied" in _codes(result)


def test_upper_bound_range_is_always_rewritten() -> None:
    # "<2.0.0" never asserts you are on the fix → rewrite the number.
    text = json.dumps({"dependencies": {"pkg": "<2.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.9.9")])
    assert result.changed is True
    assert json.loads(result.edited_text)["dependencies"]["pkg"] == "<1.9.9"


# ---------------------------------------------------------------------------
# Multi-section + selective edit
# ---------------------------------------------------------------------------


def test_multi_section_bumps_each_block() -> None:
    manifest = {
        "dependencies": {"a": "^1.0.0"},
        "devDependencies": {"b": "~2.0.0"},
        "optionalDependencies": {"c": "3.0.0"},
        "peerDependencies": {"d": ">=4.0.0"},
    }
    text = json.dumps(manifest, indent=2) + "\n"
    result = edit_npm_manifest(
        text,
        [
            _bump("a", "1.5.0"),
            _bump("b", "2.5.0"),
            _bump("c", "3.5.0"),
            _bump("d", "4.5.0"),
        ],
    )
    parsed = json.loads(result.edited_text)
    assert parsed["dependencies"]["a"] == "^1.5.0"
    assert parsed["devDependencies"]["b"] == "~2.5.0"
    assert parsed["optionalDependencies"]["c"] == "3.5.0"
    assert parsed["peerDependencies"]["d"] == ">=4.5.0"
    assert len(result.changes) == 4
    assert {c.section for c in result.changes} == {
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    }


def test_only_recommended_packages_touched() -> None:
    manifest = {"dependencies": {"keep": "^1.0.0", "bump": "^2.0.0"}}
    text = json.dumps(manifest, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("bump", "2.5.0")])
    parsed = json.loads(result.edited_text)
    assert parsed["dependencies"]["keep"] == "^1.0.0"  # untouched
    assert parsed["dependencies"]["bump"] == "^2.5.0"
    assert [c.package for c in result.changes] == ["bump"]


def test_package_in_two_sections_edited_in_both() -> None:
    manifest = {
        "dependencies": {"x": "^1.0.0"},
        "peerDependencies": {"x": "^1.0.0"},
    }
    text = json.dumps(manifest, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("x", "1.5.0")])
    parsed = json.loads(result.edited_text)
    assert parsed["dependencies"]["x"] == "^1.5.0"
    assert parsed["peerDependencies"]["x"] == "^1.5.0"
    assert len(result.changes) == 2


def test_scoped_package_name() -> None:
    manifest = {"dependencies": {"@scope/pkg": "^1.0.0"}}
    text = json.dumps(manifest, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("@scope/pkg", "1.5.0")])
    assert json.loads(result.edited_text)["dependencies"]["@scope/pkg"] == "^1.5.0"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_reedit_output_is_noop() -> None:
    text = json.dumps({"dependencies": {"pkg": "^1.2.3"}}, indent=2) + "\n"
    first = edit_npm_manifest(text, [_bump("pkg", "1.3.0")])
    assert first.changed is True
    second = edit_npm_manifest(first.edited_text, [_bump("pkg", "1.3.0")])
    assert second.changed is False
    assert second.edited_text == first.edited_text


# ---------------------------------------------------------------------------
# Format / ordering / newline / CRLF / BOM preservation
# ---------------------------------------------------------------------------


def test_preserves_key_order_and_indentation() -> None:
    text = (
        "{\n"
        '  "name": "demo",\n'
        '  "version": "0.0.0",\n'
        '  "dependencies": {\n'
        '    "z-last": "^1.0.0",\n'
        '    "a-first": "^2.0.0"\n'
        "  }\n"
        "}\n"
    )
    result = edit_npm_manifest(text, [_bump("a-first", "2.5.0")])
    # Only the one version token changed; key order + 2-space indent preserved.
    expected = text.replace('"a-first": "^2.0.0"', '"a-first": "^2.5.0"')
    assert result.edited_text == expected


def test_preserves_trailing_newline_absence() -> None:
    text = '{"dependencies": {"pkg": "^1.0.0"}}'  # no trailing newline
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert not result.edited_text.endswith("\n")
    assert result.edited_text == '{"dependencies": {"pkg": "^1.5.0"}}'


def test_preserves_crlf() -> None:
    text = '{\r\n  "dependencies": {\r\n    "pkg": "^1.0.0"\r\n  }\r\n}\r\n'
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert "\r\n" in result.edited_text
    assert '"pkg": "^1.5.0"' in result.edited_text


def test_preserves_bom() -> None:
    text = "﻿" + json.dumps({"dependencies": {"pkg": "^1.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert result.edited_text.startswith("﻿")
    assert '"pkg": "^1.5.0"' in result.edited_text


# ---------------------------------------------------------------------------
# Warnings — lockfile + not-present
# ---------------------------------------------------------------------------


def test_lockfile_warning_emitted_on_change() -> None:
    text = json.dumps({"dependencies": {"pkg": "^1.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert "lockfile_regeneration_required" in _codes(result)


def test_no_lockfile_warning_when_nothing_changed() -> None:
    text = json.dumps({"dependencies": {"pkg": "^2.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.0.0")])  # already satisfied
    assert "lockfile_regeneration_required" not in _codes(result)


def test_requested_package_not_present() -> None:
    text = json.dumps({"dependencies": {"other": "^1.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("ghost", "1.0.0")])
    assert result.changed is False
    assert "package_not_present" in _codes(result)


# ---------------------------------------------------------------------------
# Whole-manifest refusals (ManifestParseError)
# ---------------------------------------------------------------------------


def test_invalid_json_rejected() -> None:
    with pytest.raises(ManifestParseError) as ei:
        edit_npm_manifest("{not json", [_bump("pkg", "1.0.0")])
    assert ei.value.reason == "invalid_json"


def test_non_object_root_rejected() -> None:
    with pytest.raises(ManifestParseError) as ei:
        edit_npm_manifest("[1, 2, 3]", [_bump("pkg", "1.0.0")])
    assert ei.value.reason == "not_object"


def test_no_dependency_section_rejected() -> None:
    with pytest.raises(ManifestParseError) as ei:
        edit_npm_manifest('{"name": "demo"}', [_bump("pkg", "1.0.0")])
    assert ei.value.reason == "no_dependency_sections"


def test_non_string_manifest_rejected() -> None:
    with pytest.raises(ManifestParseError) as ei:
        edit_npm_manifest(123, [_bump("pkg", "1.0.0")])  # type: ignore[arg-type]
    assert ei.value.reason == "not_text"


def test_oversized_manifest_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NPM_MANIFEST_MAX_BYTES", "10")
    text = json.dumps({"dependencies": {"pkg": "^1.0.0"}}, indent=2) + "\n"
    with pytest.raises(ManifestParseError) as ei:
        edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert ei.value.reason == "too_large"


# ---------------------------------------------------------------------------
# Adversarial — per-package skips (never crash, never 500)
# ---------------------------------------------------------------------------


def test_dependencies_not_object_is_ignored() -> None:
    # dependencies is a string, not an object → that section is not editable, but
    # a real devDependencies object still drives the edit.
    manifest = {"dependencies": "garbage", "devDependencies": {"pkg": "^1.0.0"}}
    text = json.dumps(manifest, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert json.loads(result.edited_text)["devDependencies"]["pkg"] == "^1.5.0"


@pytest.mark.parametrize("value", [[1, 2], 42, None, {"nested": "x"}, True])
def test_non_string_version_value_skipped(value: object) -> None:
    manifest = {"dependencies": {"pkg": value}}
    text = json.dumps(manifest) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert result.changed is False
    assert "value_not_string" in _codes(result)


@pytest.mark.parametrize("evil_key", ["__proto__", "constructor", "prototype"])
def test_prototype_pollution_keys_are_inert(evil_key: str) -> None:
    # A pollution key as a DEPENDENCY name must be treated as ordinary data: if it
    # is not in the bump set it is left alone; if it IS targeted it is a plain
    # string edit, never a prototype mutation.
    manifest = {"dependencies": {evil_key: "^1.0.0", "real": "^2.0.0"}}
    text = json.dumps(manifest, indent=2) + "\n"
    # Only bump "real" — the pollution key must be untouched.
    result = edit_npm_manifest(text, [_bump("real", "2.5.0")])
    parsed = json.loads(result.edited_text)
    assert parsed["dependencies"][evil_key] == "^1.0.0"
    assert parsed["dependencies"]["real"] == "^2.5.0"


def test_pollution_key_as_a_dep_section_name_is_not_a_section() -> None:
    # __proto__ at top level is NOT one of our dep sections → ignored entirely.
    manifest = {"__proto__": {"pkg": "^1.0.0"}, "dependencies": {"pkg": "^1.0.0"}}
    text = json.dumps(manifest, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    # Only the real dependencies block is edited.
    assert json.loads(result.edited_text)["dependencies"]["pkg"] == "^1.5.0"


def test_duplicate_keys_collapsed_and_warned() -> None:
    # Raw text with a duplicated "dependencies" block — json keeps last-wins.
    text = (
        "{\n" '  "dependencies": {"pkg": "^9.9.9"},\n' '  "dependencies": {"pkg": "^1.0.0"}\n' "}\n"
    )
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert "duplicate_keys_collapsed" in _codes(result)
    # The last (winning) block's value is what we edit.
    assert '"pkg": "^1.5.0"' in result.edited_text


@pytest.mark.parametrize(
    "junk",
    [
        ">=1 || git+ssh://x",
        "npm:alias@^1.2.3",
        "file:../local",
        "git+https://github.com/x/y.git",
        "https://example.com/pkg.tgz",
        "workspace:*",
        "link:../sibling",
        ">=1.0.0 <2.0.0",  # compound
        "1.0.0 || 2.0.0",  # OR
    ],
)
def test_unrewritable_ranges_left_unchanged(junk: str) -> None:
    text = json.dumps({"dependencies": {"pkg": junk}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert result.changed is False
    assert "unparseable_range" in _codes(result)
    # The junk range is byte-preserved.
    assert json.loads(result.edited_text)["dependencies"]["pkg"] == junk


@pytest.mark.parametrize("wildcard", ["*", "", "latest", "x"])
def test_wildcards_left_unchanged(wildcard: str) -> None:
    text = json.dumps({"dependencies": {"pkg": wildcard}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    assert result.changed is False
    assert "unparseable_range" in _codes(result)


def test_target_unparseable_skips_entry() -> None:
    text = json.dumps({"dependencies": {"pkg": "^1.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "not-a-version")])
    assert result.changed is False
    assert "target_unparseable" in _codes(result)


def test_unicode_package_name_value_preserved() -> None:
    # A unicode dependency name + value must not crash the JSON-encode-match.
    manifest = {"dependencies": {"café": "^1.0.0", "pkg": "^1.0.0"}}
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    result = edit_npm_manifest(text, [_bump("pkg", "1.5.0")])
    parsed = json.loads(result.edited_text)
    assert parsed["dependencies"]["café"] == "^1.0.0"
    assert parsed["dependencies"]["pkg"] == "^1.5.0"


def test_no_bumps_is_noop() -> None:
    text = json.dumps({"dependencies": {"pkg": "^1.0.0"}}, indent=2) + "\n"
    result = edit_npm_manifest(text, [])
    assert result.changed is False
    assert result.edited_text == text
