# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
#572: the Helm chart's backup store, the optional worker-scan NetworkPolicy,
the optional ServiceMonitor, and optional per-image digests.

Renders ``charts/trustedoss`` with ``helm template`` (skipped, not failed,
when ``helm`` is unavailable, the same convention as the other chart render
tests; CI installs helm on the backend-integration leg).

The backup half is the one with a defect behind it. ``BACKUPS_ROOT`` was
unset, so ``services.backup_service.backups_root`` resolved the relative
default ``"backups"`` against ``/app``, which ``readOnlyRootFilesystem: true``
seals: the nightly backup had nowhere to write. What has to hold is that every
pod that can run a backup task, and the backend that serves the results, agree
on one path backed by one volume. Which pods can run a backup task is read
from the Celery router itself, not restated here, so a change to routing that
moves the task to another queue fails this file.

The other three are opt-in. Their first contract is that a default install
renders none of them, so the "off" cases are asserted as carefully as "on".
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
CHART_DIR = REPO_ROOT / "charts" / "trustedoss"

HELM = shutil.which("helm")

pytestmark = pytest.mark.skipif(HELM is None, reason="helm binary not available")

_REQUIRED_SET = [
    "--set",
    "env.secret.secretKey=ci-golden-secret-key-0123456789abcdef",
    "--set",
    "env.secret.apiKeyHmacSecret=ci-golden-hmac-key-abcdef0123456789",
    "--set",
    "env.secret.githubAppEncryptionKey=ci-golden-github-app-key-YWJjZGVmMDEyMzQ1Njc4OQ==",
    "--set",
    "postgres.auth.password=ci-golden-pw",
    "--set",
    "ingress.host=trustedoss.ci-golden.example.com",
]

_EXTERNAL_DATASTORES = [
    "--set",
    "postgres.bundled=false",
    "--set",
    "redis.bundled=false",
    "--set",
    "env.database.url=postgresql+asyncpg://u:p@db:5432/t",
    "--set",
    "env.redis.url=redis://r:6379/0",
]

_RELEASE = "trustedoss-golden"
_FULLNAME = f"{_RELEASE}-trustedoss"

