#!/usr/bin/env python3
"""
Initialize SQLite database for streaming_db

This script:
- Ensures SQLite DB exists and is accessible
- Enables PRAGMA foreign_keys=ON
- Creates/updates tables:
    users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT UNIQUE, password_hash TEXT, created_at TEXT)
    videos(id INTEGER PRIMARY KEY, title TEXT, description TEXT, file_path TEXT, duration_sec INTEGER, thumbnail_url TEXT, is_featured INTEGER DEFAULT 0, created_at TEXT)
    categories(id INTEGER PRIMARY KEY, name TEXT UNIQUE, created_at TEXT)
    video_categories(video_id INTEGER, category_id INTEGER, PRIMARY KEY(video_id, category_id))
    watch_history(id INTEGER PRIMARY KEY, user_id INTEGER, video_id INTEGER, last_position_sec INTEGER DEFAULT 0, updated_at TEXT)
- Seeds minimal data idempotently (2-3 categories, 4-6 videos, some mappings)
- Writes db_connection.txt and db_visualizer/sqlite.env

Notes:
- We create/alter the users table if it exists but is missing required columns.
- We keep operations compatible with db_visualizer by maintaining the same db path conventions.
"""

import os
import sqlite3
from datetime import datetime

DB_NAME = "myapp.db"

def connect_db(path: str) -> sqlite3.Connection:
    """Create a connection to the SQLite database with foreign keys enabled."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table exists in the database."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None

def get_table_columns(conn: sqlite3.Connection, table: str):
    """Return a dict of column_name -> (cid, name, type, notnull, dflt_value, pk)."""
    cols = {}
    cur = conn.execute(f"PRAGMA table_info({table})")
    for row in cur.fetchall():
        # row = (cid, name, type, notnull, dflt_value, pk)
        cols[row[1]] = row
    return cols

def ensure_users_table(conn: sqlite3.Connection):
    """
    Ensure users table exists with required columns:
    id INTEGER PRIMARY KEY, username TEXT UNIQUE, email TEXT UNIQUE,
    password_hash TEXT, created_at TEXT

    If users exists but lacks columns, migrate safely via create temp + copy.
    """
    required_columns = {
        "id": "INTEGER PRIMARY KEY",
        "username": "TEXT",
        "email": "TEXT",
        "password_hash": "TEXT",
        "created_at": "TEXT",
    }

    if not table_exists(conn, "users"):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                email TEXT UNIQUE,
                password_hash TEXT,
                created_at TEXT
            )
        """)
        return

    # Exists - check columns
    cols = get_table_columns(conn, "users")
    missing = [col for col in required_columns.keys() if col not in cols]

    # If nothing missing but uniqueness may be absent, we can try adding unique indexes safely
    if not missing:
        # Ensure username and email unique constraints via unique indexes if needed
        # Check existing indexes
        cur = conn.execute("PRAGMA index_list(users)")
        existing_indexes = [row[1] for row in cur.fetchall()]  # row[1] = name
        # Try to create unique indexes (will fail gracefully if duplicates exist; catch exceptions)
        try:
            if "idx_users_username_unique" not in existing_indexes:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users(username)")
        except sqlite3.Error:
            pass
        try:
            if "idx_users_email_unique" not in existing_indexes:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(email)")
        except sqlite3.Error:
            pass
        return

    # Need to migrate: create new table with correct schema, copy data, drop old, rename new
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users_new (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            email TEXT UNIQUE,
            password_hash TEXT,
            created_at TEXT
        )
    """)
    existing_col_names = list(cols.keys())
    # Map available old columns to new
    common_cols = [c for c in ["id", "username", "email", "password_hash", "created_at"] if c in existing_col_names]
    if common_cols:
        src_cols = ", ".join(common_cols)
        dst_cols = ", ".join(common_cols)
        conn.execute(f"INSERT INTO users_new ({dst_cols}) SELECT {src_cols} FROM users")
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")

def ensure_videos_table(conn: sqlite3.Connection):
    """Create videos table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY,
            title TEXT,
            description TEXT,
            file_path TEXT,
            duration_sec INTEGER,
            thumbnail_url TEXT,
            is_featured INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

