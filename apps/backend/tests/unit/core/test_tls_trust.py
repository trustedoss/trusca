# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The boot report says what our HTTPS clients will actually trust.

Two things are being protected, and they fail in opposite directions.

The count has to be real. Reporting a number that does not come from the
context the clients get would be worse than reporting nothing, because an
operator would act on it. So the tests set the environment and assert the
reported number moves with it.

The warning has to be able to stay quiet. It exists for one case, an override
that dropped the public roots, and firing on a correct setup is how a warning
becomes something people filter out. So there are as many tests for silence as
for noise.

The directory case is the one that would have made this lie. A context built
from ``SSL_CERT_DIR`` loads certificates on demand and reports none until it
does, so a naive count prints zero for a perfectly good directory, and a
size-based warning then fires on every such deployment.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest
import structlog

from core.tls_trust import describe_trust_store, log_trust_store


@pytest.fixture
def one_certificate(tmp_path: pathlib.Path) -> pathlib.Path:
    """A file holding exactly one real, parseable certificate.

    Taken from the bundle httpx ships so it is a genuine certificate rather
    than a fragment. A truncated PEM does not restrict trust, it fails to
    parse and the caller falls back, which is a way of measuring nothing at
    all and reading it as a result.
    """
    import certifi

    blob = pathlib.Path(certifi.where()).read_text()
    first = blob.split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"
    path = tmp_path / "corp-ca.pem"
    path.write_text(first)
    assert "BEGIN CERTIFICATE" in first and first.count("BEGIN CERTIFICATE") == 1
    return path


def test_with_no_override_the_report_names_the_shipped_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    facts = describe_trust_store()

    assert facts["source"] == "bundled"
    assert facts["path"] is None
    assert facts["authorities"] == facts["bundled_authorities"]
    assert facts["authorities"] > 1, (
        "the shipped bundle should hold many authorities; a count of one means "
        "this read something other than the default context"
    )


def test_ssl_cert_file_is_reported_with_the_count_it_produced(
    monkeypatch: pytest.MonkeyPatch, one_certificate: pathlib.Path
) -> None:
    """The number has to come from the context, not from a guess."""
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(one_certificate))

    facts = describe_trust_store()

    assert facts["source"] == "SSL_CERT_FILE"
    assert facts["path"] == str(one_certificate)
    assert facts["authorities"] == 1, (
        "the file holds one certificate, so the context our clients get holds "
        "one; any other number means this is not reading that context"
    )
    assert facts["bundled_authorities"] > 1


def test_a_directory_reports_no_count_rather_than_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, one_certificate: pathlib.Path
) -> None:
    """``SSL_CERT_DIR`` is the case that would have made the report lie.

    A capath context answers ``get_ca_certs()`` with nothing until it needs a
    certificate, so counting gives zero for a directory that trusts plenty.
    Reported as unknown, which is true, instead of as zero, which is not.
    """
    capath = tmp_path / "certs"
    capath.mkdir()
    (capath / "corp-ca.pem").write_bytes(one_certificate.read_bytes())
    digest = subprocess.run(
        ["openssl", "x509", "-hash", "-noout", "-in", str(one_certificate)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not digest:
        pytest.skip("openssl not available to hash the certificate")
    (capath / f"{digest}.0").symlink_to(capath / "corp-ca.pem")

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("SSL_CERT_DIR", str(capath))

    facts = describe_trust_store()

    assert facts["source"] == "SSL_CERT_DIR"
    assert facts["path"] == str(capath)
    assert facts["authorities"] is None


def test_the_file_wins_over_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, one_certificate: pathlib.Path
) -> None:
    """Mirrors httpx's precedence, so it is asserted rather than assumed.

    If a future httpx consulted the directory as well, the count below would
    stop being one and this fails, which is the signal that the report's
    explanation of WHICH setting won has gone stale.
    """
    capath = tmp_path / "certs"
    capath.mkdir()
    monkeypatch.setenv("SSL_CERT_FILE", str(one_certificate))
    monkeypatch.setenv("SSL_CERT_DIR", str(capath))

    facts = describe_trust_store()

    assert facts["source"] == "SSL_CERT_FILE"
    assert facts["authorities"] == 1


def test_dropping_the_public_roots_is_warned_about(
    monkeypatch: pytest.MonkeyPatch, one_certificate: pathlib.Path
) -> None:
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(one_certificate))

    with structlog.testing.capture_logs() as captured:
        facts = log_trust_store(process="test")

    assert facts["authorities"] == 1
    events = [entry["event"] for entry in captured]
    assert "tls_trust.public_roots_dropped" in events
    warning = next(
        e for e in captured if e["event"] == "tls_trust.public_roots_dropped"
    )
    assert warning["authorities"] == 1
    assert warning["bundled_authorities"] > 1
    # The message has to say what to do about it, not only that it happened.
    assert "Concatenate" in warning["action"]


