# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Compound-SPDX license-expression evaluator — v2.2 (Track C — c2).

What c1 left for c2
-------------------
c1 (``services.license_policy_service.effective_category``) resolves a SINGLE,
SIMPLE SPDX identifier against a policy + static-catalog default. It deliberately
does NOT split a compound expression. This module is the compound evaluator c2
builds on top: it parses a full SPDX *expression* — single id, ``A AND B``,
``A OR B``, ``A WITH exc``, parentheses, arbitrary nesting — and folds the
per-operand categories with a per-operator strategy
(``compound_operator_strategy``) into a single category verdict.

Why this is the headline risk
-----------------------------
The expression string is UNTRUSTED input: it comes from scanner output (cdxgen
/ scancode emit raw SPDX expressions for multi-license files) and, transitively,
from dependency metadata an attacker could shape. The ``normalize_spdx_id``
recursive-DoS precedent means a naive
regex-with-nested-quantifiers or an unbounded recursive-descent parser is a
denial-of-service waiting to happen: ``(((((((((...`` 50k deep, ``A AND A AND
...`` a megabyte long, catastrophic backtracking, ``\0`` / CRLF / control
chars, ``WITH`` followed by junk, unbalanced parens, separator-only tokens.

This evaluator is therefore designed to NEVER hang, NEVER raise an unhandled
exception, and NEVER 500. The public entry point :func:`evaluate_expression`
catches every parse failure and resolves to a SAFE CONSERVATIVE category (the
caller-supplied ``unknown_category`` posture — typically ``conditional``) plus a
structured warning, instead of propagating an error to the build-gate path.

Hardening bounds (all enforced BEFORE any parsing work)
-------------------------------------------------------
* ``MAX_EXPRESSION_LENGTH``  — total characters. Oversized → conservative.
* ``MAX_TOKEN_COUNT``        — tokens after lexing. Too many → conservative.
* ``MAX_NESTING_DEPTH``      — parenthesis nesting depth. Deeper → conservative.

The lexer is a single linear pass (no backtracking). The parser is an explicit
recursive-descent over the *already-bounded* token list with a hard depth guard
that trips at ``MAX_NESTING_DEPTH`` — recursion can never exceed the same bound
the lexer already validated, so Python's own recursion limit is never reached.

Category algebra
----------------
Categories are ranked by :data:`CATEGORY_RANK` (``forbidden`` 3 > ``conditional``
2 > ``allowed`` 1 > ``unknown`` 0), mirroring ``tasks.scan_source._CATEGORY_RANK``
exactly (asserted at import). ``most_restrictive`` keeps the higher rank;
``least_restrictive`` keeps the lower rank — EXCEPT that ``unknown`` (rank 0) is
never preferred by ``least_restrictive`` when a concrete sibling exists, so
``unknown OR MIT`` reads as ``allowed`` (the concrete operand wins) rather than
collapsing to ``unknown``. This matches the legal reading of a dual-license: an
unrecognised alternative does not make a known-good alternative unusable.

``WITH`` (an SPDX license-exception operator, e.g. ``Apache-2.0 WITH
LLVM-exception``) is treated as a binary operator whose strategy defaults to
``most_restrictive`` — the exception cannot make the base license *less*
restrictive in our model — but the exception operand itself, being a license
*exception* rather than a license id, resolves to ``unknown`` via ``resolve_id``
(it is not in the catalog), so under ``most_restrictive`` the base license's
category is what survives. Teams may relax this via the strategy map.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

import structlog

# The folded value's type. The parser only ever moves values between
# ``resolve_id`` and ``combine``, so it does not care what they are: the policy
# gate folds category strings, the conflict service folds verdict objects.
FoldValue = TypeVar("FoldValue")

log = structlog.get_logger("license_expression")

# ---------------------------------------------------------------------------
# Category vocabulary + rank (kept in lock-step with tasks.scan_source)
# ---------------------------------------------------------------------------

# Mirror of ``tasks.scan_source._CATEGORY_RANK``. Duplicated (not imported) on
# purpose: importing ``tasks.scan_source`` pulls in the whole Celery scan
# pipeline (heavy + side-effectful at import) into the gate's hot path. The
# values are asserted identical by the unit tests.
CATEGORY_RANK: dict[str, int] = {
    "forbidden": 3,
    "conditional": 2,
    "allowed": 1,
    "unknown": 0,
}

# The strategy strings persisted in ``LicensePolicy.compound_operator_strategy``.
MOST_RESTRICTIVE = "most_restrictive"
LEAST_RESTRICTIVE = "least_restrictive"

# Default per-operator strategy. Mirrors
# ``models.license_policy`` / ``schemas.license_policy._default_compound_strategy``
# and the static classifier's "keep the most restrictive" behaviour, with the
# conventional ``OR`` relaxation (dual-license → least restrictive wins).
DEFAULT_STRATEGY: dict[str, str] = {
    "AND": MOST_RESTRICTIVE,
    "OR": LEAST_RESTRICTIVE,
    "WITH": MOST_RESTRICTIVE,
}

# ---------------------------------------------------------------------------
# Hardening bounds (the headline risk — see module docstring)
# ---------------------------------------------------------------------------

# A real SPDX expression is rarely more than a few hundred chars even for a
# many-way dual license. 4096 is comfortably above any legitimate input and far
# below a payload that would stress the lexer/parser.
MAX_EXPRESSION_LENGTH = 4096
# Each operand/operator/paren is one token. 1024 is well above any real
# expression and bounds the parser's work to O(tokens).
MAX_TOKEN_COUNT = 1024
# Parenthesis nesting depth. 64 mirrors the dependency-graph MAX_DEPTH clamp and
# is far deeper than any hand- or scanner-authored SPDX expression. Beyond this
# we refuse to parse rather than recurse.
MAX_NESTING_DEPTH = 64

# The fallback verdict for input we will not / cannot parse. ``None`` here means
# "use the caller's unknown_category posture" (resolved in evaluate_expression);
# we never hard-code a category so a policy's unknown posture is honoured.
_OPERATORS = frozenset({"AND", "OR", "WITH"})


@dataclass(frozen=True)
class ExpressionResult:
    """The outcome of evaluating an SPDX expression.

    ``category`` is the folded verdict (one of :data:`CATEGORY_RANK`'s keys).
    ``warning`` is ``None`` on a clean parse, or a short machine-readable code
    describing why the input fell back to the conservative posture (e.g.
    ``"too_long"``, ``"unbalanced_parens"``, ``"max_depth_exceeded"``). Callers
    that want to surface "this expression could not be parsed" can branch on it;
    the build-gate ignores it and just uses ``category``.
    """

    category: str
    warning: str | None = None


class ExpressionParseError(Exception):
    """Raised by :func:`fold_expression` when an expression cannot be read.

    Carries a short machine-readable ``code`` (``"too_long"``,
    ``"unbalanced_parens"``, ``"control_char"``, …) so a caller can attach a
    structured warning to whatever conservative answer its own vocabulary
    calls for.

    :func:`evaluate_expression` catches this and never propagates it — the
    build gate's guarantee that a license expression cannot 500 a request is
    unchanged.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# Historical alias: the lexer / parser raise this name throughout.
_ParseError = ExpressionParseError


# ---------------------------------------------------------------------------
# Lexer — single linear pass, no backtracking
# ---------------------------------------------------------------------------


def _is_control_char(ch: str) -> bool:
    """True for ASCII control chars (NUL, CR, LF, TAB, ...) and DEL."""
    code = ord(ch)
    return code < 0x20 or code == 0x7F


def _tokenize(expression: str) -> list[str]:
    """Lex *expression* into ``(``, ``)``, operator, and id tokens.

    One linear pass. Whitespace separates tokens; parentheses are their own
    tokens even when not whitespace-separated (``(MIT)`` → ``( MIT )``). The
    operators ``AND`` / ``OR`` / ``WITH`` are recognised case-INsensitively and
    normalised to upper-case (SPDX operators are case-insensitive in practice;
    scanners emit both). Any other word is an opaque license-id token passed to
    ``resolve_id`` verbatim.

    Raises ``_ParseError`` for control characters or when the token budget is
    exhausted — both before any quadratic work can happen.
    """
    tokens: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            word = "".join(buf)
            # Normalise the boolean/exception operators; leave ids verbatim.
            tokens.append(word.upper() if word.upper() in _OPERATORS else word)
            buf.clear()
            if len(tokens) > MAX_TOKEN_COUNT:
                raise _ParseError("too_many_tokens")

    for ch in expression:
        if _is_control_char(ch):
            # Whitespace control chars (we already excluded the ones below) are
            # rejected: SPDX expressions use only spaces. Treat ANY control char
            # — including embedded CR/LF/TAB/NUL — as hostile.
            raise _ParseError("control_char")
        if ch == " ":
            flush()
            continue
        if ch in "()":
            flush()
            tokens.append(ch)
            if len(tokens) > MAX_TOKEN_COUNT:
                raise _ParseError("too_many_tokens")
            continue
        buf.append(ch)
    flush()
    return tokens


# ---------------------------------------------------------------------------
# Parser — recursive descent over a pre-bounded token list, depth-guarded
# ---------------------------------------------------------------------------
#
# Grammar (precedence: AND/WITH bind tighter than OR, parens override):
#
#   or_expr   := and_expr ( "OR" and_expr )*
#   and_expr  := with_expr ( "AND" with_expr )*
#   with_expr := primary ( "WITH" primary )*
#   primary   := "(" or_expr ")" | ID
#
# Why this precedence: SPDX 2.x specifies ``+`` > ``WITH`` > ``AND`` > ``OR``.
# We don't model ``+`` as an operator (it's part of an id, handled by
# resolve_id); ``WITH`` is folded at the tightest binary level so
# ``A WITH e OR B`` parses as ``(A WITH e) OR B``.


def _combine_categories(
    left: str,
    right: str,
    operator: str,
    strategy_map: dict[str, str],
) -> str:
    """Fold two operand categories under *operator*'s strategy.

    ``least_restrictive`` never prefers ``unknown`` over a concrete sibling:
    if one side is ``unknown`` and the other is concrete, the concrete side
    wins regardless of strategy. ``most_restrictive`` keeps the higher rank
    (so ``unknown`` only survives if BOTH sides are unknown).
    """
    strategy = strategy_map.get(operator, DEFAULT_STRATEGY.get(operator, MOST_RESTRICTIVE))
    lrank = CATEGORY_RANK.get(left, 0)
    rrank = CATEGORY_RANK.get(right, 0)
    if strategy == LEAST_RESTRICTIVE:
        # Prefer the lower rank, but never collapse a concrete operand to
        # unknown: if exactly one side is unknown, take the other.
        if left == "unknown" and right != "unknown":
            return right
        if right == "unknown" and left != "unknown":
            return left
        return left if lrank <= rrank else right
    # most_restrictive (default / fallback): higher rank wins.
    return left if lrank >= rrank else right


class _Parser(Generic[FoldValue]):
    """Recursive-descent SPDX-expression parser with a hard depth guard.

    The depth guard increments on every ``primary`` that opens a parenthesis (or
    enters the precedence-climbing recursion) and trips at
    :data:`MAX_NESTING_DEPTH`. Because the token list is already bounded by the
    lexer, and the depth guard caps recursion at the same bound the lexer would
    have hit, Python's interpreter recursion limit is never reached.

    Vocabulary-neutral: the parser knows the SPDX *grammar* and nothing about
    what an operand resolves to. ``resolve_id`` maps a token to a value and
    ``combine`` folds two values under an operator, so the same hardened parser
    serves the policy-category fold (:func:`evaluate_expression`) and the
    outbound-conflict fold (:mod:`services.license_conflict`) without a second
    copy of the length / token / depth guards to keep in step. Validating that
    a resolver returned an in-vocabulary value is the caller's job — it is the
    caller that owns the vocabulary.
    """

    __slots__ = ("_combine", "_pos", "_resolve", "_tokens")

    def __init__(
        self,
        tokens: list[str],
        *,
        resolve_id: Callable[[str], FoldValue],
        combine: Callable[[FoldValue, FoldValue, str], FoldValue],
    ) -> None:
        self._tokens = tokens
        self._pos = 0
        self._resolve = resolve_id
        self._combine = combine

    # -- token cursor helpers ------------------------------------------------

    def _peek(self) -> str | None:
        if self._pos < len(self._tokens):
            return self._tokens[self._pos]
        return None

    def _advance(self) -> str:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    # -- grammar -------------------------------------------------------------

    def parse(self) -> FoldValue:
        result = self._or_expr(depth=0)
        if self._pos != len(self._tokens):
            # Trailing tokens the grammar did not consume (e.g. ``MIT MIT``,
            # ``MIT )``) → malformed.
            raise _ParseError("unexpected_token")
        return result

    def _or_expr(self, *, depth: int) -> FoldValue:
        if depth >= MAX_NESTING_DEPTH:
            raise _ParseError("max_depth_exceeded")
        value = self._and_expr(depth=depth)
        while self._peek() == "OR":
            self._advance()
            right = self._and_expr(depth=depth)
            value = self._combine(value, right, "OR")
        return value

    def _and_expr(self, *, depth: int) -> FoldValue:
        value = self._with_expr(depth=depth)
        while self._peek() == "AND":
            self._advance()
            right = self._with_expr(depth=depth)
            value = self._combine(value, right, "AND")
        return value

    def _with_expr(self, *, depth: int) -> FoldValue:
        value = self._primary(depth=depth)
        while self._peek() == "WITH":
            self._advance()
            right = self._primary(depth=depth)
            value = self._combine(value, right, "WITH")
        return value

    def _primary(self, *, depth: int) -> FoldValue:
        tok = self._peek()
        if tok is None:
            # Missing operand (e.g. ``MIT AND`` with nothing after, ``()``).
            raise _ParseError("missing_operand")
        if tok == "(":
            self._advance()
            # Nesting one level deeper — guard here so ``(((...`` trips the
            # bound rather than recursing unboundedly.
            inner = self._or_expr(depth=depth + 1)
            if self._peek() != ")":
                raise _ParseError("unbalanced_parens")
            self._advance()
            return inner
        if tok == ")":
            # A ``)`` where an operand was expected → unbalanced / empty parens.
            raise _ParseError("unbalanced_parens")
        if tok in _OPERATORS:
            # An operator where an operand was expected (``AND MIT``,
            # ``MIT OR OR Apache-2.0``) → malformed.
            raise _ParseError("unexpected_operator")
        # An opaque license id. The resolver maps it into the caller's
        # vocabulary; keeping that value in bounds is the caller's contract.
        self._advance()
        return self._resolve(tok)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fold_expression(
    spdx_expression: str | None,
    *,
    resolve_id: Callable[[str], FoldValue],
    combine: Callable[[FoldValue, FoldValue, str], FoldValue],
) -> FoldValue:
    """Parse an SPDX expression and fold it into ONE value of any vocabulary.

    ``resolve_id`` maps ONE license-id token to a value in the caller's
    vocabulary — any type, folded opaquely; ``combine(left, right, operator)``
    folds two such values, where ``operator`` is ``"AND"``, ``"OR"`` or
    ``"WITH"``.

    Raises :class:`ExpressionParseError` — carrying ``"empty"``,
    ``"too_long"``, ``"control_char"``, ``"unbalanced_parens"``,
    ``"max_depth_exceeded"`` or another lexer / parser code — for anything it
    cannot read. Signalling failure by exception rather than by a sentinel
    keeps the folded value's type honest: a vocabulary whose own values may be
    ``None`` (or falsy) stays unambiguous, and each caller decides what an
    unreadable expression means, because the conservative answer differs by
    vocabulary (the policy gate falls back to its unknown posture; a conflict
    verdict falls back to "unknown").

    Never hangs. The length, token-count and nesting bounds are enforced here
    so that a second vocabulary cannot ship with weaker guards than the first.
    """
    if spdx_expression is None or not spdx_expression.strip():
        raise ExpressionParseError("empty")

    # Bound length BEFORE any per-char work.
    if len(spdx_expression) > MAX_EXPRESSION_LENGTH:
        log.warning(
            "license_expression.too_long",
            length=len(spdx_expression),
            limit=MAX_EXPRESSION_LENGTH,
        )
        raise ExpressionParseError("too_long")

    try:
        tokens = _tokenize(spdx_expression)
        if not tokens:
            # Should be unreachable (stripped was non-empty), but never trust it.
            raise ExpressionParseError("empty")
        parser = _Parser(tokens, resolve_id=resolve_id, combine=combine)
        return parser.parse()
    except ExpressionParseError as exc:
        log.warning("license_expression.unparseable", code=exc.code)
        raise
    except RecursionError as exc:  # pragma: no cover - depth guard prevents this
        # Belt-and-braces: the explicit depth guard makes this unreachable, but
        # if a future grammar change regresses it, fail safe rather than 500.
        log.error("license_expression.recursion_error")
        raise ExpressionParseError("recursion") from exc


def validate_expression_syntax(spdx_expression: str | None) -> str | None:
    """Return a parse-error code for *spdx_expression*, or ``None`` if well-formed.

    Syntax only — no vocabulary is consulted, so an expression naming licenses
    nobody has heard of still validates. Used where a user-supplied expression
    is stored rather than evaluated (``projects.declared_license``): accepting
    an unparseable value there would turn every later verdict into "unknown"
    with no indication of why.
    """
    try:
        fold_expression(
            spdx_expression,
            resolve_id=lambda _token: "",
            combine=lambda left, _right, _operator: left,
        )
    except ExpressionParseError as exc:
        return exc.code
    return None


def evaluate_expression(
    spdx_expression: str | None,
    *,
    resolve_id: Callable[[str], str],
    strategy: dict[str, str] | None = None,
    unknown_category: str = "conditional",
) -> ExpressionResult:
    """Resolve an SPDX license *expression* to a single category, safely.

    Parameters
    ----------
    spdx_expression:
        The raw SPDX expression — a single id, or a compound built from
        ``AND`` / ``OR`` / ``WITH`` and parentheses. UNTRUSTED input. ``None``
        or empty/whitespace-only → the conservative ``unknown_category`` posture.
    resolve_id:
        Maps a SINGLE license id (one operand token) to its category
        (``allowed`` | ``conditional`` | ``forbidden`` | ``unknown``). The caller
        supplies one that applies policy overrides/exceptions then falls back to
        the static catalog (see :func:`services.policy_gate._policy_resolver`). A
        catalog-miss MUST resolve to ``"unknown"`` so the unknown-posture rules
        apply.
    strategy:
        Per-operator resolution strategy (``compound_operator_strategy`` from the
        team's policy). Missing operators fall back to :data:`DEFAULT_STRATEGY`.
        ``None`` → the default strategy.
    unknown_category:
        The posture used (a) when the whole expression is empty/None, and (b) as
        the conservative fallback when the expression cannot be parsed. This is
        the policy's ``unknown_license_category``.

    Returns
    -------
    ExpressionResult
        ``category`` is always a valid category; ``warning`` is a short code when
        the conservative fallback was used (else ``None``).

    Guarantees
    ----------
    Never raises, never hangs, never returns an invalid category. Adversarial
    input (oversized, deeply-nested, unbalanced, control chars, separator-only,
    unknown operators, junk) resolves to ``unknown_category`` with a warning.
    """
    resolved_strategy = strategy if strategy is not None else DEFAULT_STRATEGY

    def _resolve(token: str) -> str:
        category = resolve_id(token)
        if category not in CATEGORY_RANK:
            # Defensive: a misbehaving resolver returned a non-category. Treat as
            # unknown rather than trusting an out-of-vocabulary string.
            return "unknown"
        return category

    def _combine(left: str, right: str, operator: str) -> str:
        return _combine_categories(left, right, operator, resolved_strategy)

    try:
        category = fold_expression(
            spdx_expression,
            resolve_id=_resolve,
            combine=_combine,
        )
    except ExpressionParseError as exc:
        return ExpressionResult(category=unknown_category, warning=exc.code)

    # A fully-``unknown`` expression (every operand uncatalogued) folds to
    # "unknown"; surface it as the policy's unknown posture so the caller never
    # sees a raw "unknown" that the gate can't act on.
    if category == "unknown":
        return ExpressionResult(category=unknown_category, warning="all_unknown")
    return ExpressionResult(category=category, warning=None)


__all__ = [
    "CATEGORY_RANK",
    "DEFAULT_STRATEGY",
    "MAX_EXPRESSION_LENGTH",
    "MAX_NESTING_DEPTH",
    "MAX_TOKEN_COUNT",
    "ExpressionParseError",
    "ExpressionResult",
    "evaluate_expression",
    "fold_expression",
    "validate_expression_syntax",
]
