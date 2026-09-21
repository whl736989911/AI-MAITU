"""Public interface for the enterprise feature directory."""

from octop.infra.features.catalog import (
    MANIFEST_FILENAME,
    Feature,
    FeatureAgent,
    FeatureCatalog,
    ScopedRule,
    build_user_prompt,
    default_library_root,
)
from octop.infra.features.capability import (
    CapabilityUnavailable,
    ResolvedCapability,
    resolve_capability,
    stamp_capability,
)
from octop.infra.features.schema import feature_json_schema, validate_manifest
from octop.infra.features.store import (
    USER_PROMPT_FILENAME,
    FeatureAlreadyExists,
    FeatureDefinitionInvalid,
    FeatureNotFound,
    FeatureReadOnly,
    FeatureStore,
    FeatureStoreError,
)

__all__ = [
    "MANIFEST_FILENAME",
    "USER_PROMPT_FILENAME",
    "CapabilityUnavailable",
    "Feature",
    "FeatureAgent",
    "FeatureAlreadyExists",
    "FeatureCatalog",
    "FeatureDefinitionInvalid",
    "FeatureNotFound",
    "FeatureReadOnly",
    "FeatureStore",
    "FeatureStoreError",
    "ResolvedCapability",
    "ScopedRule",
    "build_user_prompt",
    "default_library_root",
    "feature_json_schema",
    "resolve_capability",
    "stamp_capability",
    "validate_manifest",
]