_DIGEST = {
    "backend": "sha256:" + "1" * 64,
    "worker": "sha256:" + "2" * 64,
    "frontend": "sha256:" + "3" * 64,
    "postgres": "sha256:" + "4" * 64,
    "redis": "sha256:" + "5" * 64,
}


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    assert HELM is not None
    cmd = [HELM, "template", _RELEASE, str(CHART_DIR), *_REQUIRED_SET, *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


def _render(*extra: str) -> list[dict[str, Any]]:
    result = _run(*extra)
    assert (
        result.returncode == 0
    ), f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _render_fails(*extra: str) -> str:
    result = _run(*extra)
    assert result.returncode != 0, "expected the render to fail, it succeeded"
    return result.stderr


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"] == name:
            return doc
    raise AssertionError(f"{kind}/{name} not rendered; got {_inventory(docs)}")


def _inventory(docs: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(doc["kind"], doc["metadata"]["name"]) for doc in docs}


def _container(deployment: dict[str, Any]) -> dict[str, Any]:
    containers: list[dict[str, Any]] = deployment["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1
    return containers[0]


def _env(deployment: dict[str, Any]) -> dict[str, Any]:
    return {e["name"]: e for e in _container(deployment).get("env", [])}


def _mounts(deployment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in _container(deployment).get("volumeMounts", [])}


def _volumes(deployment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]}


def _render_notes(*extra: str) -> str:
    """Render NOTES.txt without a cluster.

    `helm template` never prints NOTES.txt, and `helm install --dry-run`
    needs a reachable cluster on Helm 3 (see test_helm_notes_connection_budget.py).
    So the chart is copied and NOTES.txt is wrapped in a named template that a
    probe ConfigMap includes: the text goes through the same template engine,
    values and helpers as at install time, and comes back as a string.
    """
    assert HELM is not None
    with tempfile.TemporaryDirectory() as tmp:
        chart = Path(tmp) / "trustedoss"
        shutil.copytree(CHART_DIR, chart)
        templates = chart / "templates"
        notes = (templates / "NOTES.txt").read_text()
        (templates / "NOTES.txt").unlink()
        (templates / "_notes_probe.tpl").write_text(
            '{{- define "probe.notes" -}}\n' + notes + "\n{{- end -}}\n"
        )
        (templates / "notes-probe.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: notes-probe\n"
            'data:\n  notes: {{ include "probe.notes" . | toJson }}\n'
        )
        cmd = [
            HELM,
            "template",
            _RELEASE,
            str(chart),
            "--show-only",
            "templates/notes-probe.yaml",
            *_REQUIRED_SET,
            *extra,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    assert (
        result.returncode == 0
    ), f"helm template failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    rendered: str = yaml.safe_load(result.stdout)["data"]["notes"]
    assert "helm upgrade" in rendered, "probe did not render NOTES.txt"
    return rendered


def _warnings(notes: str) -> list[str]:
    """Each WARNING paragraph of rendered NOTES, as one line."""
    return [
        " ".join(block.split())
        for block in notes.split("\n\n")
        if block.lstrip().startswith("WARNING:")
    ]


def _values() -> dict[str, Any]:
    values: dict[str, Any] = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
    return values


# ---------------------------------------------------------------------------
# Defaults: the three opt-in features render nothing.
# ---------------------------------------------------------------------------


def test_default_install_renders_no_networkpolicy_or_servicemonitor() -> None:
    kinds = {kind for kind, _ in _inventory(_render())}
    assert "NetworkPolicy" not in kinds
    assert "ServiceMonitor" not in kinds


def test_default_install_leaves_metrics_off_and_tokenless() -> None:
    docs = _render()
    configmap = _find(docs, "ConfigMap", f"{_FULLNAME}-env")
    assert "METRICS_ENABLED" not in configmap["data"]
    backend = _find(docs, "Deployment", f"{_FULLNAME}-backend")
    assert "METRICS_TOKEN" not in _env(backend)


# ---------------------------------------------------------------------------
# Backups (#572)
# ---------------------------------------------------------------------------


def _backup_task_queues() -> set[str]:
    """Where Celery actually routes the two backup tasks."""
    from tasks.celery_app import celery_app

    return {
        celery_app.amqp.router.route({}, name)["queue"].name
        for name in ("trustedoss.backup.run", "trustedoss.backup.restore")
    }


def _workers_that_take(queues: set[str], *extra: str) -> set[str]:
    """Worker Deployments whose `-Q` list contains any of ``queues``."""
    takers = set()
    for kind in ("scan", "default"):
        deployment = _find(_render(*extra), "Deployment", f"{_FULLNAME}-worker-{kind}")
        command = _container(deployment)["command"]
        subscribed = set(command[command.index("-Q") + 1].split(","))
        if subscribed & queues:
            takers.add(kind)
    return takers


def test_backup_tasks_route_to_the_queue_the_chart_treats_as_default() -> None:
    """The chart decides which workers mount the store by looking for
    worker.queues.default in each subscription list. That is only right while
    Celery routes both backup tasks there."""
    assert _backup_task_queues() == {_values()["worker"]["queues"]["default"]}


def test_every_pod_that_can_run_a_backup_task_and_the_backend_share_one_store() -> None:
    docs = _render()
    mount_path = _values()["backups"]["mountPath"]
    takers = _workers_that_take(_backup_task_queues())
    # At chart defaults both worker kinds consume the default queue.
    assert takers == {"scan", "default"}

    names = [f"{_FULLNAME}-backend"] + [f"{_FULLNAME}-worker-{k}" for k in sorted(takers)]
    claims = set()
    for name in names:
        deployment = _find(docs, "Deployment", name)
        env = _env(deployment)
        assert env.get("BACKUPS_ROOT", {}).get("value") == mount_path, (
            f"{name}: BACKUPS_ROOT must be {mount_path!r}; without it the backup "
            "service writes under /app, which the read-only root filesystem seals"
        )
        assert _mounts(deployment)["backups"]["mountPath"] == mount_path, name
        assert not _mounts(deployment)["backups"].get("readOnly"), name
        claims.add(_volumes(deployment)["backups"]["persistentVolumeClaim"]["claimName"])
    assert claims == {f"{_FULLNAME}-backups"}, f"pods disagree on the backup claim: {claims}"

    pvc = _find(docs, "PersistentVolumeClaim", f"{_FULLNAME}-backups")
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]
    assert pvc["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def test_beat_does_not_mount_the_backup_store() -> None:
    """Beat only schedules the task; it never opens a backup."""
    beat = _find(_render(), "Deployment", f"{_FULLNAME}-beat")
    assert "BACKUPS_ROOT" not in _env(beat)
    assert "backups" not in _mounts(beat)


def test_narrowing_worker_scan_to_the_scan_queue_drops_its_backup_mount() -> None:
    """A backup is a full database dump, and worker-scan runs scanned projects'
    build scripts. Once worker-scan can no longer receive a backup task it must
    not keep the store mounted."""
    narrowed = ("--set", "worker.subscriptions.scan={trustedoss.scan}")
    assert _workers_that_take(_backup_task_queues(), *narrowed) == {"default"}
    docs = _render(*narrowed)
    scan = _find(docs, "Deployment", f"{_FULLNAME}-worker-scan")
    assert "BACKUPS_ROOT" not in _env(scan)
    assert "backups" not in _mounts(scan)
    assert "backups" not in _volumes(scan)
    default = _find(docs, "Deployment", f"{_FULLNAME}-worker-default")
    assert "backups" in _mounts(default)


def test_backup_persistence_disabled_falls_back_to_emptydir_without_a_pvc() -> None:
    docs = _render("--set", "backups.persistence.enabled=false")
    assert ("PersistentVolumeClaim", f"{_FULLNAME}-backups") not in _inventory(docs)
    backend = _find(docs, "Deployment", f"{_FULLNAME}-backend")
    assert _volumes(backend)["backups"] == {"name": "backups", "emptyDir": {}}


def test_backup_existing_claim_is_used_and_nothing_is_provisioned() -> None:
    docs = _render("--set", "backups.persistence.existingClaim=site-backups")
    assert ("PersistentVolumeClaim", f"{_FULLNAME}-backups") not in _inventory(docs)
    for name in ("backend", "worker-default", "worker-scan"):
        deployment = _find(docs, "Deployment", f"{_FULLNAME}-{name}")
        claim = _volumes(deployment)["backups"]["persistentVolumeClaim"]["claimName"]
        assert claim == "site-backups", name


def test_backup_pvc_size_class_and_keep_policy_are_configurable() -> None:
    docs = _render(
        "--set",
        "backups.persistence.size=200Gi",
        "--set",
        "backups.persistence.storageClassName=nfs-rwx",
        "--set",
        "backups.persistence.keepOnUninstall=false",
    )
    pvc = _find(docs, "PersistentVolumeClaim", f"{_FULLNAME}-backups")
    assert pvc["spec"]["resources"]["requests"]["storage"] == "200Gi"
    assert pvc["spec"]["storageClassName"] == "nfs-rwx"
    assert "helm.sh/resource-policy" not in (pvc["metadata"].get("annotations") or {})


def _backup_pvc(*extra: str) -> dict[str, Any]:
    return _find(_render(*extra), "PersistentVolumeClaim", f"{_FULLNAME}-backups")


def test_backup_pvc_inherits_the_workspace_class_and_access_mode() -> None:
    """An upgrade adds this PVC to an install that already chose storage for
    its workspace. Landing it on the cluster default class in ReadWriteMany
    instead leaves the new backend and worker pods Pending on such installs."""
    pvc = _backup_pvc(
        "--set",
        "workspace.persistence.storageClassName=efs",
        "--set",
        "workspace.persistence.accessMode=ReadWriteOnce",
    )
    assert pvc["spec"]["storageClassName"] == "efs"
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_backup_pvc_on_workspace_defaults_sets_no_class_and_stays_rwx() -> None:
    pvc = _backup_pvc()
    assert "storageClassName" not in pvc["spec"]
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]


def test_explicit_backup_class_and_access_mode_override_the_workspace() -> None:
    pvc = _backup_pvc(
        "--set",
        "workspace.persistence.storageClassName=efs",
        "--set",
        "workspace.persistence.accessMode=ReadWriteOnce",
        "--set",
        "backups.persistence.storageClassName=nfs-backups",
        "--set",
        "backups.persistence.accessMode=ReadWriteMany",
    )
    assert pvc["spec"]["storageClassName"] == "nfs-backups"
    assert pvc["spec"]["accessModes"] == ["ReadWriteMany"]


def _rwx_warning(notes: str) -> str:
    matches = [w for w in _warnings(notes) if "ReadWriteMany with no storageClassName" in w]
    return matches[0] if matches else ""


def test_notes_rwx_warning_names_the_backup_pvc_on_the_default_class() -> None:
    assert "backups.persistence" in _rwx_warning(_render_notes())


def test_notes_rwx_warning_follows_the_inherited_backup_class() -> None:
    """With the workspace on a named class the backup PVC inherits it, so the
    warning must not tell the operator the backup PVC is on the default."""
    notes = _render_notes(
        "--set",
        "workspace.persistence.storageClassName=efs",
        "--set",
        "trivyCache.persistence.storageClassName=efs",
    )
    assert _rwx_warning(notes) == ""


# ---------------------------------------------------------------------------
# NetworkPolicy (#572)
# ---------------------------------------------------------------------------


def _default_queue_warning(notes: str) -> str:
    matches = [w for w in _warnings(notes) if "networkPolicy.enabled restricts worker-scan" in w]
    assert len(matches) <= 1
    return matches[0] if matches else ""


def test_notes_warn_when_the_policy_covers_default_queue_tasks() -> None:
    """worker-scan subscribed to the default queue runs notifications and
    ticket posts under the policy, which fail only when it takes them."""
    warning = _default_queue_warning(_render_notes("--set", "networkPolicy.enabled=true"))
    assert "trustedoss.default" in warning
    assert "worker.subscriptions.scan" in warning
    assert "extraEgress" in warning


def test_notes_do_not_warn_without_the_policy() -> None:
    assert _default_queue_warning(_render_notes()) == ""


def test_notes_do_not_warn_once_worker_scan_takes_only_the_scan_queue() -> None:
    notes = _render_notes(
        "--set",
        "networkPolicy.enabled=true",
        "--set",
        "worker.subscriptions.scan={trustedoss.scan}",
    )
    assert _default_queue_warning(notes) == ""


def _policy(*extra: str) -> dict[str, Any]:
    docs = _render("--set", "networkPolicy.enabled=true", *extra)
    policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
    assert len(policies) == 1, f"expected one NetworkPolicy, got {_inventory(docs)}"
    return policies[0]


def _rules_with_pod_component(policy: dict[str, Any], component: str) -> list[dict[str, Any]]:
    return [
        rule
        for rule in policy["spec"]["egress"]
        for peer in rule.get("to", [])
        if (peer.get("podSelector") or {}).get("matchLabels", {}).get(
            "app.kubernetes.io/component"
        )
        == component
        and "namespaceSelector" not in peer
    ]


def _ip_blocks(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        peer["ipBlock"]
        for rule in policy["spec"]["egress"]
        for peer in rule.get("to", [])
        if "ipBlock" in peer
    ]


def test_networkpolicy_selects_worker_scan_only_and_denies_ingress() -> None:
    policy = _policy()
    labels = policy["spec"]["podSelector"]["matchLabels"]
    assert labels["app.kubernetes.io/component"] == "worker-scan"
    assert labels["app.kubernetes.io/instance"] == _RELEASE
    assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert policy["spec"]["ingress"] == []


def test_networkpolicy_selector_matches_the_worker_scan_pod_labels() -> None:
    """A selector that matches no pod is a policy that does nothing."""
    docs = _render("--set", "networkPolicy.enabled=true")
    policy = next(d for d in docs if d["kind"] == "NetworkPolicy")
    scan = _find(docs, "Deployment", f"{_FULLNAME}-worker-scan")
    pod_labels = scan["spec"]["template"]["metadata"]["labels"]
    selector = policy["spec"]["podSelector"]["matchLabels"]
    assert selector.items() <= pod_labels.items()
    for other in ("backend", "worker-default", "beat", "frontend"):
        other_labels = _find(docs, "Deployment", f"{_FULLNAME}-{other}")["spec"]["template"][
            "metadata"
        ]["labels"]
        assert not selector.items() <= other_labels.items(), other


def test_networkpolicy_allows_dns_and_the_bundled_datastores_on_their_ports() -> None:
    policy = _policy()
    dns_rules = [
        rule
        for rule in policy["spec"]["egress"]
        for peer in rule.get("to", [])
        if "namespaceSelector" in peer
    ]
    assert len(dns_rules) == 1
    assert {(p["port"], p["protocol"]) for p in dns_rules[0]["ports"]} == {
        (53, "UDP"),
        (53, "TCP"),
    }
    values = _values()
    docs = _render("--set", "networkPolicy.enabled=true")
    workloads = {
        "postgres": _find(docs, "StatefulSet", f"{_FULLNAME}-postgres"),
        "redis": _find(docs, "Deployment", f"{_FULLNAME}-redis"),
    }
    for component, port in (
        ("postgres", values["postgres"]["service"]["port"]),
        ("redis", values["redis"]["service"]["port"]),
    ):
        rules = _rules_with_pod_component(policy, component)
        assert len(rules) == 1, component
        assert rules[0]["ports"] == [{"port": port, "protocol": "TCP"}], component
        # A selector that matches no pod allows nothing, and the worker
        # loses its database. Check it against the real pod labels.
        (peer,) = rules[0]["to"]
        selector = peer["podSelector"]["matchLabels"]
        pod_labels = workloads[component]["spec"]["template"]["metadata"]["labels"]
        assert selector.items() <= pod_labels.items(), component


def test_networkpolicy_internet_rule_excludes_private_and_metadata_ranges() -> None:
    blocks = {b["cidr"]: set(b.get("except", [])) for b in _ip_blocks(_policy())}
    assert {"0.0.0.0/0", "::/0"} <= blocks.keys()
    for private in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"):
        assert private in blocks["0.0.0.0/0"], private
    assert "fc00::/7" in blocks["::/0"]


def test_networkpolicy_internet_rule_can_be_turned_off() -> None:
    policy = _policy("--set", "networkPolicy.workerScan.internet.enabled=false")
    assert _ip_blocks(policy) == []


def test_networkpolicy_extra_egress_is_appended() -> None:
    policy = _policy(
        "--set",
        "networkPolicy.workerScan.extraEgress[0].to[0].ipBlock.cidr=10.30.0.0/24",
        "--set",
        "networkPolicy.workerScan.extraEgress[0].ports[0].port=8081",
    )
    assert {"cidr": "10.30.0.0/24"} in _ip_blocks(policy)


def test_networkpolicy_with_external_datastores_requires_explicit_rules() -> None:
    stderr = _render_fails("--set", "networkPolicy.enabled=true", *_EXTERNAL_DATASTORES)
    assert "externalDatastoreEgress is required" in stderr


def test_networkpolicy_with_external_datastores_uses_the_given_rules_only() -> None:
    policy = _policy(
        *_EXTERNAL_DATASTORES,
        "--set",
        "networkPolicy.workerScan.externalDatastoreEgress[0].to[0].ipBlock.cidr=10.20.0.5/32",
        "--set",
        "networkPolicy.workerScan.externalDatastoreEgress[0].ports[0].port=5432",
    )
    assert _rules_with_pod_component(policy, "postgres") == []
    assert _rules_with_pod_component(policy, "redis") == []
    assert {"cidr": "10.20.0.5/32"} in _ip_blocks(policy)


# ---------------------------------------------------------------------------
# Metrics and ServiceMonitor (#572)
# ---------------------------------------------------------------------------


def test_metrics_enabled_sets_the_backend_switch() -> None:
    configmap = _find(
        _render("--set", "metrics.enabled=true"), "ConfigMap", f"{_FULLNAME}-env"
    )
    assert configmap["data"]["METRICS_ENABLED"] == "true"


def test_servicemonitor_requires_metrics_enabled() -> None:
    stderr = _render_fails("--set", "metrics.serviceMonitor.enabled=true")
    assert "requires metrics.enabled" in stderr


def test_servicemonitor_targets_the_backend_service_port_and_path() -> None:
    docs = _render(
        "--set",
        "metrics.enabled=true",
        "--set",
        "metrics.serviceMonitor.enabled=true",
        "--set",
        "metrics.serviceMonitor.labels.release=prometheus",
    )
    monitor = _find(docs, "ServiceMonitor", f"{_FULLNAME}-backend")
    service = _find(docs, "Service", f"{_FULLNAME}-backend")
    assert monitor["metadata"]["labels"]["release"] == "prometheus"
    assert monitor["spec"]["selector"]["matchLabels"].items() <= service["metadata"][
        "labels"
    ].items()
    port_names = {p["name"] for p in service["spec"]["ports"]}
    (endpoint,) = monitor["spec"]["endpoints"]
    assert endpoint["port"] in port_names
    assert endpoint["path"] == "/metrics"
    assert "authorization" not in endpoint


def test_metrics_token_reaches_the_backend_and_the_servicemonitor_from_one_secret() -> None:
    docs = _render(
        "--set",
        "metrics.enabled=true",
        "--set",
        "metrics.serviceMonitor.enabled=true",
        "--set",
        "metrics.token.existingSecret=trusca-metrics",
        "--set",
        "metrics.token.key=token",
    )
    backend = _find(docs, "Deployment", f"{_FULLNAME}-backend")
    ref = _env(backend)["METRICS_TOKEN"]["valueFrom"]["secretKeyRef"]
    assert ref == {"name": "trusca-metrics", "key": "token"}
    monitor = _find(docs, "ServiceMonitor", f"{_FULLNAME}-backend")
    (endpoint,) = monitor["spec"]["endpoints"]
    assert endpoint["authorization"]["type"] == "Bearer"
    assert endpoint["authorization"]["credentials"] == ref


# ---------------------------------------------------------------------------
# Image digests (#572)
# ---------------------------------------------------------------------------


def _images(docs: list[dict[str, Any]]) -> dict[str, str]:
    """Every container and init container image, keyed by object/container."""
    images: dict[str, str] = {}
    for doc in docs:
        spec = doc.get("spec", {})
        pod = spec.get("template", {}).get("spec") if "template" in spec else None
        if not pod:
            continue
        for container in pod.get("initContainers", []) + pod.get("containers", []):
            images[f"{doc['metadata']['name']}/{container['name']}"] = container["image"]
    return images


def _digest_args() -> list[str]:
    return [
        "--set",
        f"image.backendDigest={_DIGEST['backend']}",
        "--set",
        f"image.workerDigest={_DIGEST['worker']}",
        "--set",
        f"image.frontendDigest={_DIGEST['frontend']}",
        "--set",
        f"postgres.image.digest={_DIGEST['postgres']}",
        "--set",
        f"redis.image.digest={_DIGEST['redis']}",
    ]


def _expected_repository(values: dict[str, Any]) -> dict[str, str]:
    return {
        "backend": values["image"]["backendRepository"],
        "worker": values["image"]["workerRepository"],
        "frontend": values["image"]["frontendRepository"],
        "postgres": values["postgres"]["image"]["repository"],
        "redis": values["redis"]["image"]["repository"],
    }


def _which(image: str, repositories: dict[str, str]) -> str:
    repo = image.split("@", 1)[0] if "@" in image else image.rsplit(":", 1)[0]
    matches = [k for k, r in repositories.items() if r == repo]
    assert len(matches) == 1, f"{image} matches {matches}"
    return matches[0]


def test_without_digests_every_image_is_repository_colon_tag() -> None:
    values = _values()
    repositories = _expected_repository(values)
    tags = {
        "backend": values["image"]["tag"],
        "worker": values["image"]["tag"],
        "frontend": values["image"]["tag"],
        "postgres": values["postgres"]["image"]["tag"],
        "redis": values["redis"]["image"]["tag"],
    }
    images = _images(_render())
    assert images, "no images found in the render"
    for where, image in images.items():
        assert "@" not in image, where
        kind = _which(image, repositories)
        assert image == f"{repositories[kind]}:{tags[kind]}", where


def test_digests_replace_the_tag_on_every_image_they_apply_to() -> None:
    repositories = _expected_repository(_values())
    images = _images(_render(*_digest_args()))
    seen = set()
    for where, image in images.items():
        kind = _which(image, repositories)
        assert image == f"{repositories[kind]}@{_DIGEST[kind]}", where
        seen.add(kind)
    assert seen == set(_DIGEST), f"no rendered image used: {set(_DIGEST) - seen}"


def test_one_digest_leaves_the_other_images_on_their_tags() -> None:
    images = _images(_render("--set", f"image.frontendDigest={_DIGEST['frontend']}"))
    with_digest = {where for where, image in images.items() if "@" in image}
    assert with_digest == {f"{_FULLNAME}-frontend/frontend"}


@pytest.mark.parametrize(
    "bad",
    ["sha256:abc", "abc" + "0" * 61, "sha512:" + "0" * 64, "sha256:" + "A" * 64],
)
def test_malformed_digest_fails_the_render(bad: str) -> None:
    stderr = _render_fails("--set", f"image.workerDigest={bad}")
    assert "must be sha256: followed by 64 lowercase hex" in stderr
