# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Re-capture the conformance baselines that test_conformance_output_unchanged reads.

Run from ``apps/backend`` only when the output is MEANT to change, and let the
resulting diff be reviewed as part of the change that caused it:

    python -m tests.fixtures.conformance_baseline.recapture

Running it to make a red test go green defeats the purpose of the captures.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.sbom_conformance import evaluate

_HERE = Path(__file__).resolve().parent
_SOURCES = _HERE.parent / "sbom_ingest"


def main() -> None:
    for source in sorted(_SOURCES.glob("*.json")):
        result = evaluate(source.read_bytes()).as_dict()
        target = _HERE / f"{source.stem}.json"
        target.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        g7 = sum(1 for check in result["checks"] if check.get("cluster"))
        print(
            f"{source.name:34s} format={result['source_format']:10s} "
            f"result={result['result']:5s} checks={len(result['checks']):3d} "
            f"clustered={g7}"
        )


if __name__ == "__main__":
    main()