def ensure_categories_table(conn: sqlite3.Connection):
    """Create categories table if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            created_at TEXT
        )
    """)

def ensure_video_categories_table(conn: sqlite3.Connection):
    """Create video_categories mapping table with FKs if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS video_categories (
            video_id INTEGER,
            category_id INTEGER,
            PRIMARY KEY (video_id, category_id),
            FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
        )
    """)

def ensure_watch_history_table(conn: sqlite3.Connection):
    """Create watch_history table with FKs if not exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watch_history (
            id INTEGER PRIMARY KEY,
            user_id INTEGER,
            video_id INTEGER,
            last_position_sec INTEGER DEFAULT 0,
            updated_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
    """)

def seed_categories(conn: sqlite3.Connection) -> dict:
    """Seed 2-3 categories idempotently; return dict name->id."""
    now = datetime.utcnow().isoformat()
    categories = ["Action", "Drama", "Comedy"]
    name_to_id = {}
    for name in categories:
        cur = conn.execute("SELECT id FROM categories WHERE name = ?", (name,))
        row = cur.fetchone()
        if row:
            name_to_id[name] = row[0]
            continue
        conn.execute(
            "INSERT INTO categories (name, created_at) VALUES (?, ?)",
            (name, now),
        )
        new_id = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()[0]
        name_to_id[name] = new_id
    return name_to_id

