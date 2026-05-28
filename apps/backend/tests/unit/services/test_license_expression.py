"""
Unit tests for the compound-SPDX evaluator — v2.2 Track C (c2).

PURE tests — no DB, no I/O. The evaluator
(``services.license_expression.evaluate_expression``) is a pure function over a
caller-supplied ``resolve_id``; these tests pin (a) the category algebra
(AND/OR/WITH folding, strategy overrides, unknown posture) and (b) the HARDENING
contract — the headline risk. The adversarial block asserts every hostile shape
resolves to the conservative unknown posture, in bounded wall-clock time, with no
unhandled exception (see MEMORY: adversarial-input parametrize).
"""

from __future__ import annotations

import time

import pytest

from services.license_expression import (
    CATEGORY_RANK,
    DEFAULT_STRATEGY,
    MAX_EXPRESSION_LENGTH,
    MAX_NESTING_DEPTH,
    MAX_TOKEN_COUNT,
    ExpressionResult,
    evaluate_expression,
)

# A small catalog standing in for the static-catalog + policy resolver. A miss
# resolves to "unknown" exactly as the real policy resolver does.
_CATALOG: dict[str, str] = {
    "MIT": "allowed",
    "Apache-2.0": "allowed",
    "BSD-3-Clause": "allowed",
    "MPL-2.0": "conditional",
    "LGPL-3.0-only": "conditional",
    "GPL-3.0-only": "forbidden",
    "AGPL-3.0-only": "forbidden",
}


def _resolve(spdx_id: str) -> str:
    return _CATALOG.get(spdx_id, "unknown")


# ---------------------------------------------------------------------------
# Rank mirror — the evaluator's rank table MUST match the static classifier's
# ---------------------------------------------------------------------------


def test_category_rank_mirrors_static_classifier() -> None:
    """A drift between the two rank tables would silently change verdicts."""
    from tasks.scan_source import _CATEGORY_RANK

    assert CATEGORY_RANK == _CATEGORY_RANK


# ---------------------------------------------------------------------------
# Category algebra — single id + compound folding
# ---------------------------------------------------------------------------


def test_single_id_allowed() -> None:
    result = evaluate_expression("MIT", resolve_id=_resolve)
    assert result == ExpressionResult(category="allowed", warning=None)


def test_single_id_forbidden() -> None:
    result = evaluate_expression("GPL-3.0-only", resolve_id=_resolve)
    assert result.category == "forbidden"
    assert result.warning is None


def test_and_keeps_most_restrictive() -> None:
    # allowed AND forbidden → forbidden (default AND = most_restrictive).
    result = evaluate_expression("MIT AND GPL-3.0-only", resolve_id=_resolve)
    assert result.category == "forbidden"


def test_and_three_operands_most_restrictive() -> None:
    result = evaluate_expression("MIT AND MPL-2.0 AND BSD-3-Clause", resolve_id=_resolve)
    assert result.category == "conditional"  # MPL-2.0 is the most restrictive


def test_or_keeps_least_restrictive_by_default() -> None:
    # allowed OR forbidden → allowed (default OR = least_restrictive).
    result = evaluate_expression("MIT OR GPL-3.0-only", resolve_id=_resolve)
    assert result.category == "allowed"


def test_with_keeps_most_restrictive_by_default() -> None:
    # The exception operand is uncatalogued (unknown); WITH = most_restrictive
    # so the base license's category survives.
    result = evaluate_expression("GPL-3.0-only WITH Classpath-exception-2.0", resolve_id=_resolve)
    assert result.category == "forbidden"
    result_allowed = evaluate_expression(
        "Apache-2.0 WITH LLVM-exception", resolve_id=_resolve
    )
    assert result_allowed.category == "allowed"


def test_nested_parens() -> None:
    # (MIT OR GPL-3.0-only) AND MPL-2.0
    #   inner OR → allowed (least), AND MPL-2.0 (conditional) → conditional.
    result = evaluate_expression("(MIT OR GPL-3.0-only) AND MPL-2.0", resolve_id=_resolve)
    assert result.category == "conditional"


def test_precedence_and_binds_tighter_than_or() -> None:
    # MIT OR GPL-3.0-only AND AGPL-3.0-only  ==  MIT OR (GPL AND AGPL)
    #   (GPL AND AGPL) → forbidden; MIT OR forbidden → allowed (least).
    result = evaluate_expression(
        "MIT OR GPL-3.0-only AND AGPL-3.0-only", resolve_id=_resolve
    )
    assert result.category == "allowed"


