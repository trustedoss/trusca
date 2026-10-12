"""The BomLens runner's container image scan (``MODE=IMAGE``, SCAN_CONTAINER_ENGINE=bomlens).

Every assertion is on the ``docker run`` argument list the runner built (or
on the reference it hands over), which is what the Docker daemon and the
BomLens image would get. The container itself is played by
:class:`tests._bomlens_engine.FakeDocker`.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import structlog

from integrations.bomlens import runner as runner_mod
from integrations.bomlens.contract import MESSAGE_MAX_CHARS
from integrations.bomlens.plan import PROXY_ENV_NAMES
from integrations.bomlens.runner import (
    BomLensEngineError,
    BomLensRunner,
    ContainerOutcome,
    ContainerSpec,
    EngineLayout,
    EngineWroteNoResult,
    NamedVolumeMounts,
    RunReport,
    docker_run_argv,
    image_target,
    output_tail,
    resolve_images,
)
from tests._bomlens_engine import Behaviour, FakeDocker, captured_images, write_result

CASE = "image-registry-alpine"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    scan_dir = root / "scan"
    scan_dir.mkdir(parents=True)
    trivy = tmp_path / "trivy" / "db"
    trivy.mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_HOST_PATH", str(root))
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(tmp_path / "trivy"))
    monkeypatch.setenv("SCAN_DOCKER_VOLUME_STRATEGY", "bind")
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE", captured_images())
    monkeypatch.setenv("SCAN_BOMLENS_NETWORK", "trusca-scan-egress")
    monkeypatch.setenv("SECRET_KEY", "worker-secret-that-must-not-leak-0123456789")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("SYFT_REGISTRY_AUTH_PASSWORD", "worker-env-registry-password")
    monkeypatch.setenv("TRIVY_PASSWORD", "worker-env-trivy-password")
    for name in (*PROXY_ENV_NAMES, "TRIVY_DB_REFRESH_HOURS"):
        monkeypatch.delenv(name, raising=False)
    return scan_dir


def _runner(workspace: Path, docker: FakeDocker) -> BomLensRunner:
    return BomLensRunner(
        scan_uuid=uuid.UUID(int=9),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source"),
        images=resolve_images(deep_license=False),
        project_name="image",
        project_version="1.0.0",
        fetch_license=False,
        docker=docker,
    )


# ---------------------------------------------------------------------------
# The reference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    [
        "alpine",
        "alpine:3.19.0",
        "library/alpine:3.19.0",
        "myorg/app",
        "docker.io/library/alpine:3.19.0",
        "ghcr.io/org/team/app:1.2.3",
        "registry.example:5000/app:v1_2-3",
        "localhost:5000/app",
        "localhost/app:1",
        "alpine@sha256:" + "a" * 64,
        "alpine:3.19.0@sha256:" + "0123456789abcdef" * 4,
        "my-org/my_app__x/sub.part:Tag_1.0-rc",
        # Valid image names that are also syft scheme words: the registry
        # scheme in front is what keeps syft from reading a folder, and makes
        # "registry:alpine" the image "registry" with the tag "alpine".
        "dir:x",
        "registry:alpine",
    ],
)
def test_a_registry_reference_is_handed_over_with_the_registry_scheme(reference: str) -> None:
    assert image_target(reference) == f"registry:{reference}"


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "   ",
        "docker-archive:/w/image.tar",
        "oci-archive:/w/image.tar",
        "oci-dir:/w/layout",
        "dir:/",
        "file:/etc/passwd",
        "docker://alpine",
        "https://registry.example/app:1",
        "user:token@registry.example/app:1",
        "registry.example/App:1",
        "MyOrg/app",
        "alpine:3.19 --privileged",
        "alpine\n:3.19",
        "-alpine",
        "alpine:-3",
        "alpine@sha256:short",
        "alpine@md5:" + "a" * 32,
        "../alpine",
        "alpine/",
        "a" * 600,
    ],
)
def test_anything_else_is_refused_without_echoing_it(reference: str) -> None:
    with pytest.raises(BomLensEngineError, match="not a registry image reference") as raised:
        image_target(reference)
    if reference.strip():
        assert reference not in str(raised.value)


@pytest.mark.parametrize(
    ("host", "internal"),
    [
        ("localhost", True),
        ("localhost:5000", True),
        ("LOCALHOST", True),
        ("registry.localhost", True),
        ("127.0.0.1", True),
        ("127.1:5000", True),
        ("0x7f000001", True),
        ("10.1.2.3:443", True),
        ("169.254.169.254", True),
        ("[::1]:5000", True),
        ("[fe80::1]", True),
        ("8.8.8.8", True),
        ("docker.io", False),
        ("ghcr.io", False),
        ("registry.example:5000", False),
        ("localhost.example.com", False),
        ("deadbeef.example", False),
        ("abc", False),
        ("", False),
    ],
)
def test_an_address_or_the_local_machine_is_an_internal_registry(
    host: str, internal: bool
) -> None:
    from tasks.scan_container_bomlens import is_internal_registry

    assert is_internal_registry(host) is internal


def test_surrounding_space_is_dropped_not_passed_on() -> None:
    assert image_target(" alpine:3.19 ") == "registry:alpine:3.19"


# ---------------------------------------------------------------------------
# The container
# ---------------------------------------------------------------------------


def test_the_image_container_runs_hardened_with_three_mounts(workspace: Path) -> None:
    docker = FakeDocker().behave("image", Behaviour(effect=write_result("image-registry-alpine")))
    runner = _runner(workspace, docker)

    output = runner.run_image("registry:alpine:3.19.0")

    assert output == runner_mod.confined_path(workspace / "bomlens" / "out", workspace.parent)
    (run,) = docker.runs
    assert run.label == "image"
    assert run.image == captured_images()
    assert docker.pulled == [captured_images()]
    assert run.option_values("--user") == [f"{os.getuid()}:{os.getgid()}"]
    assert run.has_flag("--read-only")
    assert run.option_values("--cap-add") == []
    assert run.option_values("--cap-drop") == ["ALL"]
    assert run.option_values("--network") == ["trusca-scan-egress"]
    assert run.option_values("--workdir") == ["/host-output"]
    assert set(run.mounts) == {"/host-output", "/tmp", "/trivy-db"}
    assert run.mounts["/trivy-db"]["readonly"] == "true"
    assert "readonly" not in run.mounts["/host-output"]
    assert run.env["MODE"] == "IMAGE"
    assert run.env["TARGET_IMAGE"] == "registry:alpine:3.19.0"
    assert run.env["UPLOAD_ENABLED"] == "false"
    assert run.env["DEEP_LICENSE"] == "false"
    assert run.env["TRIVY_OFFLINE_SCAN"] == "true"
    # Nothing from the worker's environment: no secret, no registry login
    # (the engine path passes none), no database address.
    joined = " ".join(run.argv)
    for leaked in (
        "worker-secret-that-must-not-leak",
        "postgresql",
        "worker-env-registry-password",
        "worker-env-trivy-password",
    ):
        assert leaked not in joined
        assert leaked not in str(docker.process_envs)
    assert not any(n.startswith(("SYFT_", "DOCKER_")) for n in run.env)
    assert "TRIVY_PASSWORD" not in run.env
    # Removed after it exited.
    assert docker.removed == list(docker.containers)
    report = runner.report.as_metadata()
    assert report == {
        "mode": "image",
        "image_target": "registry:alpine:3.19.0",
        "engine_image": captured_images(),
        "exit_code": 0,
        "timed_out": False,
    }


def test_a_proxy_is_passed_by_name_and_a_loopback_one_not_at_all(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://u:proxy-pass-1@proxy.example:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3128")
    docker = FakeDocker().behave("image", Behaviour(effect=write_result("image-registry-alpine")))

    _runner(workspace, docker).run_image("registry:alpine")

    (run,) = docker.runs
    assert run.by_name == frozenset({"HTTPS_PROXY"})
    assert "HTTP_PROXY" not in run.env
    assert not any("proxy-pass-1" in arg for arg in run.argv)


def test_a_container_past_its_time_limit_is_killed_and_the_run_refused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS", "60")
    docker = FakeDocker().behave("image", Behaviour(hang=True))
    runner = _runner(workspace, docker)

    with pytest.raises(BomLensEngineError, match="stopped at its time limit"):
        runner.run_image("registry:alpine")

    assert docker.killed == list(docker.containers)
    assert docker.removed == list(docker.containers)
    assert runner.report.image_timed_out is True


def test_leftovers_of_an_earlier_attempt_are_removed_first(workspace: Path) -> None:
    docker = FakeDocker().behave("image", Behaviour(effect=write_result("image-registry-alpine")))
    docker.leftovers = ["old-1"]

    _runner(workspace, docker).run_image("registry:alpine")

    assert docker.removed[0] == "old-1"


def test_an_old_docker_api_is_refused_before_anything_runs(workspace: Path) -> None:
    docker = FakeDocker(api_version="1.44")

    with pytest.raises(BomLensEngineError, match="Docker Engine 26"):
        _runner(workspace, docker).run_image("registry:alpine")
    assert docker.runs == []


def test_dropping_all_capabilities_removes_every_added_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCAN_SIDECAR_CAP_ADD", "CHOWN,SETUID")
    monkeypatch.setenv("SCAN_SIDECAR_CAP_DROP", "NET_RAW")
    spec = ContainerSpec(
        name="n",
        image="i@sha256:" + "a" * 64,
        user="1000:1000",
        entrypoint=None,
        args=(),
        workdir=None,
        mounts=(),
        extra_mount_args=(),
        env=(),
        network="none",
        read_only=True,
        drop_all_caps=True,
    )

    class _NoMounts:
        def mount_args(self, *_a: object, **_k: object) -> list[str]:
            return []

        def trivy_db_args(self) -> list[str]:
            return []

    argv = docker_run_argv(spec, _NoMounts(), "ab", 1)
    assert "--cap-add" not in argv
    drops = [argv[i + 1] for i, a in enumerate(argv) if a == "--cap-drop"]
    assert "ALL" in drops
    # The rest of the hardening stays.
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert "--pids-limit" in argv

    # Without the flag the sidecar's minimal set is added back, as before.
    from dataclasses import replace

    default = docker_run_argv(replace(spec, drop_all_caps=False), _NoMounts(), "ab", 1)
    assert [default[i + 1] for i, a in enumerate(default) if a == "--cap-add"] == [
        "CHOWN",
        "SETUID",
    ]


def test_a_source_run_report_is_unchanged() -> None:
    """The image fields do not leak into a source scan's record."""
    keys = set(RunReport().as_metadata())
    assert "mode" not in keys and "image_target" not in keys
    assert "plan_status" in keys


