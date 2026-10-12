# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Shared credential-shaped secret scrubber for subprocess output.

Originally added to ``tasks/_progress.py`` (P2 #8c follow-up, security review
MEDIUM on the scan-log-verbosity widening) to keep private-registry and
bearer-token credentials out of the live/durable ``scan.log`` stream. It moved
here, a leaf module under ``integrations/`` alongside ``_subprocess_env.py``
and ``_line_streamer.py``, because ``integrations/cdxgen.py`` needs the same
redaction on its ``CdxgenFailed`` exception message: cdxgen's stderr is not
line-streamed the way `publish_log` output is, so a private-registry
credential embedded in a failed pip/npm/Maven resolution (e.g. a
``PIP_CONFIG_FILE``-pointed ``index-url = https://user:pass@host/simple``)
survives into ``CdxgenFailed`` and, from there, into ``scan.error_message``,
an API-exposed field any team member can read (security review HIGH,
private-registry-auth-mount PR).

``tasks/`` is allowed to import ``integrations/`` (the pipeline orchestrates
leaf adapters); the reverse is a layering violation, so the scrubber lives in
the leaf package and ``tasks._progress`` re-exports it for its existing
publish-path call site and test suite.
"""

from __future__ import annotations

import re

import structlog

log = structlog.get_logger("integrations._secret_scrub")

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
#
# Conservative on purpose: we would rather false-positive a few innocuous
# tokens than miss a credential. Every pattern is line-bounded (``\S+`` /
# no ``re.MULTILINE``) so a pathological single-line input cannot make a
# pattern's match span runaway across the whole payload.

#: Key words whose value is a credential. Unanchored on purpose, so
#: ``AWS_SECRET_ACCESS_KEY`` matches via ``secret`` and ``GITHUB_TOKEN`` via
#: ``token``; over-redaction is the accepted cost.
_CREDENTIAL_KEYS = r"(?:password|passwd|passphrase|secret|credential|access[_-]?key|token)\w*"

#: Between a key and its value: ``=`` or ``:`` with optional spaces, and an
#: optional closing quote on the key, so ``KEY=v``, ``KEY: v`` and the JSON
#: ``"KEY": "v"`` all match.
_ASSIGN = r"[\"']?\s*[:=]\s*"


def _key_patterns(key: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Patterns that redact the value assigned to ``key``.

    A quoted value is redacted up to its closing quote, which is kept, so a
    JSON or DSL line keeps its shape (``"password": "***"``). A quoted value
    may also follow the key after plain whitespace, as in a Gradle
    ``credentials { password "..." }`` block; an unquoted value only after
    ``=`` or ``:``, because ``password reset failed`` is prose, not an
    assignment. An unquoted value runs to the next whitespace, as before.
    """
    return (
        (
            re.compile(rf"(?i)({key}(?:{_ASSIGN}|\s+))([\"'])[^\"'\n]*\2"),
            r"\1\2***\2",
        ),
        (re.compile(rf"(?i)({key}{_ASSIGN})(?![\"'])\S+"), r"\1***"),
    )


_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # HTTP Authorization schemes Bearer would miss. Verbose Trivy / cdxgen
    # registry round-trips (``--debug``, ``CDXGEN_DEBUG_MODE=debug``) emit
    # ``Authorization: Basic <b64(user:pass)>`` and ``Authorization: token
    # <ghp_...>``, neither of which is a Bearer token. We redact the
    # credential value only (keeping the scheme keyword visible for
    # debuggability), so a line carrying a second credential after it still
    # gets matched by the later patterns.
    (re.compile(r"(?i)(Authorization\s*:\s*Basic\s+)\S+"), r"\1***"),
    (re.compile(r"(?i)(Authorization\s*:\s*token\s+)\S+"), r"\1***"),
    # HTTP Bearer tokens anywhere (incl. ``Authorization: Bearer <tok>``),
    # RFC 6750 syntax (token charset).
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._\-+/=]+"), r"\1***"),
    # Registry / VCS / cloud auth headers Trivy + cdxgen emit in debug mode:
    # ``X-Registry-Auth`` (Docker), ``PRIVATE-TOKEN`` (GitLab), ``X-Amz-Security-Token``
    # (AWS ECR), ``X-Auth-Token`` (generic). Line-bounded ``\S+`` value.
    (
        re.compile(
            r"(?i)((?:x-registry-auth|private-token|x-amz-security-token|x-auth-token)\s*[:=]\s*)\S+"
        ),
        r"\1***",
    ),
    # Set-Cookie / Cookie, session material. Redact to end-of-line so a
    # multi-attribute cookie (``session=abc; Path=/``) is fully covered.
    (re.compile(r"(?i)((?:set-)?cookie\s*:\s*)\S.*$"), r"\1***"),
    # npm-style auth tokens: ``npm_config__authToken=``, ``_authToken:``,
    # ``_auth =``. The trailing ``\S+`` is line-bounded (no re.MULTILINE) so
    # it cannot run away across lines.
    *_key_patterns(r"_auth(?:Token)?"),
    # Generic ``password`` / ``secret`` / ``credential`` / ``access[_-]key`` /
    # ``token`` assignments that resolved config / env dumps surface in
    # verbose mode (e.g. ``npm_config__password=``, ``GITHUB_TOKEN=ghp_...``,
    # ``AWS_SECRET_ACCESS_KEY=...``), JSON (``{"password": "..."}``) and build
    # DSLs (``password "..."``). See :data:`_CREDENTIAL_KEYS`.
    *_key_patterns(_CREDENTIAL_KEYS),
    # URLs with userinfo: ``scheme://user:pass@host`` and the token-only
    # ``scheme://<token>@host`` (a GitHub or GitLab token in a clone URL) ->
    # ``scheme://***@host``. The userinfo charset excludes ``/`` ``\s`` ``@``
    # so pathological inputs like ``://user:pass@@@host`` match the first
    # ``user:pass@`` only (the trailing ``@@host`` becomes opaque path, not a
    # userinfo passthrough). An image digest (``docker://alpine@sha256:...``)
    # is not userinfo and is left alone. This covers a failed ``pip`` /
    # ``npm`` / ``mvn`` resolution against a private-registry URL echoed in
    # stderr.
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s@]+@(?!sha\d+:)"), r"\1***@"),
    # Generic API key headers: ``X-API-Key:``, ``api-key=``, ``api_key:``
    *_key_patterns(r"x-api-key"),
    *_key_patterns(r"api[_-]?key"),
    # Tokens recognisable by their own prefix, with no key next to them:
    # GitHub (classic ``ghp_``/``gho_``/``ghu_``/``ghs_``/``ghr_`` and
    # fine-grained ``github_pat_``), GitLab (``glpat-``), Slack (``xoxb-``,
    # ``xoxp-``, ``xoxa-``, ``xoxs-``), npm (``npm_`` and 36 characters) and
    # AWS access key ids (``AKIA`` and 16 characters). The prefix is kept so
    # the kind of credential stays visible; minimum lengths keep ordinary
    # words such as ``npm_config_...`` out.
    (
        re.compile(r"\b(gh[pousr]_|github_pat_|glpat-|xox[bpas]-)[A-Za-z0-9_\-]{16,}"),
        r"\1***",
    ),
    (re.compile(r"\b(npm_)[A-Za-z0-9]{36}\b"), r"\1***"),
    (re.compile(r"\b(AKIA)[0-9A-Z]{16}\b"), r"\1***"),
)


def scrub_secrets(line: str) -> str:
    """Best-effort credential redaction on a line of subprocess output.

    Callers that may receive an unbounded / pathological string (a hostile or
    runaway subprocess) MUST truncate before calling this: the patterns are
    line-bounded but not length-bounded, so an oversized input should never
    reach the regex engine untruncated.

    Fails CLOSED + observable: if any pattern raises (a future regression or
    a pathological input that trips the engine), we drop the line to a hard
    sentinel and emit a distinct ``secret_scrub_failed`` event (no line
    content) so a redaction regression is alertable instead of silently
    leaking the un-scrubbed line downstream (security review, low severity).
    """
    try:
        for pattern, replacement in _SECRET_PATTERNS:
            line = pattern.sub(replacement, line)
        return line
    except Exception as exc:  # noqa: BLE001 - fail closed, never leak the raw line
        log.warning("secret_scrub_failed", error=str(exc))
        return "***(redaction failed)***"


__all__ = ["scrub_secrets"]