# ---------------------------------------------------------------------------
# Strategy overrides — the policy can flip the fold
# ---------------------------------------------------------------------------


def test_or_strategy_override_flips_result() -> None:
    # Forcing OR = most_restrictive turns allowed-OR-forbidden into forbidden.
    result = evaluate_expression(
        "MIT OR GPL-3.0-only",
        resolve_id=_resolve,
        strategy={"OR": "most_restrictive"},
    )
    assert result.category == "forbidden"


def test_and_strategy_override_relaxes_result() -> None:
    # Forcing AND = least_restrictive turns allowed-AND-forbidden into allowed.
    result = evaluate_expression(
        "MIT AND GPL-3.0-only",
        resolve_id=_resolve,
        strategy={"AND": "least_restrictive"},
    )
    assert result.category == "allowed"


def test_partial_strategy_falls_back_to_default_for_missing_operators() -> None:
    # Only OR specified → AND/WITH use DEFAULT_STRATEGY.
    result = evaluate_expression(
        "MIT AND GPL-3.0-only",
        resolve_id=_resolve,
        strategy={"OR": "most_restrictive"},
    )
    # AND default = most_restrictive → forbidden.
    assert result.category == "forbidden"
    assert DEFAULT_STRATEGY["AND"] == "most_restrictive"


# ---------------------------------------------------------------------------
# Unknown handling + posture
# ---------------------------------------------------------------------------


def test_unknown_id_uses_unknown_posture() -> None:
    result = evaluate_expression(
        "Frobnicate-1.0", resolve_id=_resolve, unknown_category="conditional"
    )
    assert result.category == "conditional"
    assert result.warning == "all_unknown"


def test_unknown_posture_can_be_forbidden() -> None:
    result = evaluate_expression(
        "Frobnicate-1.0", resolve_id=_resolve, unknown_category="forbidden"
    )
    assert result.category == "forbidden"


def test_least_restrictive_never_prefers_unknown_over_concrete() -> None:
    # unknown OR MIT → allowed (the concrete operand wins, not unknown).
    result = evaluate_expression("Frobnicate-1.0 OR MIT", resolve_id=_resolve)
    assert result.category == "allowed"


def test_most_restrictive_keeps_concrete_over_unknown() -> None:
    # unknown AND GPL-3.0-only → forbidden.
    result = evaluate_expression("Frobnicate-1.0 AND GPL-3.0-only", resolve_id=_resolve)
    assert result.category == "forbidden"


def test_exception_resolver_forces_allowed() -> None:
    # A resolver that grants a waiver (returns "allowed" for an otherwise
    # forbidden id) flips the verdict — this is how the policy exception path
    # reaches the evaluator.
    def waiver_resolve(spdx_id: str) -> str:
        if spdx_id == "GPL-3.0-only":
            return "allowed"  # waiver
        return _resolve(spdx_id)

    result = evaluate_expression("GPL-3.0-only", resolve_id=waiver_resolve)
    assert result.category == "allowed"
    # And in a compound: waived GPL OR MIT stays allowed.
    compound = evaluate_expression("GPL-3.0-only AND MIT", resolve_id=waiver_resolve)
    assert compound.category == "allowed"


def test_misbehaving_resolver_returning_garbage_is_treated_as_unknown() -> None:
    def bad_resolve(spdx_id: str) -> str:
        return "TOTALLY-NOT-A-CATEGORY"

    result = evaluate_expression("MIT", resolve_id=bad_resolve, unknown_category="conditional")
    # Out-of-vocabulary → unknown → unknown posture, never a crash.
    assert result.category == "conditional"
    assert result.warning == "all_unknown"


# ---------------------------------------------------------------------------
# Empty / None
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\t \n"])
def test_empty_or_whitespace_uses_unknown_posture(value: str | None) -> None:
    # Whitespace-only with control chars (\t,\n) still resolves to posture: the
    # strip() short-circuits before the control-char lexer for pure-whitespace.
    result = evaluate_expression(value, resolve_id=_resolve, unknown_category="conditional")
    assert result.category == "conditional"
    assert result.warning in {"empty", "control_char"}


