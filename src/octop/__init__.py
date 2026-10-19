"""Octop — smarter self-hosted AI assistant (multi-user, multi-agent)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from octop.env_compat import mirror_brand_env

# Branded environment spellings (MAITU_*) are what the branded docs/installers
# use; the code base keeps OCTOP_* internally. Mirror them before any module
# reads the environment.
mirror_brand_env()

try:
    __version__ = version("octop")
except PackageNotFoundError:
    __version__ = "0.0.0"
