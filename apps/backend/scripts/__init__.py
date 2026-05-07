"""Backend operational scripts.

Marks ``scripts/`` as a regular Python package so test code can use the
canonical ``from scripts.seed_e2e_user import ...`` form (mypy strict
otherwise reports "source file found twice" because the same module is
discoverable both as ``seed_e2e_user`` (cwd-relative) and
``scripts.seed_e2e_user``).

The empty package init also keeps ``python -m scripts.seed_e2e_user``
working as a fallback invocation pattern alongside the existing
``python apps/backend/scripts/seed_e2e_user.py`` direct-script path.
"""