# ---------------------------------------------------------------------------
# A named workspace volume (the compose files)
# ---------------------------------------------------------------------------


def _named_runner(workspace: Path, docker: FakeDocker) -> BomLensRunner:
    style = NamedVolumeMounts(
        volume="trusca_workspace",
        volume_mount=workspace.parent,
        trivy_volume="trusca_trivy-cache",
    )
    return BomLensRunner(
        scan_uuid=uuid.UUID(int=9),
        layout=EngineLayout(scan_dir=workspace, source=workspace / "source"),
        images=resolve_images(deep_license=False),
        project_name="image",
        project_version="1.0.0",
        fetch_license=False,
        docker=docker,
        mounts=style,
    )


def test_on_a_named_volume_no_mount_takes_the_image_s_content(workspace: Path) -> None:
    """Docker copies the image's content, owner and mode into an empty volume
    sub-path unless the mount says volume-nocopy. ``/host-output`` is not in
    the image, so the daemon creates it as root's 0755 and the worker's empty
    output folder became root's: the container, running as the worker's
    non-root user, exited 1 at its first write (Permission denied), with no
    result manifest. Reproduced on Docker 29 with the published image; the
    real-daemon test in tests/integration/scan/test_bomlens_container_engine.py
    runs it."""
    docker = FakeDocker()
    _named_runner(workspace, docker).run_image("registry:alpine")

    (run,) = docker.runs
    assert set(run.mounts) == {"/host-output", "/tmp", "/trivy-db"}
    for target, fields in run.mounts.items():
        assert fields.get("volume-nocopy") == "true", target
        assert fields["type"] == "volume"
    assert run.mounts["/host-output"]["volume-subpath"] == "scan/bomlens/out"
    assert run.mounts["/tmp"]["volume-subpath"] == "scan/bomlens/tmp-image"
    assert run.mounts["/trivy-db"]["src"] == "trusca_trivy-cache"
    assert run.option_values("--user") == [f"{os.getuid()}:{os.getgid()}"]


