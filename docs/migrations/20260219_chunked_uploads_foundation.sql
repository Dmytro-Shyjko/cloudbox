-- Chunked uploads foundation schema migration.
-- Implemented by app.models._apply_chunked_uploads_foundation_migration.

CREATE TABLE IF NOT EXISTS uploads (
	id TEXT PRIMARY KEY,
	user_id INTEGER NOT NULL,
	target_path TEXT,
	filename_original TEXT NOT NULL,
	filename_final TEXT,
	total_size INTEGER NOT NULL,
	chunk_size INTEGER NOT NULL,
	total_chunks INTEGER NOT NULL,
	status TEXT NOT NULL,
	sha256_client TEXT,
	sha256_final TEXT,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	expires_at DATETIME NOT NULL,
	last_activity_at DATETIME NOT NULL,
	FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_uploads_user_status ON uploads(user_id, status);
CREATE INDEX IF NOT EXISTS idx_uploads_expires_at ON uploads(expires_at);
CREATE INDEX IF NOT EXISTS idx_uploads_last_activity_at ON uploads(last_activity_at);

CREATE TABLE IF NOT EXISTS upload_chunks (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	upload_id TEXT NOT NULL,
	chunk_index INTEGER NOT NULL,
	size INTEGER NOT NULL,
	sha256 TEXT,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY(upload_id) REFERENCES uploads(id) ON DELETE CASCADE,
	UNIQUE(upload_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_upload_chunks_upload_id ON upload_chunks(upload_id);

CREATE TABLE IF NOT EXISTS user_files (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	user_id INTEGER NOT NULL,
	path TEXT NOT NULL,
	filename TEXT NOT NULL,
	size INTEGER,
	sha256 TEXT,
	created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
	FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_user_files_user_sha256 ON user_files(user_id, sha256);
