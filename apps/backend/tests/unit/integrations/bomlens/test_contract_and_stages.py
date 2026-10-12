"""BomLens result manifest: parsing, the stage mapping, and the vocabulary it shares.

The persist path is exercised against a real database in
``tests/integration/scan/test_bomlens_result.py``. This file pins the rules on
their own and the places the contract's vocabulary meets TRUSCA's.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from integrations.bomlens import contract, stages
from services import scan_outcome as so

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "bomlens"
CASES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def _manifest(case: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / case / contract.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    return data


def _step(
    step_id: str, status: str, reason: str | None = None, detail: str | None = None
) -> dict[str, Any]:
    return {
        "id": step_id,
        "status": status,
        "reason": reason,
        "detail": detail,
        "stage": 2,
        "started_at": "2026-10-11T09:00:00Z",
        "ended_at": "2026-10-11T09:00:05Z",
    }


def _with_steps(*steps: dict[str, Any]) -> contract.EngineResult:
    document = copy.deepcopy(_manifest("source-ok"))
    document["steps"] = list(steps)
    return contract.parse_manifest(document)


# ---------------------------------------------------------------------------
# The fixtures are what the tests below assume
# ---------------------------------------------------------------------------


#: The contract version each capture was written with (PROVENANCE.md):
#: sha-b826d43 wrote 1.0.0-dev, sha-5a93586 1.0.0, sha-1e5ef6b 1.2.0.
CONTRACT_VERSIONS = {
    "image-ok": "1.0.0-dev",
    "image-os-licenses": "1.2.0",
    "source-deep-license": "1.0.0",
    "source-degraded": "1.0.0-dev",
    "source-no-components": "1.0.0",
    # Derived from source-no-components (PROVENANCE.md): only the detail moved.
    "source-no-components-failed-step": "1.0.0",
    "source-ok": "1.0.0-dev",
    "source-ok-results-stripped": "1.0.0-dev",
    "source-run-failed": "1.0.0-dev",
    "source-trivy-db": "1.2.0",
    "source-trivy-db-stale": "1.2.0",
    "source-vulnerabilities-failed": "1.0.0-dev",
}


def test_the_fixture_set_is_the_documented_one() -> None:
    assert CASES == sorted(CONTRACT_VERSIONS)


@pytest.mark.parametrize("case", CASES)
def test_every_captured_manifest_parses(case: str) -> None:
    """Every 1.x minor the pinned images wrote reads with the same reader."""
    result = contract.parse_manifest(_manifest(case))
    assert result.contract_version == CONTRACT_VERSIONS[case]
    assert contract.contract_major(result.contract_version) == contract.SUPPORTED_MAJOR
    assert result.image_digest is not None and result.image_digest.startswith("sha256:")


def test_the_newest_captures_come_from_the_pinned_image() -> None:
    """The 1.2.0 captures are the image config.py pins, so a version bump
    that forgets to recapture shows up here."""
    from core.config import BOMLENS_IMAGE_DEFAULT

    pinned_digest = BOMLENS_IMAGE_DEFAULT.split("@", 1)[1]
    for case, version in CONTRACT_VERSIONS.items():
        if version == "1.2.0":
            assert contract.parse_manifest(_manifest(case)).image_digest == pinned_digest, case


def test_a_real_image_run_records_the_os_licenses_step() -> None:
    """Contract 1.2.0 (BomLens #206): the step is in real output, so the
    reader test above checks a real ``os-licenses`` entry, not an imagined one."""
    result = contract.parse_manifest(_manifest("image-os-licenses"))
    step = result.step("os-licenses")
    assert step is not None
    assert (step.status, step.reason) == ("ok", None)
    assert step.counts == {"replaced": 5, "hashes_dropped": 0}
    assert stages.STEP_STAGE["os-licenses"] == "enrich"
    assert stages.stage_outcomes(result) == []


@pytest.mark.parametrize("status", ["degraded", "failed"])
def test_an_os_licenses_problem_is_an_enrich_degradation(status: str) -> None:
    document = copy.deepcopy(_manifest("image-os-licenses"))
    step = next(s for s in document["steps"] if s["id"] == "os-licenses")
    assert step["status"] == "ok"
    step.update(status=status, reason="unavailable" if status == "degraded" else "failed")
    outcomes = stages.stage_outcomes(contract.parse_manifest(document))
    assert [(o.stage, o.step, o.status) for o in outcomes] == [("enrich", "os-licenses", status)]


def test_a_trivy_database_past_its_age_fails_the_vulnerability_scan() -> None:
    """The user's decision (2026-10-12): a database older than
    BOMLENS_TRIVY_DB_MAX_AGE_DAYS degrades scan-security, and the adapter
    fails the scan rather than persisting a result from stale data. Real
    output with the age check on; the same run with a fresh database is ok."""
    stale = contract.parse_manifest(_manifest("source-trivy-db-stale"))
    entries = [s for s in stale.steps if s.id == stages.VULNERABILITY_STEP]
    assert [(s.status, s.reason) for s in entries] == [("degraded", "unavailable")]
    assert entries[0].detail is not None and "days old" in entries[0].detail
    assert stages.vulnerability_failure(stale) == "the vulnerability scan degraded (unavailable)"
    fresh = contract.parse_manifest(_manifest("source-trivy-db"))
    assert stages.vulnerability_failure(fresh) is None


# ---------------------------------------------------------------------------
# Vocabulary contracts (hardening rule 2): the same words live in BomLens'
# contract, in this reader and in services.scan_outcome.
# ---------------------------------------------------------------------------


def test_every_contract_reason_but_no_input_is_a_trusca_stage_reason() -> None:
    """A step with nothing to read did not degrade; every other reason is one
    TRUSCA can record and label."""
    assert set(so.STAGE_REASONS) == set(contract.STEP_REASONS) - {"no_input"}


def test_every_mapped_stage_is_a_recorded_stage() -> None:
    targets = {stage for stage in stages.STEP_STAGE.values() if stage is not None}
    assert targets <= set(so.DEGRADABLE_STAGES)
    # And every stage only the engine writes is reachable from some step.
    assert {"generate", "normalize", "enrich"} <= targets


@pytest.mark.parametrize("case", CASES)
def test_every_step_and_reason_bomlens_wrote_is_one_this_reader_knows(case: str) -> None:
    """Values seen in real output, so a renamed step in a new image shows up here
    first instead of as a silently ignored step."""
    for step in _manifest(case)["steps"]:
        assert step["id"] in stages.STEP_STAGE, step["id"]
        assert step["status"] in contract.STEP_STATUSES
        assert step["reason"] is None or step["reason"] in contract.STEP_REASONS


def test_the_timing_stages_keep_the_names_the_progress_view_uses() -> None:
    from tasks.scan_source import _STAGE_PROGRESS

    named = set(stages.TIMING_STAGE.values())
    assert named <= set(_STAGE_PROGRESS)
    assert stages.POSTPROCESS_TIMING_STAGE not in _STAGE_PROGRESS


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "major"),
    [("1.0.0", 1), ("1.0.0-dev", 1), ("1.12.3", 1), ("2.0.0", 2), ("1.0", None), ("x", None)],
)
def test_the_contract_major_is_read_from_a_semantic_version(
    version: str, major: int | None
) -> None:
    assert contract.contract_major(version) == major


@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "1.0", "one"])
def test_a_version_other_than_1x_is_refused(version: str) -> None:
    document = _manifest("source-ok")
    document["contract"]["version"] = version
    with pytest.raises(contract.UnsupportedContractVersion) as caught:
        contract.parse_manifest(document)
    assert caught.value.version == version


def test_the_version_is_checked_before_the_shape() -> None:
    """A 2.x manifest that also dropped a 1.x field is refused for its version,
    which is the message that tells an operator what to do."""
    document = _manifest("source-ok")
    document["contract"]["version"] = "2.0.0"
    del document["steps"]
    with pytest.raises(contract.UnsupportedContractVersion):
        contract.parse_manifest(document)


def test_another_contract_name_is_not_a_bomlens_manifest() -> None:
    document = _manifest("source-ok")
    document["contract"]["name"] = "something-else"
    with pytest.raises(contract.BomLensContractError, match="not a BomLens"):
        contract.parse_manifest(document)


@pytest.mark.parametrize(
    ("where", "value"),
    [("run", {"status": "partly"}), ("step", {"status": "weird"}), ("step", {"stage": 3})],
)
def test_a_status_outside_the_1x_vocabulary_is_malformed(where: str, value: dict[str, Any]) -> None:
    document = _manifest("source-ok")
    target = document["run"] if where == "run" else document["steps"][0]
    target.update(value)
    with pytest.raises(contract.BomLensContractError):
        contract.parse_manifest(document)


def test_a_missing_manifest_says_the_engine_did_not_finish(tmp_path: Path) -> None:
    with pytest.raises(contract.ManifestMissing, match="stopped before it finished"):
        contract.load_manifest(tmp_path)


def test_a_manifest_that_is_not_json_is_refused(tmp_path: Path) -> None:
    (tmp_path / contract.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(contract.BomLensContractError, match="not valid JSON"):
        contract.load_manifest(tmp_path)


# ---------------------------------------------------------------------------
# Output files
# ---------------------------------------------------------------------------


def _output(path: str, data: bytes) -> contract.Output:
    import hashlib

    return contract.Output(
        role="sbom.cyclonedx", path=path, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)
    )


@pytest.mark.parametrize("name", ["../bom.json", "sub/bom.json", "..", ".", "", "a\\b", "a\x00b"])
def test_an_output_path_that_is_not_a_bare_file_name_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(contract.BomLensContractError, match="not a file name"):
        contract.read_output(tmp_path, _output(name, b"{}"))


def test_a_symlink_out_of_the_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    folder = tmp_path / "out"
    folder.mkdir()
    os.symlink(outside, folder / "bom.json")
    with pytest.raises(contract.BomLensContractError, match="not in the output folder"):
        contract.read_output(folder, _output("bom.json", b"{}"))


def test_a_size_mismatch_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bom.json").write_bytes(b"{} ")
    with pytest.raises(contract.BomLensContractError, match="bytes"):
        contract.read_output(tmp_path, _output("bom.json", b"{}"))


def test_a_matching_file_is_read(tmp_path: Path) -> None:
    (tmp_path / "bom.json").write_bytes(b'{"a": 1}')
    verified = contract.read_output(tmp_path, _output("bom.json", b'{"a": 1}'))
    assert (verified.name, verified.data, verified.document) == ("bom.json", b'{"a": 1}', {"a": 1})


def test_an_output_is_opened_once_and_parsed_from_the_hashed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: one read. A second open would let a file that changed between the
    hash and the parse be persisted as if it had been checked."""
    (tmp_path / "bom.json").write_bytes(b'{"a": 1}')
    opened: list[str] = []
    real_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_k: pytest.fail("read_text"))
    monkeypatch.setattr(Path, "read_bytes", lambda *_a, **_k: pytest.fail("read_bytes"))

    contract.read_output(tmp_path, _output("bom.json", b'{"a": 1}'))

    assert opened == ["bom.json"]