# ---------------------------------------------------------------------------
# HARDENING — the headline risk. Adversarial input must be safe.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expression", "expected_warning"),
    [
        ("deep_open_paren", "(" * 5000 + "MIT", "too_long"),
        ("deep_balanced", "(" * 5000 + "MIT" + ")" * 5000, "too_long"),
        ("unbalanced_open", "(MIT", "unbalanced_parens"),
        ("unbalanced_close", "MIT)", "unexpected_token"),
        ("sep_only_and", "AND AND AND", "unexpected_operator"),
        ("sep_only_open_parens", "(((", "missing_operand"),
        ("empty_parens", "()", "unbalanced_parens"),
        ("unknown_operator", "MIT XOR GPL-3.0-only", "unexpected_token"),
        ("with_junk", "MIT WITH WITH", "unexpected_operator"),
        ("null_byte", "MIT\x00GPL-3.0-only", "control_char"),
        ("crlf", "MIT\r\nGPL-3.0-only", "control_char"),
        ("tab_embedded", "MIT\tAND\tGPL-3.0-only", "control_char"),
        ("trailing_operator", "MIT AND", "missing_operand"),
        ("leading_operator", "OR MIT", "unexpected_operator"),
        ("double_id", "MIT MIT", "unexpected_token"),
        ("oversized_or_chain", "MIT OR " * 2000 + "MIT", "too_long"),
        ("huge_token_count", "A OR " * 600 + "A", "too_many_tokens"),
        ("depth_over_within_length", "(" * 100 + "MIT" + ")" * 100, "max_depth_exceeded"),
    ],
)
def test_adversarial_input_is_safe(
    name: str, expression: str, expected_warning: str
) -> None:
    """Every hostile shape resolves to the conservative posture, fast, no crash."""
    start = time.perf_counter()
    result = evaluate_expression(
        expression, resolve_id=_resolve, unknown_category="conditional"
    )
    elapsed = time.perf_counter() - start

    # 1. Conservative safe result — the policy's unknown posture.
    assert result.category == "conditional", name
    # 2. A structured warning explaining the fallback.
    assert result.warning == expected_warning, (name, result.warning)
    # 3. Bounded wall-clock — no hang / catastrophic backtracking. 250ms is
    #    generously above the sub-millisecond real cost while staying robust on
    #    a loaded CI box.
    assert elapsed < 0.25, f"{name} took {elapsed:.3f}s — possible DoS"


def test_length_bound_triggers_before_parsing() -> None:
    """An input one char over the length bound is rejected without parsing."""
    over = "A" + "B" * MAX_EXPRESSION_LENGTH  # length = MAX + 1
    assert len(over) == MAX_EXPRESSION_LENGTH + 1
    result = evaluate_expression(over, resolve_id=_resolve, unknown_category="forbidden")
    assert result.category == "forbidden"
    assert result.warning == "too_long"


def test_depth_bound_is_independent_of_length() -> None:
    """Nesting just over the depth bound (but well under the length bound) trips
    the depth guard, NOT the length guard — proves the guard exists separately."""
    depth = MAX_NESTING_DEPTH + 1
    expr = "(" * depth + "MIT" + ")" * depth
    assert len(expr) < MAX_EXPRESSION_LENGTH  # length guard does NOT fire
    result = evaluate_expression(expr, resolve_id=_resolve, unknown_category="conditional")
    assert result.category == "conditional"
    assert result.warning == "max_depth_exceeded"


def test_depth_at_bound_minus_one_parses() -> None:
    """One level below the depth bound parses cleanly (boundary fence)."""
    depth = MAX_NESTING_DEPTH - 1
    expr = "(" * depth + "MIT" + ")" * depth
    result = evaluate_expression(expr, resolve_id=_resolve)
    assert result.category == "allowed"
    assert result.warning is None


def test_token_count_bound_is_independent_of_length() -> None:
    """A token count just over the bound (single-char ids, under the length
    bound) trips the token guard — proves it fires before O(n^2) work."""
    # Pack > MAX_TOKEN_COUNT tokens into < MAX_EXPRESSION_LENGTH chars using
    # single-char operands: "A OR " is 2 tokens per 5 chars.
    pairs = (MAX_TOKEN_COUNT // 2) + 50
    expr = "A OR " * pairs + "A"
    assert len(expr) < MAX_EXPRESSION_LENGTH
    result = evaluate_expression(expr, resolve_id=_resolve, unknown_category="conditional")
    assert result.category == "conditional"
    assert result.warning == "too_many_tokens"


def test_no_recursion_error_on_max_legal_depth() -> None:
    """The maximum LEGAL nesting depth parses without a RecursionError — the
    depth guard caps recursion below Python's interpreter limit."""
    depth = MAX_NESTING_DEPTH - 1
    expr = "(" * depth + "GPL-3.0-only" + ")" * depth
    result = evaluate_expression(expr, resolve_id=_resolve)
    assert result.category == "forbidden"
    assert result.warning is None
