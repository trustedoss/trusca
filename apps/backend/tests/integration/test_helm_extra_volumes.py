# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
A certificate mounted through the chart reaches every process that goes out.

ER25. The chart could already take extra environment variables, so an operator
on a network with a private certificate authority could set `SSL_CERT_FILE` and
its three siblings, and every one of them named a path the chart had no way to
create: there were no extra volumes, and the default
`readOnlyRootFilesystem: true` rules out writing one in at container start.

What is asserted, and why it is not a string match
--------------------------------------------------
The failure this guards against is not a missing template line, it is a pod
that will not start. A `volumeMounts` entry with no matching `volumes` entry
renders as perfectly good YAML, passes `helm lint`, and is rejected by the API
server. That is exactly what the first version of this change produced on the
backend: the include landed after the volumes list rather than inside it, and a
count of rendered mount paths said four, which was the number expected.

So these render the chart and pair every mount against the volumes of the same
pod, which is the relationship Kubernetes checks. A misplaced include fails
here rather than at deploy time.

Both settings of `readOnlyRootFilesystem` are rendered because beat mounts
nothing when the root filesystem is left writable, so its `volumes:` key is
conditional and an extra volume has to bring the key with it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CHART_DIR = REPO_ROOT / "charts" / "trustedoss"
HELM = shutil.which("helm")

_REQUIRED_SET = [
    "--set",
    "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
    "--set",
    "env.secret.apiKeyHmacSecret=ci-golden-hmac-key-abcdef0123456789",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]

#: The private-CA recipe from values.yaml, as an operator would write it.
_CA_SET = [
    "--set",
    "env.extraVolumes[0].name=corp-ca",
    "--set",
    "env.extraVolumes[0].secret.secretName=corp-ca",
    "--set",
    "env.extraVolumeMounts[0].name=corp-ca",
    "--set",
    "env.extraVolumeMounts[0].mountPath=/etc/ssl/corp-ca.pem",
    "--set",
    "env.extraVolumeMounts[0].subPath=ca-bundle.pem",
    "--set",
    "env.extraVolumeMounts[0].readOnly=true",
]

#: The processes that make outbound calls. frontend and redis do not, and are
#: deliberately left without the mount rather than given one they never read.
_OUTBOUND = {"backend", "beat", "worker-default", "worker-scan"}

pytestmark = pytest.mark.integration


def _render(*extra_set: str) -> list[dict[str, Any]]:
    assert HELM is not None, "caller must skip via pytest.mark.skipif(HELM is None, ...)"
    result = subprocess.run(
        [HELM, "template", "trustedoss-golden", str(CHART_DIR), *_REQUIRED_SET, *extra_set],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    assert docs, "helm rendered nothing, so nothing below is being checked"
    return docs


def _deployments(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if doc.get("kind") != "Deployment":
            continue
        name = doc["metadata"]["name"].replace("trustedoss-golden-trustedoss-", "")
        out[name] = doc["spec"]["template"]["spec"]
    assert out, "no Deployments in the rendered chart"
    return out


def _volume_names(pod: dict[str, Any]) -> set[str]:
    return {v["name"] for v in pod.get("volumes") or []}


def _mount_names(pod: dict[str, Any]) -> set[str]:
    return {
        m["name"]
        for container in pod.get("containers") or []
        for m in (container.get("volumeMounts") or [])
    }


@pytest.mark.skipif(HELM is None, reason="helm not installed")
@pytest.mark.parametrize("sealed", ["true", "false"])
def test_every_mount_has_a_volume_behind_it(sealed: str) -> None:
    """The invariant the API server enforces and YAML does not.

    Checked on every workload, not only the ones this feature touches: a mount
    without its volume is the shape a misplaced template line takes, and it can
    land anywhere.
    """
    docs = _render(
        *_CA_SET,
        "--set",
        f"backend.containerSecurityContext.readOnlyRootFilesystem={sealed}",
        "--set",
        f"beat.containerSecurityContext.readOnlyRootFilesystem={sealed}",
        "--set",
        f"worker.containerSecurityContext.readOnlyRootFilesystem={sealed}",
    )
    for name, pod in _deployments(docs).items():
        orphans = _mount_names(pod) - _volume_names(pod)
        assert not orphans, (
            f"{name} mounts {sorted(orphans)} with no volume of that name. "
            "Kubernetes refuses the pod; helm and YAML do not."
        )


@pytest.mark.skipif(HELM is None, reason="helm not installed")
@pytest.mark.parametrize("sealed", ["true", "false"])
def test_the_certificate_reaches_every_outbound_process(sealed: str) -> None:
    docs = _render(
        *_CA_SET,
        "--set",
        f"backend.containerSecurityContext.readOnlyRootFilesystem={sealed}",
        "--set",
        f"beat.containerSecurityContext.readOnlyRootFilesystem={sealed}",
        "--set",
        f"worker.containerSecurityContext.readOnlyRootFilesystem={sealed}",
    )
    pods = _deployments(docs)
    carrying = {
        name
        for name, pod in pods.items()
        if "corp-ca" in _volume_names(pod) and "corp-ca" in _mount_names(pod)
    }
    assert carrying == _OUTBOUND, (
        f"expected the certificate on {sorted(_OUTBOUND)}, got {sorted(carrying)}. "
        "A process left out reaches the network without it and fails to verify."
    )


@pytest.mark.skipif(HELM is None, reason="helm not installed")
def test_the_mount_keeps_the_options_the_operator_wrote() -> None:
    """subPath and readOnly are not decoration.

    Without subPath the Secret mounts as a directory over the path, and the
    file the certificate variables name does not exist. This is the shape of
    mistake that produces a working-looking chart and a failing deployment.
    """
    pods = _deployments(_render(*_CA_SET))
    for name in sorted(_OUTBOUND):
        mounts = [
            m
            for container in pods[name]["containers"]
            for m in (container.get("volumeMounts") or [])
            if m["name"] == "corp-ca"
        ]
        assert len(mounts) == 1, f"{name}: expected one corp-ca mount, got {len(mounts)}"
        assert mounts[0]["mountPath"] == "/etc/ssl/corp-ca.pem"
        assert mounts[0]["subPath"] == "ca-bundle.pem"
        assert mounts[0]["readOnly"] is True


@pytest.mark.skipif(HELM is None, reason="helm not installed")
@pytest.mark.parametrize("sealed", ["true", "false"])
def test_nothing_appears_when_the_operator_asked_for_nothing(sealed: str) -> None:
    """The default has to stay exactly as it was.

    A chart that grew an empty `volumes: []` or a stray key on every install
    would be a change to deployments that never asked for one.
    """
    docs = _render(
        "--set",
        f"backend.containerSecurityContext.readOnlyRootFilesystem={sealed}",
        "--set",
        f"beat.containerSecurityContext.readOnlyRootFilesystem={sealed}",
        "--set",
        f"worker.containerSecurityContext.readOnlyRootFilesystem={sealed}",
    )
    for name, pod in _deployments(docs).items():
        assert "corp-ca" not in _volume_names(pod)
        assert pod.get("volumes") != [], f"{name} renders an empty volumes list"