def test_a_manifest_symlinked_out_of_the_folder_is_refused(tmp_path: Path) -> None:
    """L1: the manifest gets the same containment as the outputs it names."""
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps(_manifest("source-ok")), encoding="utf-8")
    folder = tmp_path / "out"
    folder.mkdir()
    os.symlink(outside, folder / contract.MANIFEST_NAME)
    with pytest.raises(contract.BomLensContractError, match="not in the output folder"):
        contract.load_manifest(folder)


@pytest.mark.parametrize("target", ["manifest", "output"])
def test_json_nested_past_the_parser_stack_is_a_refused_result(tmp_path: Path, target: str) -> None:
    """Info: RecursionError becomes a BomLensError, so the scan fails with a
    message rather than an unexpected-error traceback."""
    deep = b"[" * 200_000 + b"]" * 200_000
    if target == "manifest":
        (tmp_path / contract.MANIFEST_NAME).write_bytes(deep)
        with pytest.raises(contract.BomLensContractError, match="nested too deeply"):
            contract.load_manifest(tmp_path)
    else:
        (tmp_path / "bom.json").write_bytes(deep)
        with pytest.raises(contract.BomLensContractError, match="nested too deeply"):
            contract.read_output(tmp_path, _output("bom.json", deep))


@pytest.mark.parametrize("target", ["manifest", "output"])
def test_a_number_past_the_integer_digit_limit_is_a_refused_result(
    tmp_path: Path, target: str
) -> None:
    """Python refuses to read an integer of more than 4300 digits with a
    ValueError; that is a refused result, not an unexpected error."""
    data = b'{"n": ' + b"9" * 5000 + b"}"
    if target == "manifest":
        (tmp_path / contract.MANIFEST_NAME).write_bytes(data)
        with pytest.raises(contract.BomLensContractError, match="too large to read"):
            contract.load_manifest(tmp_path)
    else:
        (tmp_path / "bom.json").write_bytes(data)
        with pytest.raises(contract.BomLensContractError, match="too large to read"):
            contract.read_output(tmp_path, _output("bom.json", data))


