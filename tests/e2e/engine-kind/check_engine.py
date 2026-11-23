# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Run the BomLens engine through the Kubernetes backend, inside the engine worker pod.

Not a unit test: ``engine-kind.yml`` copies this into the worker-engine pod of a
kind cluster that has the chart installed and runs it there, so the service
account token, the Role, the claims mounted by sub-path, the Jobs' network
labels and the registries are all the real ones. It calls the adapter the scan
tasks call (``integrations.bomlens.engine``), without the database.

Exit code 0 means a source scan and an image scan both ran to a result.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from core.config import workspace_root
from integrations.bomlens import engine

ALPINE = "alpine:3.19.0@sha256:51b67269f354137895d43f3b3d810bfacd3945438e94dc5ac55fdac340352f48"


def _read(run: engine.EngineRun) -> dict:
    return json.loads((run.output_dir / "bomlens-result.json").read_text())


def source_scan() -> None:
    scan_id = uuid.uuid4()
    scan_dir = Path(workspace_root()) / scan_id.hex
    source = scan_dir / "source" / "repo"
    source.mkdir(parents=True)
    (source / "package.json").write_text(
        json.dumps({"name": "ci-app", "version": "1.0.0", "dependencies": {"lodash": "4.17.20"}})
    )
    stages: list[str] = []
    run = engine.run_source(
        scan_uuid=scan_id,
        scan_dir=scan_dir,
        source=source,
        project="ci-app",
        version="1.0.0",
        fetch_license=False,
        deep_license=False,
        include_non_shipped=False,
        on_stage=stages.append,
    )
    result = _read(run)
    print("source scan:", run.run_status, "stages", stages, "metadata", run.metadata)
    assert stages == ["prep", "cdxgen", "scancode"], stages
    assert run.metadata["plan_status"] == "ok", run.metadata
    assert run.metadata["stage1_exit_code"] == 0, run.metadata
    assert run.metadata["stage2_exit_code"] == 0, run.metadata
    assert run.run_status in ("succeeded", "succeeded_degraded"), run.run_status
    assert run.stage1_window is not None
    sbom = next(o for o in result["outputs"] if o["role"] == "sbom.cyclonedx")
    document = json.loads((run.output_dir / sbom["path"]).read_text())
    assert document.get("components"), "the SBOM has no component"


def image_scan() -> None:
    scan_id = uuid.uuid4()
    scan_dir = Path(workspace_root()) / scan_id.hex
    scan_dir.mkdir(parents=True)
    run = engine.run_image(
        scan_uuid=scan_id,
        scan_dir=scan_dir,
        target=engine.image_target(ALPINE),
        project="ci-image",
        version="1.0.0",
    )
    print("image scan:", run.run_status, run.metadata)
    assert run.metadata["exit_code"] == 0, run.metadata
    assert run.run_status in ("succeeded", "succeeded_degraded"), run.run_status


def reaper_sees_no_leftover() -> None:
    from core.config import trusca_instance_id

    backend = engine.engine_backend()
    left = backend.list_engine_containers({"trusca.instance": trusca_instance_id()})
    print("engine Jobs left after the runs:", [c.name for c in left])
    assert not [c for c in left if c.running], "a Job is still running after its scan ended"


def main() -> int:
    source_scan()
    image_scan()
    reaper_sees_no_leftover()
    print("engine on Kubernetes: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
