"""The BomLens execution plan reader treats the plan as untrusted input.

Every case starts from a plan the published BomLens image wrote
(``tests/fixtures/bomlens-plan/``, see ``PROVENANCE.md`` there) and changes one
thing in a copy. A mutation test first asserts the copy really differs from
the captured plan where it should, so a mutation that missed its target
cannot pass as a refusal.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from integrations.bomlens import plan as plan_mod
from integrations.bomlens.plan import BomLensPlanRejected, load_plan, parse_plan

PLAN_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "bomlens-plan"


def _captured(case: str = "node-ok") -> dict[str, Any]:
    document: dict[str, Any] = json.loads((PLAN_DIR / case / "bomlens-plan.json").read_text())
    return document


def _stage(document: dict[str, Any], stage_id: str) -> dict[str, Any]:
    return next(s for s in document["stages"] if s["id"] == stage_id)


def _mount(stage: dict[str, Any], role: str) -> dict[str, Any]:
    return next(m for m in stage["mounts"] if m["role"] == role)


# ---------------------------------------------------------------------------
# The captured plans read as BomLens wrote them
# ---------------------------------------------------------------------------


def test_the_captured_npm_plan_reads_as_two_pinned_stages() -> None:
    plan = load_plan(PLAN_DIR / "node-ok")
    assert plan.supported
    assert plan.detected_ecosystem == "node"
    generate, postprocess = plan.stages
    assert (generate.id, generate.stage) == ("generate", 1)
    assert generate.image.startswith("ghcr.io/cdxgen/")
    assert plan_mod.DIGEST_RE.match(generate.image_digest)
    assert generate.user == "0:0"
    assert generate.entrypoint == "sh"
    assert generate.network_required is True
    assert {m.role for m in generate.mounts} == {
        "source",
        "output",
        "plan-file",
        "guard-state",
        "cache-maven",
        "cache-gradle",
    }
    assert generate.dropped_env == ()
    assert (postprocess.id, postprocess.stage) == ("postprocess", 2)
    assert postprocess.env["MODE"] == "POSTPROCESS"
    assert postprocess.env["BOMLENS_RUN_INPUT"] == plan.output_file
    assert postprocess.network_required is False
    assert postprocess.mount("source") is not None
    assert postprocess.mount("source").mode == "ro"  # type: ignore[union-attr]


def test_the_captured_unsupported_plan_has_no_stages() -> None:
    plan = load_plan(PLAN_DIR / "android-unsupported")
    assert not plan.supported
    assert plan.status == "unsupported"
    assert plan.reason == "android-sdk-image"
    assert plan.stages == ()


def test_the_vocabulary_matches_the_schema_bomlens_publishes() -> None:
    """``engine-plan-1.json`` is copied from BomLens beside the captures; every
    enum this reader hard-codes must be the schema's (rule: one vocabulary in
    two places needs a consistency test)."""
    schema = json.loads((PLAN_DIR / "engine-plan-1.json").read_text())
    props = schema["properties"]
    stage_props = props["stages"]["items"]["properties"]
    mount_props = stage_props["mounts"]["items"]["properties"]
    assert set(props["status"]["enum"]) == set(plan_mod.PLAN_STATUSES)
    assert set(props["reason"]["enum"]) - {None} == set(plan_mod.PLAN_REASONS)
    assert set(stage_props["id"]["enum"]) == {i for i, _ in plan_mod.STAGE_IDS}
    assert set(stage_props["stage"]["enum"]) == {n for _, n in plan_mod.STAGE_IDS}
    assert set(mount_props["role"]["enum"]) == set(plan_mod.MOUNT_ROLES)
    assert set(mount_props["mode"]["enum"]) == set(plan_mod.MOUNT_MODES)
    assert props["contract"]["properties"]["name"]["const"] == plan_mod.PLAN_CONTRACT_NAME


def test_every_env_name_the_captured_plan_sets_is_on_the_allow_list() -> None:
    """A BomLens upgrade that adds a variable shows up here, not as a silently
    dropped setting in production."""
    document = _captured()
    for stage in document["stages"]:
        allowed = plan_mod.STAGE_ENV_ALLOWED[stage["stage"]]
        assert set(stage["env"]) <= allowed, stage["id"]


# ---------------------------------------------------------------------------
# Hostile plans
# ---------------------------------------------------------------------------


def _mutated(mutate: Any) -> dict[str, Any]:
    document = _captured()
    changed = copy.deepcopy(document)
    mutate(changed)
    assert changed != document, "the mutation did not change the plan"
    return changed


def _refused(document: dict[str, Any], fragment: str) -> None:
    with pytest.raises(BomLensPlanRejected) as caught:
        parse_plan(document)
    assert fragment in str(caught.value)


def test_a_host_path_on_a_folder_mount_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), "source")["source"] = "/etc"

    _refused(_mutated(mutate), "names a source path")


@pytest.mark.parametrize("source", ["../../../etc/passwd", "/etc/shadow", "bomlens-plan/../x"])
def test_a_plan_file_outside_the_plan_folder_is_refused(source: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        next(m for m in _stage(d, "generate")["mounts"] if m["role"] == "plan-file")["source"] = (
            source
        )

    _refused(_mutated(mutate), "is not a file in bomlens-plan/")


@pytest.mark.parametrize(
    "target", ["/var/run/docker.sock", "/run/docker.sock", "/etc", "/proc/1", "/"]
)
def test_a_mount_over_a_system_path_or_the_docker_socket_is_refused(target: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), "output")["target"] = target

    with pytest.raises(BomLensPlanRejected):
        parse_plan(_mutated(mutate))


def test_a_docker_socket_mount_anywhere_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), "output")["target"] = "/srv/docker.sock"

    _refused(_mutated(mutate), "names the Docker socket")


def test_an_unknown_mount_role_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["mounts"].append(
            {"role": "docker-socket", "target": "/sock", "mode": "rw"}
        )

    _refused(_mutated(mutate), "is not a mount role")


def test_a_writable_source_in_stage_2_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "postprocess"), "source")["mode"] = "rw"

    _refused(_mutated(mutate), "writable in stage 2")


def test_a_writable_plan_file_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        next(m for m in _stage(d, "generate")["mounts"] if m["role"] == "plan-file")["mode"] = "rw"

    _refused(_mutated(mutate), "plan file writable")


def test_a_required_cache_mount_trusca_does_not_provide_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), "cache-maven")["optional"] = False

    _refused(_mutated(mutate), "TRUSCA does not provide")


def test_the_guard_state_in_stage_2_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "postprocess")["mounts"].append(
            {"role": "guard-state", "target": "/bomlens-state", "mode": "rw"}
        )

    _refused(_mutated(mutate), "not allowed in stage 2")


def test_nested_folder_mounts_are_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _mount(_stage(d, "generate"), "output")["target"] = "/app/out"

    _refused(_mutated(mutate), "nests the 'output' mount")


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("image_digest", None, "not pinned by a sha256 digest"),
        ("image_digest", "sha256:abc", "not pinned by a sha256 digest"),
        ("image", "ghcr.io/cdxgen/cdxgen-alpine-node24:latest", "tagged latest"),
        ("image", "ghcr.io/cdxgen/x:1 --privileged", "is not an image name"),
        ("image", "ghcr.io/cdxgen/x@sha256:" + "0" * 64, "is not an image name"),
    ],
)
def test_an_unpinned_or_malformed_stage_image_is_refused(
    field: str, value: Any, fragment: str
) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")[field] = value

    _refused(_mutated(mutate), fragment)


@pytest.mark.parametrize("user", ["root", "0:0 --privileged", "-1", ""])
def test_a_user_that_is_not_numeric_is_refused(user: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["user"] = user

    _refused(_mutated(mutate), "is not a numeric uid[:gid]")


@pytest.mark.parametrize("entrypoint", [["/bin/rm"], ["sh", "-c"], ["python3"], "sh"])
def test_an_entrypoint_that_is_not_a_shell_is_refused(entrypoint: Any) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["entrypoint"] = entrypoint

    _refused(_mutated(mutate), "is not a shell")


@pytest.mark.parametrize("output_file", ["../x_bom.json", "/tmp/x_bom.json", "a/b_bom.json"])
def test_an_output_file_outside_the_output_folder_is_refused(output_file: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["output_file"] = output_file

    _refused(_mutated(mutate), "is not a plain SBOM file name")


def test_a_workdir_outside_the_stage_mounts_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "postprocess")["workdir"] = "/root"

    _refused(_mutated(mutate), "is not its source or output folder")


@pytest.mark.parametrize(
    ("name", "value"),
    [("MODE", "UI"), ("UPLOAD_ENABLED", "true"), ("BOMLENS_RUN_INPUT", "../../x")],
)
def test_stage_2_values_trusca_relies_on_cannot_change(name: str, value: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "postprocess")["env"][name] = value

    _refused(_mutated(mutate), f"stage 2 sets {name}=")


def test_env_names_off_the_allow_list_are_dropped_not_passed() -> None:
    def mutate(d: dict[str, Any]) -> None:
        env = _stage(d, "generate")["env"]
        env["LD_PRELOAD"] = "/tmp/evil.so"
        env["PGPASSWORD"] = "x"

    plan = parse_plan(_mutated(mutate))
    assert "LD_PRELOAD" not in plan.generate.env
    assert "PGPASSWORD" not in plan.generate.env
    assert set(plan.generate.dropped_env) == {"LD_PRELOAD", "PGPASSWORD"}


def test_passthrough_names_off_the_allow_list_are_ignored() -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["env_passthrough"] += ["SECRET_KEY", "DATABASE_URL", "HOST_UID"]

    plan = parse_plan(_mutated(mutate))
    assert not {"SECRET_KEY", "DATABASE_URL", "HOST_UID"} & set(plan.generate.env_passthrough)


@pytest.mark.parametrize("value", ["a\nb", "a\x00b", "x" * 5000])
def test_an_env_value_that_is_not_plain_is_refused(value: str) -> None:
    def mutate(d: dict[str, Any]) -> None:
        _stage(d, "generate")["env"]["HOME"] = value

    _refused(_mutated(mutate), "is not a plain value")


def test_fields_the_plan_cannot_ask_for_are_never_read() -> None:
    """``privileged``, capabilities, devices and a network name are not plan
    fields; a plan that carries them parses, and nothing of them survives."""

    def mutate(d: dict[str, Any]) -> None:
        stage = _stage(d, "generate")
        stage["privileged"] = True
        stage["cap_add"] = ["SYS_ADMIN"]
        stage["devices"] = ["/dev/kmsg"]
        stage["network"]["name"] = "host"

    plan = parse_plan(_mutated(mutate))
    rendered = repr(plan.generate)
    for word in ("privileged", "SYS_ADMIN", "/dev/kmsg", "'host'"):
        assert word not in rendered


def test_a_later_major_contract_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["contract"]["version"] = "2.0.0"

    _refused(_mutated(mutate), "reads 1.x only")


def test_an_unsupported_plan_with_stages_is_refused() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["status"] = "unsupported"
        d["reason"] = "android-sdk-image"

    _refused(_mutated(mutate), "an unsupported plan lists stages")


def test_a_plan_file_that_is_a_symbolic_link_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere.json"
    real.write_text((PLAN_DIR / "node-ok" / "bomlens-plan.json").read_text())
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "bomlens-plan.json").symlink_to(real)
    with pytest.raises(BomLensPlanRejected, match="wrote no plan file"):
        load_plan(tmp_path / "plan")


def test_an_oversized_plan_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bomlens-plan.json").write_text(" " * (plan_mod.MAX_PLAN_BYTES + 1) + "{}")
    with pytest.raises(BomLensPlanRejected, match="larger than any plan"):
        load_plan(tmp_path)
