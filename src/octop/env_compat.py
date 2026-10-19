"""Brand-prefixed environment variable compatibility.

The deployment documents and installer scripts use the brand prefix
(``MAITU_*`` after branding), while the code base deliberately keeps its
historic ``OCTOP_*`` names. Mirror the branded names onto the legacy ones
before any config/path module reads the environment, so both spellings work:

* new docs / Docker / systemd files may set ``MAITU_PORT`` etc.;
* existing deployments with ``OCTOP_PORT`` keep working unchanged.
"""

from __future__ import annotations

import os

_BRAND_PREFIX = "MAITU_"
_LEGACY_PREFIX = "OCTOP_"


def mirror_brand_env(environ: dict[str, str] | None = None) -> int:
    """Copy ``MAITU_*`` values to missing ``OCTOP_*`` keys; return copy count."""
    env = os.environ if environ is None else environ
    copied = 0
    for key, value in list(env.items()):
        if not key.startswith(_BRAND_PREFIX):
            continue
        legacy = _LEGACY_PREFIX + key[len(_BRAND_PREFIX) :]
        if legacy not in env:
            env[legacy] = value
            copied += 1
    return copied