def test_a_daemon_that_drops_volume_nocopy_is_refused_before_the_container_starts(
    workspace: Path,
) -> None:
    def drop_nocopy(recorded: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for mount in recorded:
            (mount.get("VolumeOptions") or {}).pop("NoCopy", None)
        return recorded

    docker = FakeDocker(record_mounts=drop_nocopy)

    with pytest.raises(BomLensEngineError, match="recorded different mounts"):
        _named_runner(workspace, docker).run_image("registry:alpine")
    assert docker.started == []
    assert docker.removed == list(docker.containers)


# ---------------------------------------------------------------------------
# A run that left no result manifest says why
# ---------------------------------------------------------------------------

_SECRET_URL_PASSWORD = "url-pass-5b9e"
_SECRET_TOKEN = "ghp_" + "Q" * 36
_SECRET_HIDDEN = "zero-width-secret-71c2"

#: The published image's output when its output folder was not writable
#: (the compose reproduction), with credential-shaped lines around it.
_FAILED_OUTPUT = (
    b"==========================================\n"
    b" Mode: IMAGE\n"
    b"[1/2] syft: image registry:docker.io/library/alpine:3.20 (no Docker socket)\n"
    b"GET https://bot:" + _SECRET_URL_PASSWORD.encode() + b"@registry.example/v2/ 401\n"
    b"using " + _SECRET_TOKEN.encode() + b"\x00 for the registry\n"
    # A zero-width space inside the key word would hide it from the patterns.
    b"pass\xe2\x80\x8bword=" + _SECRET_HIDDEN.encode() + b"\n"
    b"/usr/local/bin/run-scan: line 733: image_1_bom.json: Permission denied\n"
    b"[ERROR] syft could not read the image registry:docker.io/library/alpine:3.20: "
    b"it does not exist, or the registry needs credentials\n"
    b"/usr/local/lib/sbom/engine-result.sh: line 255: "
    b"/host-output/.bomlens-result.json.tmp.1: Permission denied\n"
    b"\n"
)


def _assert_no_secret(text: str) -> None:
    for secret in (_SECRET_URL_PASSWORD, _SECRET_TOKEN, _SECRET_HIDDEN):
        assert secret not in text


def test_an_image_run_without_a_manifest_reports_its_exit_and_last_lines(
    workspace: Path,
) -> None:
    docker = FakeDocker().behave("image", Behaviour(exit_code=1, output=_FAILED_OUTPUT))
    runner = _runner(workspace, docker)
    output = runner.run_image("registry:alpine")

    with structlog.testing.capture_logs() as logs:
        with pytest.raises(EngineWroteNoResult) as caught:
            runner.require_result(output)

    message = str(caught.value)
    assert message.startswith(
        "BomLens MODE=IMAGE exited 1 without writing a result manifest; last output: "
    )
    # The last three lines, each cut short, in order.
    assert "run-scan: line 733: image_1_bom.json: Permission denied | [ERROR] syft" in message
    assert message.endswith("...")  # capped at the error's length limit
    assert len(message) <= MESSAGE_MAX_CHARS
    _assert_no_secret(message)
    assert "\x00" not in message

    (event,) = [e for e in logs if e["event"] == "bomlens_engine_no_result"]
    assert (event["log_level"], event["stage"], event["exit_code"]) == ("error", "trivy", 1)
    tail = event["output_tail"]
    assert tail[-1] == (
        "/usr/local/lib/sbom/engine-result.sh: line 255: "
        "/host-output/.bomlens-result.json.tmp.1: Permission denied"
    )
    assert "Mode: IMAGE" in tail
    assert "" not in tail
    joined = "\n".join(tail)
    _assert_no_secret(joined)
    assert "GET https://***@registry.example/v2/ 401" in tail
    assert "using ghp_*** for the registry" in tail
    assert "password=***" in tail
    assert "\x00" not in joined and "​" not in joined


def test_an_image_run_with_a_manifest_is_left_to_the_reader(workspace: Path) -> None:
    docker = FakeDocker().behave(
        "image", Behaviour(exit_code=1, output=b"x\n", effect=write_result(CASE))
    )
    runner = _runner(workspace, docker)
    output = runner.run_image("registry:alpine")

    runner.require_result(output)  # the manifest reader decides


def test_a_run_whose_result_container_never_exited_is_not_judged(workspace: Path) -> None:
    runner = _runner(workspace, FakeDocker())
    runner.require_result(workspace / "bomlens" / "out")


def test_the_reason_names_a_time_limit_and_a_memory_kill(workspace: Path) -> None:
    runner = _runner(workspace, FakeDocker())
    output = workspace / "out"
    output.mkdir()
    for outcome, ended in (
        (ContainerOutcome(137, True, False, b""), "BomLens stage 2 ran out of memory"),
        (ContainerOutcome(137, False, True, b""), "BomLens stage 2 was stopped at its time limit"),
        (ContainerOutcome(2, False, False, b""), "BomLens stage 2 exited 2"),
    ):
        runner._result_run = (outcome, "stage 2", "postprocess")
        with pytest.raises(EngineWroteNoResult) as caught:
            runner.require_result(output)
        # No output: no "last output" part.
        assert str(caught.value) == f"{ended} without writing a result manifest"


def test_the_tail_keeps_the_last_lines_and_cuts_each_one() -> None:
    output = b"".join(f"line {n}\n".encode() for n in range(50))
    assert output_tail(output, lines=3) == ["line 47", "line 48", "line 49"]
    # Under the pre-cut (4 x line_chars), cut to line_chars after cleaning.
    assert output_tail(b"a" * 30 + b" b\n", line_chars=10) == ["a" * 10]


def test_a_line_cut_before_cleaning_loses_the_word_the_cut_fell_in() -> None:
    """A credential split by the cut no longer matches a pattern, so the word
    goes with the cut instead of reaching the log half-redacted."""
    secret = "https://bot:" + "p" * 40 + "@registry.example"
    line = ("word " * 7 + secret).encode()
    # line_chars=10: the cut is at 40 characters, inside the credential.
    assert output_tail(line + b"\n", line_chars=10) == ["word word "]
    # One word longer than the cut: nothing of it is kept.
    assert output_tail(b"z" * 100 + b"\n", line_chars=10) == []


def test_the_first_line_of_a_longer_output_is_dropped_its_start_is_missing() -> None:
    window = runner_mod.OUTPUT_TAIL_WINDOW_BYTES
    head = b"bot:hidden-start-of-a-credential@registry.example\n"
    body = b"x" * (window - len(head) + 10)
    tail = output_tail(b"https://" + head + body + b"\nlast\n")
    assert tail == ["last"]


@pytest.mark.parametrize(
    ("assignment", "kept"),
    [
        ('err "password": "', 'err "password":'),
        # Gradle's credentials block: no "=" or ":", only the quotes.
        ('password "', "password"),
    ],
    ids=["json", "gradle-dsl"],
)
def test_a_quoted_value_the_cut_fell_in_is_dropped_from_its_opening_quote(
    assignment: str, kept: str
) -> None:
    """The default cut is at 1200 characters. Spaces before it fold away when
    the line is cleaned, so what was near the cut lands inside the stored 300."""
    limit = runner_mod.OUTPUT_TAIL_LINE_CHARS * 4
    pad = "pad" + " " * (limit - 40 - 3)
    secret = "hunter2-6f0d"
    line = pad + assignment + secret + " more " + "y" * 100 + '"'
    assert len(pad + assignment + secret) < limit < len(line)

    (cleaned,) = output_tail(line.encode() + b"\n")

    assert secret not in cleaned
    assert cleaned == f"pad {kept}"


def test_a_vertical_tab_does_not_part_a_key_from_its_value() -> None:
    """Lines are split on line feeds only, as the log readers split them."""
    tail = output_tail(b"password=\x0bsecret-vt-3a9e\nnext\x0cline\n")
    assert tail == ["password=***", "nextline"]


# ---------------------------------------------------------------------------
# The administrator guide says what the code does (hardening rule 4)
# ---------------------------------------------------------------------------

_DOCS = Path(__file__).resolve().parents[6] / "docs-site"
_GUIDES = (
    _DOCS / "docs" / "admin-guide" / "dynamic-scan-executor.md",
    _DOCS
    / "i18n"
    / "ko"
    / "docusaurus-plugin-content-docs"
    / "current"
    / "admin-guide"
    / "dynamic-scan-executor.md",
)


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_quotes_the_private_registry_refusal_as_the_scan_reports_it(
    guide: Path,
) -> None:
    from tasks.scan_container_bomlens import credential_refusal, internal_registry_refusal

    text = guide.read_text(encoding="utf-8")
    assert credential_refusal("<host>") in text
    assert internal_registry_refusal("<host>") in text


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_gives_the_image_scan_settings_their_code_defaults(
    guide: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.config import bomlens_image_scan_timeout_seconds, scan_container_engine

    monkeypatch.delenv("SCAN_CONTAINER_ENGINE", raising=False)
    monkeypatch.delenv("SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS", raising=False)
    rows = {
        line.split("|")[1].strip(): line.split("|")[2].strip()
        for line in guide.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `SCAN_")
    }
    assert rows["`SCAN_CONTAINER_ENGINE`"] == f"`{scan_container_engine()}`"
    assert rows["`SCAN_BOMLENS_IMAGE_TIMEOUT_SECONDS`"] == (
        f"`{bomlens_image_scan_timeout_seconds()}`"
    )


#: How each guide states the two output-tail limits.
_TAIL_LIMITS = {
    "en": (
        "up to {lines} lines of {chars} characters each",
        "up to {lines} lines of {chars} characters",
    ),
    "ko": ("최대 {lines}줄(줄마다 {chars}자)", "최대 {lines}줄(줄마다 {chars}자)"),
}


@pytest.mark.parametrize("guide", _GUIDES, ids=["en", "ko"])
def test_the_guide_describes_a_run_without_a_result_as_the_scan_reports_it(
    guide: Path, workspace: Path
) -> None:
    runner = _runner(workspace, FakeDocker())
    runner._result_run = (ContainerOutcome(1, False, False, b"x\n"), "MODE=IMAGE", "trivy")
    output = workspace / "out"
    output.mkdir()
    with pytest.raises(EngineWroteNoResult) as caught:
        runner.require_result(output)
    # The scan's error as the task stores it, up to the output lines.
    prefix = "scan engine: " + str(caught.value).removesuffix("x")

    text = guide.read_text(encoding="utf-8")
    assert f"`{prefix}... Permission denied`" in text
    assert "`bomlens_engine_no_result`" in text
    assert "`volume-nocopy`" in text
    short, full = _TAIL_LIMITS["ko" if "/i18n/ko/" in str(guide) else "en"]
    lines, chars = runner_mod.ERROR_TAIL_LINES, runner_mod.ERROR_TAIL_LINE_CHARS
    assert short.format(lines=lines, chars=chars) in text
    lines, chars = runner_mod.OUTPUT_TAIL_LINES, runner_mod.OUTPUT_TAIL_LINE_CHARS
    assert full.format(lines=lines, chars=chars) in text
