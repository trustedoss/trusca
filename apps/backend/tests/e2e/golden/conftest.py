"""Put this directory on sys.path so ``import run_golden`` resolves in the
golden pytest wrapper (the harness is a sibling module, not an installed pkg)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
