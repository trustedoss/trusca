# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The BomLens engine worker in the Helm chart (worker.engine).

Renders ``charts/trustedoss`` with ``helm template`` (skipped, not failed, when
``helm`` is unavailable, like the other chart golden tests) and pins what the
engine's isolation rests on: the engine worker is the only workload with a
ServiceAccount token and a Role, it is the only consumer of the engine queue,
its Jobs have no token, the Jobs that must have no network get none, and a
values file that would leave the engine without its prerequisites is refused
when it renders.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CHART_DIR = REPO_ROOT / "charts" / "trustedoss"
CONTRACT = json.loads((REPO_ROOT / "tests" / "contracts" / "queue-names.json").read_text())
HELM = shutil.which("helm")

pytestmark = pytest.mark.skipif(HELM is None, reason="helm is not installed")

_REQUIRED = {
    "env": {
        "secret": {
            "secretKey": "ci-golden-secret-key-0123456789abcdef",
            "apiKeyHmacSecret": "ci-golden-hmac-key-abcdef0123456789",
            "githubAppEncryptionKey": "ci-golden-github-app-key-YWJjZGVmMDEyMzQ1Njc4OQ==",
        }
    },
    "postgres": {"auth": {"password": "ci-golden-pw"}},
    "ingress": {"host": "trustedoss.ci-golden.example.com"},
}

_API_EGRESS = [
    {
        "to": [{"ipBlock": {"cidr": "10.0.0.1/32"}}],
        "ports": [{"port": 6443, "protocol": "TCP"}],
    }
]

_ENGINE = {
    "worker": {"engine": {"enabled": True}},
    "env": {"scan": {"engine": "bomlens", "containerEngine": "bomlens"}},
    "workspace": {"persistence": {"enabled": True}},
    "trivyCache": {"persistence": {"enabled": True}},
    "networkPolicy": {"enabled": True, "workerEngine": {"apiServerEgress": _API_EGRESS}},
}


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _render(tmp_path: Path, *overrides: dict[str, Any]) -> list[dict[str, Any]]:
    values: dict[str, Any] = _REQUIRED
    for override in overrides:
        values = _merge(values, override)
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(values))
    assert HELM is not None
    done = subprocess.run(  # noqa: S603 - fixed argv, test only
        [HELM, "template", "t", str(CHART_DIR), "-f", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr)
    return [d for d in yaml.safe_load_all(done.stdout) if isinstance(d, dict)]


def _of(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d["kind"] == kind]


def _named(docs: list[dict[str, Any]], kind: str, suffix: str) -> dict[str, Any]:
    (found,) = [d for d in _of(docs, kind) if d["metadata"]["name"].endswith(suffix)]
    return found


def _env(deployment: dict[str, Any]) -> dict[str, Any]:
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e for e in container.get("env", [])}


def test_the_engine_is_off_by_default_and_adds_nothing(tmp_path: Path) -> None:
    docs = _render(tmp_path)
    assert not [d for d in docs if "engine" in d["metadata"]["name"]]
    assert not _of(docs, "Role") and not _of(docs, "RoleBinding")
    configmap = _named(docs, "ConfigMap", "-env")
    assert configmap["data"]["SCAN_ENGINE"] == "inprocess"
    assert configmap["data"]["SCAN_CONTAINER_ENGINE"] == "inprocess"


