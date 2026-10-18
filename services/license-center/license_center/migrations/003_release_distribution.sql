CREATE TABLE IF NOT EXISTS release_signing_keys(
    kid TEXT PRIMARY KEY,
    private_path TEXT NOT NULL,
    public_pem TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('staged','active','retired')),
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS releases(
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    release_notes TEXT NOT NULL,
    package_type TEXT NOT NULL,
    platform TEXT NOT NULL,
    architecture TEXT NOT NULL,
    published INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE(version, platform, architecture)
);
CREATE INDEX IF NOT EXISTS idx_releases_published ON releases(published, platform, architecture);
CREATE TABLE IF NOT EXISTS center_imports(
    source TEXT PRIMARY KEY,
    imported_at INTEGER NOT NULL
);
