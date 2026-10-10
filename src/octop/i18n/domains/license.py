"""``license.*`` — customer-facing instance authorization state."""

from __future__ import annotations

from octop.i18n.loader import tr
from octop.infra.utils.locale import Locale

__all__ = ["license_reason_message"]

_LICENSE_REASONS = frozenset(
    {
        "unactivated",
        "expired",
        "revoked",
        "customer_disabled",
        "cache_expired",
        "invalid_credential",
        "clock_anomaly",
        "configuration_required",
        "network_error",
    }
)


def license_reason_message(reason: str | None, locale: str | Locale = "en") -> str:
    key = reason if reason in _LICENSE_REASONS else "unactivated"
    return tr(f"license.reason.{key}", locale)
