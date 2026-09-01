# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Unit tests for scripts/scan-bench/bulk_register.py's git_credential PATCH.

self-resource-validation-plan-2026-08-30.md S1.5: ProjectCreate has no
git_credential field, so internal/private targets need a follow-up PATCH
after project creation or the worker can never authenticate the clone. This
covers just that follow-up call and its CLI/env wiring against a fake
PortalClient, no real portal or network involved. Run with:
    python3 -m pytest scripts/scan-bench/tests
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import bulk_register


class FakeClient:
    def __init__(self, response: tuple[int, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, str, object]] = []

    def request(self, method, path, *, json_body=None, **_kwargs):
        self.calls.append((method, path, json_body))
        return self.response


def test_set_git_credential_patches_project():
    client = FakeClient((200, {"id": "p1", "has_git_credential": True}))
    bulk_register._set_git_credential(client, project_id="p1", credential="tok")
    assert client.calls == [("PATCH", "/v1/projects/p1", {"git_credential": "tok"})]


def test_set_git_credential_raises_on_non_200():
    client = FakeClient((403, {"detail": "forbidden"}))
    with pytest.raises(RuntimeError, match="git_credential PATCH failed 403"):
        bulk_register._set_git_credential(client, project_id="p1", credential="tok")


def test_resolve_git_credential_prefers_cli_flag(monkeypatch):
    monkeypatch.setenv("COHORT_GIT_CREDENTIAL", "from-env")
    args = argparse.Namespace(git_credential="from-cli")
    assert bulk_register._resolve_git_credential(args) == "from-cli"


def test_resolve_git_credential_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("COHORT_GIT_CREDENTIAL", "from-env")
    args = argparse.Namespace(git_credential=None)
    assert bulk_register._resolve_git_credential(args) == "from-env"


def test_resolve_git_credential_none_when_unset(monkeypatch):
    monkeypatch.delenv("COHORT_GIT_CREDENTIAL", raising=False)
    args = argparse.Namespace(git_credential=None)
    assert bulk_register._resolve_git_credential(args) is None