def test_a_bundle_that_keeps_the_public_roots_is_not_warned_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """The operator did the right thing, so nothing should shout at them.

    This is the test that keeps the warning worth reading. Without it the
    condition could be "an override is set", which fires on every correct
    private-CA deployment.
    """
    import certifi

    combined = tmp_path / "corp-plus-public.pem"
    blob = pathlib.Path(certifi.where()).read_text()
    first = blob.split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"
    combined.write_text(first + blob)

    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(combined))

    with structlog.testing.capture_logs() as captured:
        facts = log_trust_store(process="test")

    assert facts["authorities"] >= facts["bundled_authorities"]
    events = [entry["event"] for entry in captured]
    assert "tls_trust.outbound" in events, "the facts are stated either way"
    assert "tls_trust.public_roots_dropped" not in events


def test_no_override_is_not_warned_about(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    with structlog.testing.capture_logs() as captured:
        log_trust_store(process="test")

    events = [entry["event"] for entry in captured]
    assert "tls_trust.outbound" in events
    assert "tls_trust.public_roots_dropped" not in events


def test_the_report_never_stops_a_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A certificate setting that cannot be described must not be fatal."""
    import core.tls_trust as module

    def _explode() -> dict[str, object]:
        raise RuntimeError("no")

    monkeypatch.setattr(module, "describe_trust_store", _explode)

    with structlog.testing.capture_logs() as captured:
        assert log_trust_store(process="test") == {}
    assert "tls_trust.describe_failed" in [e["event"] for e in captured]


# ---------------------------------------------------------------------------
# Every process that reaches the network reports its own trust set
# ---------------------------------------------------------------------------


def test_the_line_says_which_process_it_came_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose gives each service its own environment.

    Without the name, an operator reads one line and concludes the certificate
    is configured everywhere, when it may be configured only where that line
    came from.
    """
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    with structlog.testing.capture_logs() as captured:
        log_trust_store(process="worker")

    line = next(e for e in captured if e["event"] == "tls_trust.outbound")
    assert line["process"] == "worker"


def test_the_warning_says_which_process_too(
    monkeypatch: pytest.MonkeyPatch, one_certificate: pathlib.Path
) -> None:
    """The warning is the line an operator acts on, so it has to name the
    service whose environment needs fixing."""
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(one_certificate))

    with structlog.testing.capture_logs() as captured:
        log_trust_store(process="beat")

    warning = next(
        e for e in captured if e["event"] == "tls_trust.public_roots_dropped"
    )
    assert warning["process"] == "beat"


def test_the_worker_and_beat_hooks_report_their_own_process() -> None:
    """The handlers are called directly, not through Celery's signal machinery.

    Driving the signals would test Celery. What can go wrong here is a handler
    that reports the wrong process name, or one that raises and takes a worker
    boot with it.
    """
    from tasks.tls_trust_boot import _on_beat_init, _on_worker_ready

    with structlog.testing.capture_logs() as captured:
        _on_worker_ready()
        _on_beat_init()

    reported = [e["process"] for e in captured if e["event"] == "tls_trust.outbound"]
    assert reported == ["worker", "beat"]


def test_the_hook_module_is_imported_by_the_worker() -> None:
    """A signal handler in a module nobody imports never registers.

    This is the failure that leaves the process the feature is most for saying
    nothing, and it looks like working code: the module is correct, the
    handlers are correct, and neither ever runs.
    """
    from tasks.celery_app import celery_app

    includes = list(celery_app.conf.include or [])
    assert "tasks.tls_trust_boot" in includes, (
        "the worker will not import the hook module, so neither the worker nor "
        "beat will report its trust set"
    )
