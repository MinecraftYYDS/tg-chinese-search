PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA temp_store=MEMORY;

CREATE TABLE IF NOT EXISTS channel_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    channel_username TEXT,
    text TEXT NOT NULL,
    tokens TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    edited_timestamp INTEGER,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_messages_chat_time
    ON channel_messages(chat_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_channel_messages_time
    ON channel_messages(timestamp DESC);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    is_sensitive INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    key TEXT,
    masked_value TEXT,
    detail TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_session (
    user_id INTEGER PRIMARY KEY,
    expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_login_attempt (
    user_id INTEGER PRIMARY KEY,
    failed_attempts INTEGER NOT NULL,
    locked_until INTEGER
);

CREATE TABLE IF NOT EXISTS channel_alias (
    chat_id INTEGER PRIMARY KEY,
    username TEXT UNIQUE
);

CREATE VIRTUAL TABLE IF NOT EXISTS channel_messages_fts USING fts5(
    tokens,
    content='channel_messages',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS channel_messages_ai AFTER INSERT ON channel_messages BEGIN
    INSERT INTO channel_messages_fts(rowid, tokens)
    VALUES (new.id, new.tokens);
END;

CREATE TRIGGER IF NOT EXISTS channel_messages_ad AFTER DELETE ON channel_messages BEGIN
    INSERT INTO channel_messages_fts(channel_messages_fts, rowid, tokens)
    VALUES('delete', old.id, old.tokens);
END;

CREATE TRIGGER IF NOT EXISTS channel_messages_au AFTER UPDATE ON channel_messages BEGIN
    INSERT INTO channel_messages_fts(channel_messages_fts, rowid, tokens)
    VALUES('delete', old.id, old.tokens);
    INSERT INTO channel_messages_fts(rowid, tokens)
    VALUES (new.id, new.tokens);
END;

