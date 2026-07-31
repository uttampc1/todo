import sqlite3

DB_FILE = "todos.db"

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id            INTEGER  PRIMARY KEY AUTOINCREMENT,
            task          TEXT     NOT NULL UNIQUE,
            is_done       INTEGER DEFAULT 0,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── create a migrations tracking table ────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL,
            applied_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()

    # ── run any pending migrations ────────────────────────────────────────────
    run_migrations(conn)

    conn.close()
    print(f"[DB] Initialized: {DB_FILE}")

# ─────────────────────────────────────────────────────────────────────────────
# Migration system
#
# To add a new column or change the schema:
#   1. Add a new entry to MIGRATIONS with the next version number
#   2. Write the SQL in the "sql" field
#   3. Restart the app — it will auto-apply
#
# Migrations are applied in order and only once (tracked in schema_migrations)
# ─────────────────────────────────────────────────────────────────────────────

MIGRATIONS = [
    {
        "version": 1,
        "name":    "add_tag_column",
        "sql": [
            "ALTER TABLE todos ADD COLUMN tag TEXT",
        ]
    },
    {
        "version": 2,
        "name":    "add_owner_column",
        "sql": [
            "ALTER TABLE todos ADD COLUMN owner TEXT",
        ]
    },
]

def get_applied_versions(conn):
    """Return a set of already-applied migration version numbers."""
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def run_migrations(conn):
    """Apply any migrations that haven't been applied yet."""
    applied = get_applied_versions(conn)

    for migration in sorted(MIGRATIONS, key=lambda m: m["version"]):
        ver  = migration["version"]
        name = migration["name"]

        if ver in applied:
            continue    # already applied

        print(f"[DB] Applying migration {ver}: {name}")

        try:
            for sql in migration["sql"]:
                conn.execute(sql)

            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (ver, name)
            )
            conn.commit()
            print(f"[DB] Migration {ver} applied successfully.")

        except Exception as e:
            conn.rollback()
            print(f"[DB] ERROR applying migration {ver}: {e}")
            raise

