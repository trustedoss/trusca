"""Put this directory on sys.path so ``import run_golden`` resolves in the
golden pytest wrapper (the harness is a sibling module, not an installed pkg)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Shadow the backend suite's limiter reset.

    ``tests/conftest.py`` clears the in-process limiter, which reaches for the
    Redis host named in the app settings. The golden gate drives a remote stack
    over HTTP and holds no limiter of its own, and on the nightly runner that
    host (``redis``) does not resolve, so the reset failed every case at setup.
    """
