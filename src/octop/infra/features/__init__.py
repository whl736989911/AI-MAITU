"""Public interface for the enterprise feature directory."""

from octop.infra.features.catalog import (
    MANIFEST_FILENAME,
    Feature,
    FeatureCatalog,
    ScopedRule,
    build_user_prompt,
    default_library_root,
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
    "Feature",
    "FeatureAlreadyExists",
    "FeatureCatalog",
    "FeatureDefinitionInvalid",
    "FeatureNotFound",
    "FeatureReadOnly",
    "FeatureStore",
    "FeatureStoreError",
    "ScopedRule",
    "build_user_prompt",
    "default_library_root",
    "feature_json_schema",
    "validate_manifest",
]
