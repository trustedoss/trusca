"""Credential shapes ``scrub_secrets`` must redact, and text it must leave alone.

The shared scrubber guards cdxgen's failure message, the scan log stream and
BomLens step details. The broader shape matrix lives in
``tests/unit/tasks/test_scan_log_persist.py``; this file pins the shapes a
security review of the BomLens detail path found missing: token-only URL
userinfo, quoted and whitespace-separated key assignments, and well-known
token prefixes with no key beside them.
"""

from __future__ import annotations

import pytest

from integrations._secret_scrub import scrub_secrets


@pytest.mark.parametrize(
    ("raw", "expected", "secret"),
    [
        # (a) userinfo with no colon: a token alone before the host.
        (
            "fatal: could not read https://oauth2tokenvalue123@gitlab.example.com/g/r.git",
            "fatal: could not read https://***@gitlab.example.com/g/r.git",
            "oauth2tokenvalue123",
        ),
        (
            "https://user:pass@host/x",
            "https://***@host/x",
            "pass",
        ),
        # (b) a key in quotes, as JSON writes it.
        ('{"password": "s3cr3t"}', '{"password": "***"}', "s3cr3t"),
        ("{'secret':'tops3cret'}", "{'secret':'***'}", "tops3cret"),
        ('{"api_key": "k3yvalue99"}', '{"api_key": "***"}', "k3yvalue99"),
        ('{"_authToken":"npmauth77"}', '{"_authToken":"***"}', "npmauth77"),
        # (b) a quoted value after plain whitespace, as a Gradle DSL writes it.
        (
            'credentials { username "ci"; password "hunter2secret" }',
            'credentials { username "ci"; password "***" }',
            "hunter2secret",
        ),
        ("password 'hunter2secret'", "password '***'", "hunter2secret"),
    ],
)
def test_a_credential_is_redacted(raw: str, expected: str, secret: str) -> None:
    out = scrub_secrets(raw)
    assert secret not in out
    assert out == expected


#: (c) Tokens known by their prefix, with no key beside them. Prefix and body
#: are kept apart in the source, and joined only when the test runs, so the
#: file holds nothing a secret scanner reads as a live token.
PREFIXED_TOKENS = [
    ("ghp_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"),
    ("gho_", "Z9y8X7w6V5u4T3s2R1q0P9o8"),
    ("github_pat_", "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"),
    ("glpat-", "Ab12Cd34Ef56Gh78Ij90"),
    ("AKIA", "IOSFODNN7EXAMPLE"),
    ("xoxb-", "1234567890-abcdefghijklmn"),
    ("xoxp-", "1234567890-abcdefghijklmn"),
    ("npm_", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"),
]


@pytest.mark.parametrize(("prefix", "body"), PREFIXED_TOKENS, ids=[p for p, _ in PREFIXED_TOKENS])
def test_a_token_known_by_its_prefix_is_redacted(prefix: str, body: str) -> None:
    out = scrub_secrets(f"registry said {prefix}{body} and stopped")
    assert body not in out
    assert out == f"registry said {prefix}*** and stopped"


@pytest.mark.parametrize(
    "clean",
    [
        "steps that failed: gradle-dependencies",
        "manifests found: build.gradle; steps that failed: gradle-dependencies",
        "https://repo.maven.apache.org/maven2",
        "GET https://registry.npmjs.org/@scope/pkg 404",
        "pkg:npm/lodash@4.17.21",
        "docker://alpine@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1",
        "git@github.com:org/repo.git",
        "npm_config_cache=/tmp/npm",
        "the password reset failed",
        '"license": "MIT"',
    ],
)
def test_ordinary_text_is_left_alone(clean: str) -> None:
    assert scrub_secrets(clean) == clean


@pytest.mark.parametrize(
    ("raw", "expected", "secret"),
    [
        # A Docker config file's auth entry (base64 of user:password).
        (
            '{"auths": {"registry.example": {"auth": "Ym90Omh1bnRlcjI="}}}',
            '{"auths": {"registry.example": {"auth": "***"}}}',
            "Ym90Omh1bnRlcjI=",
        ),
        # JSON quoted inside a string: the quotes are escaped.
        ('{\\"password\\":\\"hunter2\\"}', '{\\"password\\":\\"***\\"}', "hunter2"),
        ("body={\\'token\\': \\'tok-91c2\\'}", "body={\\'token\\': \\'***\\'}", "tok-91c2"),
        # A percent-encoded URL with credentials, hidden in the decoded copy.
        (
            "GET https%3A%2F%2Fbot%3Ahunter2%40registry.example%2Fv2%2F failed",
            "GET https://***@registry.example/v2/ failed",
            "hunter2",
        ),
        # A proxy written with no scheme.
        (
            "proxy user:pw-77a1@10.0.0.1:3128 refused",
            "proxy ***@10.0.0.1:3128 refused",
            "pw-77a1",
        ),
        (
            "HTTPS_PROXY=user:pw-77a1@proxy.example:3128",
            "HTTPS_PROXY=***@proxy.example:3128",
            "pw-77a1",
        ),
    ],
    ids=[
        "docker-config-auth",
        "escaped-json",
        "escaped-single",
        "percent-encoded",
        "proxy",
        "proxy-env",
    ],
)
def test_a_credential_in_another_spelling_is_redacted(raw: str, expected: str, secret: str) -> None:
    out = scrub_secrets(raw)
    assert out == expected
    assert secret not in out


@pytest.mark.parametrize(
    "clean",
    [
        '"author": "Jane Doe"',
        '"authors": ["a", "b"]',
        "authority: registry.example",
        "alpine:3.19@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1",
        "at 12:30 on host-1",
        "contact: ops@example.com",
        "coverage 100% of 40%",
        "path a%2Fb has no URL in it",
    ],
)
def test_lookalikes_of_the_new_spellings_are_left_alone(clean: str) -> None:
    assert scrub_secrets(clean) == clean