def seed_videos(conn: sqlite3.Connection) -> dict:
    """
    Seed 4-6 videos with placeholder file_path under a media directory.
    Return dict title->id.
    """
    now = datetime.utcnow().isoformat()
    # Use a media directory relative to database container
    media_dir = os.path.abspath(os.path.join(os.getcwd(), "media"))
    os.makedirs(media_dir, exist_ok=True)

    # Placeholder files (we don't create actual media; file paths are references)
    video_defs = [
        {
            "title": "Ocean Wonders",
            "description": "A calming documentary of the ocean.",
            "file_path": os.path.join(media_dir, "ocean_wonders.mp4"),
            "duration_sec": 1800,
            "thumbnail_url": "/thumbnails/ocean.jpg",
            "is_featured": 1
        },
        {
            "title": "City Lights",
            "description": "A timelapse of city nightlife.",
            "file_path": os.path.join(media_dir, "city_lights.mp4"),
            "duration_sec": 900,
            "thumbnail_url": "/thumbnails/city.jpg",
            "is_featured": 0
        },
        {
            "title": "Mountain Trails",
            "description": "Hiking through mountain trails.",
            "file_path": os.path.join(media_dir, "mountain_trails.mp4"),
            "duration_sec": 2400,
            "thumbnail_url": "/thumbnails/mountain.jpg",
            "is_featured": 0
        },
        {
            "title": "Comedy Skits",
            "description": "A collection of short comedy skits.",
            "file_path": os.path.join(media_dir, "comedy_skits.mp4"),
            "duration_sec": 1200,
            "thumbnail_url": "/thumbnails/comedy.jpg",
            "is_featured": 0
        },
        {
            "title": "Epic Action",
            "description": "Explosive action highlights.",
            "file_path": os.path.join(media_dir, "epic_action.mp4"),
            "duration_sec": 1500,
            "thumbnail_url": "/thumbnails/action.jpg",
            "is_featured": 1
        },
    ]

    title_to_id = {}
    for v in video_defs:
        cur = conn.execute("SELECT id FROM videos WHERE title = ?", (v["title"],))
        row = cur.fetchone()
        if row:
            title_to_id[v["title"]] = row[0]
            continue
        conn.execute("""
            INSERT INTO videos (title, description, file_path, duration_sec, thumbnail_url, is_featured, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            v["title"], v["description"], v["file_path"], v["duration_sec"],
            v["thumbnail_url"], v["is_featured"], now
        ))
        new_id = conn.execute("SELECT id FROM videos WHERE title = ?", (v["title"],)).fetchone()[0]
        title_to_id[v["title"]] = new_id

    return title_to_id

def seed_video_categories(conn: sqlite3.Connection, title_to_id: dict, name_to_id: dict):
    """Seed some mappings between videos and categories idempotently."""
    mappings = [
        ("Epic Action", "Action"),
        ("Comedy Skits", "Comedy"),
        ("Ocean Wonders", "Drama"),      # loosely categorize as 'Drama' for demonstration
        ("Mountain Trails", "Action"),
        ("City Lights", "Drama"),
    ]

    for title, cat in mappings:
        vid = title_to_id.get(title)
        cid = name_to_id.get(cat)
        if not vid or not cid:
            continue
        cur = conn.execute(
            "SELECT 1 FROM video_categories WHERE video_id=? AND category_id=?",
            (vid, cid)
        )
        if cur.fetchone():
            continue
        conn.execute(
            "INSERT INTO video_categories (video_id, category_id) VALUES (?, ?)",
            (vid, cid)
        )

def write_connection_info(db_path: str):
    """Write db_connection.txt and db_visualizer/sqlite.env."""
    current_dir = os.getcwd()
    connection_string = f"sqlite:///{current_dir}/{DB_NAME}"
    try:
        with open("db_connection.txt", "w") as f:
            f.write("# SQLite connection methods:\n")
            f.write(f"# Python: sqlite3.connect('{DB_NAME}')\n")
            f.write(f"# Connection string: {connection_string}\n")
            f.write(f"# File path: {current_dir}/{DB_NAME}\n")
        print("Connection information saved to db_connection.txt")
    except Exception as e:
        print(f"Warning: Could not save connection info: {e}")

    # Ensure db_visualizer directory exists
    os.makedirs("db_visualizer", exist_ok=True)
    try:
        with open("db_visualizer/sqlite.env", "w") as f:
            f.write(f'export SQLITE_DB="{db_path}"\n')
        print("Environment variables saved to db_visualizer/sqlite.env")
    except Exception as e:
        print(f"Warning: Could not save environment variables: {e}")

def main():
    print("Starting SQLite setup...")
    db_exists = os.path.exists(DB_NAME)
    if db_exists:
        print(f"SQLite database already exists at {DB_NAME}")
    else:
        print("Creating new SQLite database...")

    conn = connect_db(DB_NAME)
    try:
        # Create schema
        ensure_users_table(conn)
        ensure_videos_table(conn)
        ensure_categories_table(conn)
        ensure_video_categories_table(conn)
        ensure_watch_history_table(conn)

        # Seed data idempotently
        category_ids = seed_categories(conn)
        video_ids = seed_videos(conn)
        seed_video_categories(conn, video_ids, category_ids)

        conn.commit()

        # Print simple stats
        cur = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        table_count = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM categories")
        cat_count = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM videos")
        vid_count = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM video_categories")
        map_count = cur.fetchone()[0]

        # Write connection info files
        db_path = os.path.abspath(DB_NAME)
        write_connection_info(db_path)

        print("\nSQLite setup complete!")
        print(f"Database: {DB_NAME}")
        print(f"Location: {db_path}")
        print("Statistics:")
        print(f"  Tables: {table_count}")
        print(f"  Categories: {cat_count}")
        print(f"  Videos: {vid_count}")
        print(f"  Video-Category mappings: {map_count}")
        print("\nTo use with Node.js viewer, run: source db_visualizer/sqlite.env")
        print("To open a shell: python db_shell.py")

    finally:
        conn.close()

if __name__ == "__main__":
    main()