def test_only_the_engine_worker_has_a_service_account_token(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    with_token = [
        d["metadata"]["name"]
        for d in _of(docs, "Deployment")
        if d["spec"]["template"]["spec"].get("automountServiceAccountToken") is True
    ]
    assert with_token == ["t-trustedoss-worker-engine"]
    # Every ServiceAccount says false; the engine worker's pod overrides it.
    for account in _of(docs, "ServiceAccount"):
        assert account["automountServiceAccountToken"] is False, account["metadata"]["name"]


def test_the_role_is_the_runner_packages_set_and_is_bound_to_the_engine_worker_only(
    tmp_path: Path,
) -> None:
    docs = _render(tmp_path, _ENGINE)
    (role,) = _of(docs, "Role")
    rules = {(tuple(r["apiGroups"]), tuple(r["resources"])): set(r["verbs"]) for r in role["rules"]}
    assert rules == {
        (("batch",), ("jobs",)): {"create", "get", "list", "delete"},
        (("",), ("pods",)): {"get", "list", "delete"},
        (("",), ("pods/log",)): {"get"},
    }
    (binding,) = _of(docs, "RoleBinding")
    assert binding["roleRef"]["name"] == role["metadata"]["name"]
    assert [s["name"] for s in binding["subjects"]] == ["t-trustedoss-worker-engine"]
    # Nothing here can reach beyond the namespace.
    assert not _of(docs, "ClusterRole") and not _of(docs, "ClusterRoleBinding")


def test_the_engine_worker_is_the_only_consumer_of_the_engine_queue(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    queue = CONTRACT["engine_queue"]
    consumers = []
    for deployment in _of(docs, "Deployment"):
        command = deployment["spec"]["template"]["spec"]["containers"][0].get("command", [])
        if "-Q" in command and queue in command[command.index("-Q") + 1].split(","):
            consumers.append(deployment["metadata"]["name"])
    assert consumers == ["t-trustedoss-worker-engine"]
    engine = _named(docs, "Deployment", "-worker-engine")
    command = engine["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command[command.index("-Q") + 1].split(",") == CONTRACT["worker_subscriptions"]["engine"]


def test_the_engine_worker_runs_on_kubernetes_with_the_claims_the_jobs_mount(
    tmp_path: Path,
) -> None:
    docs = _render(tmp_path, _ENGINE)
    engine = _named(docs, "Deployment", "-worker-engine")
    env = _env(engine)
    assert env["SCAN_ENGINE_BACKEND"]["value"] == "kubernetes"
    assert env["CELERY_WORKER_REMOTE_CONTROL"]["value"] == "false"
    assert env["SCAN_ENGINE_K8S_NAMESPACE"]["valueFrom"]["fieldRef"]["fieldPath"] == (
        "metadata.namespace"
    )
    volumes = {v["name"]: v for v in engine["spec"]["template"]["spec"]["volumes"]}
    assert volumes["workspace"]["persistentVolumeClaim"]["claimName"] == (
        env["SCAN_ENGINE_K8S_WORKSPACE_CLAIM"]["value"]
    )
    assert volumes["trivy-cache"]["persistentVolumeClaim"]["claimName"] == (
        env["SCAN_ENGINE_K8S_TRIVY_CLAIM"]["value"]
    )
    # The worker reads no database from the Trivy claim.
    assert volumes["trivy-cache"]["persistentVolumeClaim"]["readOnly"] is True


def test_the_jobs_run_as_an_account_with_no_role_and_no_token(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    env = _env(_named(docs, "Deployment", "-worker-engine"))
    job_account = env["SCAN_ENGINE_K8S_SERVICE_ACCOUNT"]["value"]
    names = [a["metadata"]["name"] for a in _of(docs, "ServiceAccount")]
    assert job_account in names
    (binding,) = _of(docs, "RoleBinding")
    assert job_account not in [s["name"] for s in binding["subjects"]]
    assert job_account != "t-trustedoss-worker-engine"


def test_the_other_workloads_know_nothing_of_the_kubernetes_backend(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    for deployment in _of(docs, "Deployment"):
        if deployment["metadata"]["name"].endswith("-worker-engine"):
            continue
        assert not [n for n in _env(deployment) if n.startswith("SCAN_ENGINE_K8S_")], (
            deployment["metadata"]["name"]
        )
        assert "SCAN_ENGINE_BACKEND" not in _env(deployment)


def test_every_pod_is_told_which_engine_to_use(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    configmap = _named(docs, "ConfigMap", "-env")
    assert configmap["data"]["SCAN_ENGINE"] == "bomlens"
    assert configmap["data"]["SCAN_CONTAINER_ENGINE"] == "bomlens"


def test_jobs_without_a_network_get_no_ingress_and_no_egress(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    isolated = _named(docs, "NetworkPolicy", "-engine-jobs-isolated")
    assert isolated["spec"]["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/part-of": "bomlens-engine",
        "bomlens.engine/network": "none",
    }
    assert set(isolated["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert isolated["spec"]["ingress"] == [] and isolated["spec"]["egress"] == []


def test_jobs_with_a_network_reach_dns_and_the_registries_only(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    registries = _named(docs, "NetworkPolicy", "-engine-jobs-registries")
    assert registries["spec"]["podSelector"]["matchLabels"]["bomlens.engine/network"] == "egress"
    assert registries["spec"]["ingress"] == []
    ports = {p["port"] for rule in registries["spec"]["egress"] for p in rule["ports"]}
    assert ports == {53, 443, 80}
    # The private ranges, link-local (cloud metadata) and loopback are excluded.
    blocks = [
        peer["ipBlock"]
        for rule in registries["spec"]["egress"]
        for peer in rule["to"]
        if "ipBlock" in peer
    ]
    excluded = {cidr for block in blocks for cidr in block.get("except", [])}
    assert {"10.0.0.0/8", "169.254.0.0/16", "127.0.0.0/8"} <= excluded


def test_the_engine_worker_reaches_the_api_server_and_not_the_internet(tmp_path: Path) -> None:
    docs = _render(tmp_path, _ENGINE)
    policy = _named(docs, "NetworkPolicy", "-worker-engine")
    assert policy["spec"]["ingress"] == []
    blocks = [
        peer["ipBlock"]["cidr"]
        for rule in policy["spec"]["egress"]
        for peer in rule["to"]
        if "ipBlock" in peer
    ]
    assert blocks == ["10.0.0.1/32"]


def test_a_credentials_secret_is_made_from_values_and_named_for_the_jobs(
    tmp_path: Path,
) -> None:
    docs = _render(
        tmp_path,
        _ENGINE,
        {"worker": {"engine": {"credentials": {"data": {"HTTPS_PROXY": "http://u:p@proxy:3128"}}}}},
    )
    secret = _named(docs, "Secret", "-engine-credentials")
    assert secret["stringData"] == {"HTTPS_PROXY": "http://u:p@proxy:3128"}
    env = _env(_named(docs, "Deployment", "-worker-engine"))
    assert env["SCAN_ENGINE_K8S_SECRET_NAME"]["value"] == secret["metadata"]["name"]


def test_an_existing_credentials_secret_is_referenced_and_none_is_created(
    tmp_path: Path,
) -> None:
    docs = _render(
        tmp_path, _ENGINE, {"worker": {"engine": {"credentials": {"existingSecret": "mine"}}}}
    )
    secrets = [d["metadata"]["name"] for d in _of(docs, "Secret")]
    assert not [name for name in secrets if name.endswith("-engine-credentials")]
    env = _env(_named(docs, "Deployment", "-worker-engine"))
    assert env["SCAN_ENGINE_K8S_SECRET_NAME"]["value"] == "mine"


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"worker": {"engine": {"enabled": False}}}, "worker.engine.enabled is false"),
        ({"workspace": {"persistence": {"enabled": False}}}, "workspace.persistence.enabled"),
        ({"trivyCache": {"persistence": {"enabled": False}}}, "trivyCache.persistence.enabled"),
        ({"networkPolicy": {"enabled": False}}, "networkPolicy.enabled"),
        ({"networkPolicy": {"workerEngine": {"apiServerEgress": []}}}, "apiServerEgress"),
        (
            {"worker": {"engine": {"credentials": {"existingSecret": "a", "data": {"K": "v"}}}}},
            "not both",
        ),
    ],
    ids=[
        "scans-queued-for-no-worker",
        "no-workspace-volume",
        "no-trivy-volume",
        "no-network-policy",
        "no-api-server-rule",
        "two-credential-sources",
    ],
)
def test_a_values_file_the_engine_cannot_work_with_is_refused_with_the_reason(
    tmp_path: Path, override: dict[str, Any], reason: str
) -> None:
    with pytest.raises(RuntimeError, match=reason):
        _render(tmp_path, _ENGINE, override)
