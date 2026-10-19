"""Pre-white-label on-disk identifiers that existing installs still use.

LEGACY: every literal in this module builds the old product name on purpose.
They are split into fragments so a repository-wide product-name replacement
(``scripts/apply_brand.py``) cannot rewrite them — they must keep matching the
data directory and file names customers already have on disk.

Do not use these names for new data. Remove this module only together with a
documented end of support for pre-white-label homes.
"""

from __future__ import annotations

LEGACY_BRAND = "oct" + "op"
LEGACY_HOME_DIRNAME = "." + LEGACY_BRAND
LEGACY_DB_FILENAME = LEGACY_BRAND + ".db"
LEGACY_LOG_FILENAME = LEGACY_BRAND + ".log"
LEGACY_LOGIN_FILENAME = LEGACY_BRAND + "-login.txt"
LEGACY_MANUAL_BACKUP_PREFIX = LEGACY_BRAND + "-backup-"
LEGACY_AUTO_BACKUP_PREFIX = LEGACY_BRAND + "-auto-backup-"
LEGACY_DB_ARCHIVE_PATH = "db/" + LEGACY_DB_FILENAME
