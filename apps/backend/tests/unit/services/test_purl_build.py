"""Unit tests for ``services.purl_build.build_purl``.

Extracted from ``vulnerability_matching._build_purl``'s own test suite
(``test_vulnerability_matching.py``, which still exercises the wrapper
end-to-end); these cover the shared namespace-splitting/encoding core
directly, including the versionless form the deps.dev package lookup needs.
"""

from __future__ import annotations

from services.purl_build import build_purl


def test_npm_simple() -> None:
    assert build_purl("npm", "lodash", "4.17.20") == "pkg:npm/lodash@4.17.20"


def test_npm_scoped() -> None:
    assert (
        build_purl("npm", "@types/node", "20.0.0")
        == "pkg:npm/%40types/node@20.0.0"
    )


def test_maven_namespace_split() -> None:
    assert (
        build_purl("maven", "org.apache.commons:commons-text", "1.10.0")
        == "pkg:maven/org.apache.commons/commons-text@1.10.0"
    )


def test_golang_namespace_split() -> None:
    assert (
        build_purl("golang", "github.com/foo/bar", "v1.2.3")
        == "pkg:golang/github.com/foo/bar@v1.2.3"
    )


def test_version_none_omits_suffix() -> None:
    assert build_purl("npm", "lodash", None) == "pkg:npm/lodash"


def test_version_none_with_namespace_split() -> None:
    assert (
        build_purl("maven", "org.apache.commons:commons-text", None)
        == "pkg:maven/org.apache.commons/commons-text"
    )


def test_maven_without_colon_has_no_namespace() -> None:
    assert build_purl("maven", "standalone-artifact", "1.0") == "pkg:maven/standalone-artifact@1.0"


def test_non_namespaced_type_keeps_slash_in_name() -> None:
    # pypi is not in NAMESPACED_TYPES, so a "/" in the name is not split.
    assert build_purl("pypi", "foo/bar", "1.0") == "pkg:pypi/foo/bar@1.0"


def test_rejects_missing_name() -> None:
    assert build_purl("npm", "", "1.0") is None
    assert build_purl("npm", None, "1.0") is None  # type: ignore[arg-type]


def test_rejects_missing_purl_type() -> None:
    assert build_purl("", "pkg", "1.0") is None


def test_rejects_control_char_in_name() -> None:
    assert build_purl("npm", "evil\r\npkg", "1.0") is None


def test_rejects_control_char_in_version() -> None:
    assert build_purl("npm", "pkg", "1.0\x00") is None


def test_rejects_del_char() -> None:
    assert build_purl("npm", "pkg\x7f", "1.0") is None


def test_rejects_control_char_in_purl_type() -> None:
    assert build_purl("npm\n", "pkg", "1.0") is None


def test_strips_whitespace() -> None:
    assert build_purl("npm", "  lodash  ", "  1.0  ") == "pkg:npm/lodash@1.0"


def test_empty_version_string_rejected() -> None:
    assert build_purl("npm", "lodash", "   ") is None
