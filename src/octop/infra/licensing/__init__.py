"""Mandatory per-instance licensing client."""

from .service import LicenseService, current_license, set_current_license

__all__ = ["LicenseService", "current_license", "set_current_license"]