def test_a_lower_read_limit_is_never_read_past(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ceiling holds whatever size the manifest declared, and a caller's
    limit can lower it but not raise it above MAX_OUTPUT_BYTES."""
    data = b'{"a": "' + b"x" * 5000 + b'"}'
    (tmp_path / "bom.json").write_bytes(data)
    declared_small = contract.Output(
        role="license-scan.scancode", path="bom.json", sha256="0" * 64, bytes=1000
    )
    with pytest.raises(contract.OutputTooLarge):
        contract.read_output(tmp_path, declared_small, limit=4096)
    # Within both ceilings the same file reads.
    assert contract.read_output(tmp_path, _output("bom.json", data), limit=8192).data == data
    monkeypatch.setattr(contract, "MAX_OUTPUT_BYTES", 4096)
    with pytest.raises(contract.OutputTooLarge):
        contract.read_output(tmp_path, _output("bom.json", data), limit=2**40)


# ---------------------------------------------------------------------------
# Error messages stay short (L2) and the run's own fields agree (L3)
# ---------------------------------------------------------------------------


def test_a_huge_manifest_value_cannot_fill_the_error_message() -> None:
    document = _manifest("source-ok")
    document["contract"]["version"] = "9." + "9" * 10_000 + ".0"
    with pytest.raises(contract.UnsupportedContractVersion) as caught:
        contract.parse_manifest(document)
    message = str(caught.value)
    assert len(message) <= contract.MESSAGE_MAX_CHARS
    assert "9" * (contract.CLIP_CHARS + 1) not in message


def test_every_bomlens_error_caps_its_message() -> None:
    assert len(str(contract.BomLensError("x" * 10_000))) == contract.MESSAGE_MAX_CHARS
    failed = contract.BomLensRunFailed("failed", "s" * 10_000)
    assert len(str(failed)) < contract.CLIP_CHARS * 2


@pytest.mark.parametrize("status", ["succeeded", "succeeded_degraded"])
def test_a_succeeded_run_with_a_nonzero_exit_code_is_malformed(status: str) -> None:
    document = _manifest("source-ok")
    document["run"].update(status=status, exit_code=1)
    with pytest.raises(contract.BomLensContractError, match="exit code"):
        contract.parse_manifest(document)


# ---------------------------------------------------------------------------
# Stage mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason", "degraded"),
    [
        ("ok", None, False),
        ("disabled", None, False),
        ("skipped", "no_input", False),
        ("skipped", "not_installed", True),
        ("skipped", "too_large", True),
        ("degraded", "unavailable", True),
        ("failed", "timeout", True),
    ],
)
def test_which_step_results_are_degradations(
    status: str, reason: str | None, degraded: bool
) -> None:
    result = _with_steps(_step("sign", status, reason))
    assert stages.is_degradation(result.steps[0]) is degraded
    expected = [("sign", reason or "failed")] if degraded else []
    assert [(o.stage, o.reason) for o in stages.stage_outcomes(result)] == expected


def test_several_steps_in_one_stage_report_the_worst() -> None:
    result = _with_steps(
        _step("enrich-cpe", "ok"),
        _step("license-registry", "degraded", "budget_exhausted"),
        _step("enrich-os-context", "failed", "failed"),
        _step("enrich-maven-cpe", "degraded", "unavailable"),
    )
    [outcome] = stages.stage_outcomes(result)
    assert (outcome.stage, outcome.reason, outcome.step) == (
        "enrich",
        "failed",
        "enrich-os-context",
    )


def test_a_tie_keeps_the_first_step_recorded() -> None:
    result = _with_steps(
        _step("license-registry", "degraded", "budget_exhausted"),
        _step("enrich-cpe", "degraded", "unavailable"),
    )
    [outcome] = stages.stage_outcomes(result)
    assert (outcome.step, outcome.reason) == ("license-registry", "budget_exhausted")


def test_an_unknown_step_is_left_out_and_an_unknown_reason_reads_as_failed() -> None:
    document = copy.deepcopy(_manifest("source-ok"))
    document["steps"] = [_step("brand-new-step", "failed", "failed"), _step("sign", "degraded")]
    document["steps"][1]["reason"] = "brand_new_reason"
    result = contract.parse_manifest(document)
    assert [(o.stage, o.reason, o.engine_reason) for o in stages.stage_outcomes(result)] == [
        ("sign", "failed", "brand_new_reason")
    ]


@pytest.mark.parametrize(
    ("status", "degraded"),
    [
        # A reason a later contract minor adds, as BomLens #194 added
        # no_components before this reader knew it.
        ("degraded", True),
        # A later minor reason on a step whose status still says ok or skipped
        # is not read as clean: the reader cannot know what the word means.
        ("ok", True),
        ("skipped", True),
        ("failed", True),
        # An operator's choice stays a choice.
        ("disabled", False),
    ],
)
def test_a_reason_from_a_later_minor_is_never_read_as_success(status: str, degraded: bool) -> None:
    result = _with_steps(_step("generate", status, "no_dependencies_resolved"))
    outcomes = [(o.stage, o.reason, o.engine_reason) for o in stages.stage_outcomes(result)]
    assert outcomes == ([("generate", "failed", "no_dependencies_resolved")] if degraded else [])


def test_no_components_is_a_known_generate_degradation() -> None:
    """BomLens #194: the source declares dependencies and the SBOM has none.
    Recorded under its own reason, not the catch-all with the word kept aside."""
    result = _with_steps(_step("generate", "degraded", "no_components"))
    [outcome] = stages.stage_outcomes(result)
    assert (outcome.stage, outcome.reason, outcome.engine_reason) == (
        "generate",
        "no_components",
        None,
    )


def test_a_known_reason_is_not_repeated_as_an_engine_reason() -> None:
    [outcome] = stages.stage_outcomes(_with_steps(_step("generate", "degraded", "fallback")))
    assert (outcome.reason, outcome.engine_reason) == ("fallback", None)


# ---------------------------------------------------------------------------
# The step's detail kept with its degradation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        # BomLens #209: the resolver steps that failed are named after the manifests.
        (
            "source-no-components-failed-step",
            {"generate": "manifests found: pyproject.toml; steps that failed: gradle-dependencies"},
        ),
        ("source-no-components", {"generate": "manifests found: pyproject.toml"}),
        (
            "source-degraded",
            {"generate": "cdxgen-unavailable", "scancode": "scancode is not in this image"},
        ),
    ],
)
def test_a_degradation_keeps_the_engine_detail(case: str, expected: dict[str, str]) -> None:
    outcomes = stages.stage_outcomes(contract.parse_manifest(_manifest(case)))
    assert {o.stage: o.engine_detail for o in outcomes} == expected


def test_the_detail_kept_is_the_step_that_won_the_stage() -> None:
    """Several enrich steps fold into one stage; the detail travels with the
    step whose status was reported, not with the first or last one read."""
    result = _with_steps(
        _step("license-registry", "degraded", "budget_exhausted", "budget reached"),
        _step("enrich-os-context", "failed", "failed", "os-release unreadable"),
        _step("enrich-maven-cpe", "degraded", "unavailable", "maven central 503"),
    )
    [outcome] = stages.stage_outcomes(result)
    assert (outcome.step, outcome.engine_detail) == ("enrich-os-context", "os-release unreadable")


def test_a_clean_step_with_a_detail_is_not_an_outcome() -> None:
    assert stages.stage_outcomes(_with_steps(_step("generate", "ok", None, "1 component"))) == []


def _detail(text: str) -> str | None:
    return stages.step_detail(_with_steps(_step("generate", "degraded", "failed", text)).steps[0])


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        # The real Trivy message (source-vulnerabilities-failed) starts with a tab.
        ("\t* Get x: dial tcp", "* Get x: dial tcp"),
        ("line one\nline two\r\nthree", "line one line two three"),
        # NUL is refused by Postgres JSONB; other C0 controls, DEL and a lone
        # surrogate are dropped without leaving a gap.
        ("a\x00b\x1bc\x7fd\ud800e", "abcde"),
        ("  padded   out  ", "padded out"),
    ],
)
def test_the_detail_loses_its_control_characters(raw: str, kept: str) -> None:
    assert _detail(raw) == kept


def test_the_detail_is_scrubbed_of_credentials() -> None:
    kept = _detail(
        "steps that failed: pip-install (https://build:s3cr3t@pypi.internal/simple, token=abc123)"
    )
    assert kept is not None
    assert "s3cr3t" not in kept and "abc123" not in kept
    assert "https://***@pypi.internal/simple" in kept


def test_the_detail_is_cut_to_its_ceiling() -> None:
    kept = _detail("m" * 600)
    assert kept == "m" * (stages.DETAIL_MAX_CHARS - 3) + "..."
    assert _detail("m" * stages.DETAIL_MAX_CHARS) == "m" * stages.DETAIL_MAX_CHARS


def test_a_detail_of_nothing_but_control_characters_is_none() -> None:
    assert _detail("\x00\x01\n\t ") is None
    assert stages.step_detail(_with_steps(_step("generate", "degraded", "failed")).steps[0]) is None


def test_an_oversized_detail_is_cut_before_the_credential_patterns_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def _scrub(text: str) -> str:
        seen.append(len(text))
        return text

    monkeypatch.setattr(stages, "scrub_secrets", _scrub)
    _detail("x" * 100_000)
    assert seen == [stages._DETAIL_READ_MAX]


def test_steps_trusca_does_its_own_way_never_become_stages() -> None:
    result = _with_steps(
        _step("enrich-eol", "degraded", "unavailable"),
        _step("enrich-malicious", "degraded", "unavailable"),
        _step("generate-notice", "failed", "failed"),
        _step("scan-security", "failed", "failed"),
    )
    assert stages.stage_outcomes(result) == []


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([], "the vulnerability scan was not recorded"),
        ([_step("scan-security", "ok")], None),
        ([_step("scan-security", "disabled")], "the vulnerability scan disabled"),
        (
            [_step("scan-security", "skipped", "not_installed")],
            "the vulnerability scan skipped (not_installed)",
        ),
        ([_step("scan-security", "failed", "failed")], "the vulnerability scan failed (failed)"),
        # L4: the step is not degradable, so a qualified ok is not clean.
        ([_step("scan-security", "ok", "unavailable")], "the vulnerability scan ok (unavailable)"),
        # L3: listed twice, both entries count.
        (
            [_step("scan-security", "ok"), _step("scan-security", "failed", "timeout")],
            "the vulnerability scan failed (timeout)",
        ),
        ([_step("scan-security", "ok"), _step("scan-security", "ok")], None),
    ],
)
def test_only_an_ok_vulnerability_step_is_a_result(
    steps: list[dict[str, Any]], expected: str | None
) -> None:
    assert stages.vulnerability_failure(_with_steps(*steps)) == expected


def test_windows_cover_what_ran_and_fold_the_rest_into_postprocess() -> None:
    def at(minute: int) -> str:
        return f"2026-10-11T09:{minute:02d}:00Z"

    steps = [
        {**_step("generate", "ok"), "started_at": at(0), "ended_at": at(1)},
        {**_step("normalize", "ok"), "started_at": at(2), "ended_at": at(3)},
        {
            **_step("deep-license", "skipped", "not_installed"),
            "started_at": at(3),
            "ended_at": at(3),
        },
        {**_step("enrich-cpe", "degraded", "unavailable"), "started_at": at(4), "ended_at": at(6)},
        {**_step("sign", "disabled"), "started_at": at(9), "ended_at": at(9)},
        {**_step("scan-security", "ok"), "started_at": at(6), "ended_at": at(8)},
    ]
    windows = stages.stage_windows(_with_steps(*steps))

    def t(minute: int) -> datetime:
        return datetime(2026, 10, 11, 9, minute, tzinfo=UTC)

    assert windows == {
        "cdxgen": (t(0), t(1)),
        "postprocess": (t(2), t(6)),
        "trivy": (t(6), t(8)),
    }


# ---------------------------------------------------------------------------
# What the persist path is given
# ---------------------------------------------------------------------------


def test_the_sbom_shown_to_the_persist_path() -> None:
    from tasks.bomlens_result import sbom_for_persistence

    sbom: dict[str, Any] = {
        "components": [
            {"bom-ref": "bomlens-os-context", "type": "operating-system", "name": "debian"},
            {"bom-ref": "f1", "type": "file", "name": "/bin/sh"},
            {"bom-ref": "os:alpine@3.19.0", "type": "operating-system", "name": "alpine"},
            {"type": "library", "name": "a", "version": "UNKNOWN", "purl": "pkg:npm/a"},
            {
                "type": "library",
                "name": "zlib",
                "version": "1.3",
                "purl": "pkg:github/madler/zlib@1.3",
                "properties": [{"name": "bomlens:identifiedBy", "value": "scanoss"}],
                "components": [{"bom-ref": "f2", "type": "file", "name": "zlib.h"}],
            },
        ]
    }
    original = copy.deepcopy(sbom)

    shaped, left_out = sbom_for_persistence(sbom)

    assert left_out == 3
    names = [c["name"] for c in shaped["components"]]
    assert names == ["a", "zlib"]
    assert "version" not in shaped["components"][0]
    assert shaped["components"][1]["source"] == "scanoss"
    assert shaped["components"][1]["components"] == []
    # The document on disk stays exactly as the engine wrote and hashed it.
    assert sbom == original


def test_the_engine_record_leaves_tool_messages_out() -> None:
    from tasks.bomlens_result import engine_record

    record = engine_record(contract.parse_manifest(_manifest("source-vulnerabilities-failed")))
    assert "network is unreachable" not in json.dumps(record)
    assert {"id": "scan-security", "status": "failed", "reason": "failed"} in record["steps"]


# ---------------------------------------------------------------------------
# What counts as a Trivy result (M1)
# ---------------------------------------------------------------------------


def _real_report() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "source-ok" / "node_1.0.0_security.json").read_text(encoding="utf-8")
    )
    return data


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda r: r.clear(), "not schema version 2"),
        (lambda r: [r.clear(), r.update(SchemaVersion=2)], "no Results list"),
        (lambda r: r.pop("Results"), "no Results list"),
        (lambda r: r.update(Results={"Target": "x"}), "no Results list"),
        (lambda r: r.update(Results=["x"]), "no Results list"),
        (lambda r: r.update(SchemaVersion=1), "not schema version 2"),
        (lambda r: r.update(ArtifactType="container_image"), "not about the CycloneDX SBOM"),
        (lambda r: r.pop("ArtifactType"), "not about the CycloneDX SBOM"),
        (lambda r: r.update(ScanError={"Message": "db"}), "carries a scan error"),
    ],
)
def test_a_report_that_is_not_a_trivy_result_is_refused(change: Any, expected: str) -> None:
    from tasks.bomlens_result import trivy_report_problem

    report = _real_report()
    change(report)
    problem = trivy_report_problem(report, sbom_has_components=True)
    assert problem is not None and expected in problem


def test_a_well_formed_report_with_no_findings_is_a_result() -> None:
    from tasks.bomlens_result import trivy_report_problem

    report = _real_report()
    report["Results"] = []
    assert trivy_report_problem(report, sbom_has_components=True) is None
    assert trivy_report_problem(_real_report(), sbom_has_components=True) is None
    # Nothing to match: the artifact type cannot be required of an empty SBOM.
    report.pop("ArtifactType")
    assert trivy_report_problem(report, sbom_has_components=False) is None
