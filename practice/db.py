"""Database layer — SQLite (default) or MySQL with connection pool, init, migrations, config."""
import os
import queue as _pool_queue
import sqlite3
import threading as _threading
from flask import g

from practice import DEFAULT_CONFIG, practice_bp


# ================================================================
# Backend detection — set DB_TYPE=mysql to use MySQL
# ================================================================
DB_TYPE = os.environ.get("DB_TYPE", "sqlite")  # "sqlite" | "mysql"

# MySQL connection params (from env or defaults)
MYSQL_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASSWORD", "123456"),
    "database": os.environ.get("MYSQL_DATABASE", "practice"),
    "charset": "utf8mb4",
}

# MySQL connection pool size (HTTP threads)
MYSQL_POOL_SIZE = int(os.environ.get("MYSQL_POOL_SIZE", "20"))
MYSQL_POOL_TIMEOUT = float(os.environ.get("MYSQL_POOL_TIMEOUT", "5.0"))  # seconds

# SQLite path (unchanged)
DATABASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "practice.db")


# ================================================================
# MySQL connection pool
# ================================================================
_mysql_pool = None
_mysql_pool_lock = _threading.Lock()


def _init_mysql_pool():
    """Lazy-init the MySQL connection pool (thread-safe, called once)."""
    global _mysql_pool
    if _mysql_pool is None:
        with _mysql_pool_lock:
            if _mysql_pool is None:
                _mysql_pool = _pool_queue.Queue(maxsize=MYSQL_POOL_SIZE)
                for _ in range(MYSQL_POOL_SIZE):
                    _mysql_pool.put(_create_mysql_conn())


def _create_mysql_conn():
    """Create a fresh MySQL connection & wrap it (for pool or worker)."""
    import pymysql
    conn = pymysql.connect(
        host=MYSQL_CONFIG["host"],
        port=MYSQL_CONFIG["port"],
        user=MYSQL_CONFIG["user"],
        password=MYSQL_CONFIG["password"],
        database=MYSQL_CONFIG["database"],
        charset=MYSQL_CONFIG["charset"],
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    return _MySQLConnection(conn)


def _checkout_mysql():
    """Check out a connection from the pool, ensure clean state, ping for freshness."""
    _init_mysql_pool()
    try:
        db = _mysql_pool.get(timeout=MYSQL_POOL_TIMEOUT)
    except _pool_queue.Empty:
        raise RuntimeError(
            f"MySQL connection pool exhausted ({MYSQL_POOL_SIZE} connections in use). "
            f"Increase MYSQL_POOL_SIZE or reduce concurrency."
        )
    try:
        db._conn.rollback()          # clear any stale transaction snapshot
    except Exception:
        pass
    db._conn.ping(reconnect=True)     # refresh stale connection
    return db


def _return_mysql(db):
    """Return a connection to the pool (rollback any lingering transaction first)."""
    try:
        db._conn.rollback()   # clear implicit REPEATABLE READ snapshot
    except Exception:
        pass
    _mysql_pool.put(db)


# ================================================================
# MySQL wrapper — translates sqlite3-style calls to pymysql
# ================================================================
class _MySQLConnection:
    """Thin wrapper that makes pymysql look like sqlite3.Connection + Row factory."""

    def __init__(self, conn):
        self._conn = conn
        self._conn.ping(reconnect=True)  # validate on creation

    def execute(self, sql, params=()):
        # Convert ? placeholders to %s
        sql = sql.replace("?", "%s")
        # SQLite → MySQL syntax conversions
        sql = sql.replace("INSERT OR IGNORE INTO", "INSERT IGNORE INTO")
        sql = sql.replace("INSERT OR REPLACE INTO", "REPLACE INTO")
        # ORDER BY RANDOM() is SQLite; MySQL uses RAND()
        import re as _re
        sql = _re.sub(r'\bRANDOM\s*\(\s*\)', 'RAND()', sql)
        # DATE('now') is SQLite; MySQL uses CURDATE()
        sql = _re.sub(r"\bDATE\s*\(\s*'now'\s*\)", 'CURDATE()', sql)
        # DATE('now', '-N days') → DATE_SUB(CURDATE(), INTERVAL N DAY)
        sql = _re.sub(
            r"\bDATE\s*\(\s*'now'\s*,\s*'([+-]?\d+)\s*days?'\s*\)",
            lambda m: f"DATE_SUB(CURDATE(), INTERVAL {abs(int(m.group(1)))} DAY)",
            sql,
        )
        # Backtick-quote bare `key` column (MySQL reserved word).
        # Only matches lowercase "key", NOT "PRIMARY KEY"/"FOREIGN KEY",
        # and skips already-quoted `key`.
        import re as _re
        sql = _re.sub(r'(?<!`)\bkey\b(?!`)', '`key`', sql)
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return _MySQLCursor(cursor)

    def executescript(self, script):
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class _MySQLCursor:
    """Wraps pymysql cursor to return sqlite3.Row-like dict rows."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = cursor.lastrowid

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return _RowDict(row)

    def fetchall(self):
        return [_RowDict(r) for r in self._cursor.fetchall()]


class _RowDict:
    """Dict-like row that supports row['key'], row.key, row[0], and 'key' in row.

    pymysql DictCursor returns plain dicts; this wrapper adds sqlite3.Row
    compatibility: integer index access and attribute access.
    """
    def __init__(self, data):
        self._data = data
    def __getitem__(self, key):
        if isinstance(key, int):
            # sqlite3.Row-style integer indexing
            return list(self._data.values())[key]
        return self._data[key]
    def __contains__(self, key):
        return key in self._data
    def __getattr__(self, key):
        if key.startswith("_"):
            return object.__getattribute__(self, key)
        try:
            return self._data[key]
        except KeyError:
            raise AttributeError(key)
    def keys(self):
        return self._data.keys()
    def get(self, key, default=None):
        return self._data.get(key, default)
    def __iter__(self):
        return iter(self._data)
    def __repr__(self):
        return repr(self._data)


# ================================================================
# Connection management
# ================================================================

def _connect_sqlite():
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL mode: concurrent reads never block writes, writes never block reads
    conn.execute("PRAGMA journal_mode=WAL;")
    # NORMAL sync: safe across power-loss, faster than FULL (journal fsync skipped)
    conn.execute("PRAGMA synchronous=NORMAL;")
    # 32 MB page cache (up from 8 MB) — fewer disk reads under concurrent load
    conn.execute("PRAGMA cache_size=-32000;")
    # In-memory temp storage for sorting/grouping — no temp file I/O
    conn.execute("PRAGMA temp_store=MEMORY;")
    # 256 MB memory-mapped I/O — bypasses read() syscalls entirely
    conn.execute("PRAGMA mmap_size=268435456;")
    # Defer WAL checkpoint to 100 pages (~400 KB) — aggressive truncation
    # prevents the WAL ballooning to 40 MB+ under sustained write bursts
    conn.execute("PRAGMA wal_autocheckpoint=100;")
    return conn


def _connect_mysql():
    """Create a fresh MySQL connection (used by schema init only)."""
    return _create_mysql_conn()


def get_db():
    """Per-request database connection — pooled for MySQL, fresh for SQLite."""
    if "practice_db" not in g:
        if DB_TYPE == "mysql":
            g.practice_db = _checkout_mysql()   # pool checkout + ping
        else:
            g.practice_db = _connect_sqlite()
    return g.practice_db


@practice_bp.teardown_app_request
def close_db(_exception):
    db = g.pop("practice_db", None)
    if db is not None:
        if DB_TYPE == "mysql":
            _return_mysql(db)   # return to pool (no close)
        else:
            db.close()


# ================================================================
# Schema init & migrations — runs once at import time
# ================================================================

_SQLITE_DDL = '''
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
        answer TEXT NOT NULL, subject TEXT DEFAULT '', type TEXT DEFAULT '',
        difficulty REAL DEFAULT 0.5, avg_cost REAL DEFAULT 5.0,
        source TEXT DEFAULT 'manual', content_type TEXT DEFAULT 'text',
        image_path TEXT DEFAULT '', answer_image_path TEXT DEFAULT '',
        irt_a REAL DEFAULT 1.0, irt_b REAL DEFAULT 0.0, irt_c REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS user_question_state (
        question_id INTEGER PRIMARY KEY, lambda_ REAL DEFAULT 0.3,
        last_review TEXT NOT NULL DEFAULT '', accuracy REAL DEFAULT 0.0,
        times_correct INTEGER DEFAULT 0, times_wrong INTEGER DEFAULT 0,
        consecutive_correct INTEGER DEFAULT 0,
        FOREIGN KEY (question_id) REFERENCES questions(id)
    );
    CREATE TABLE IF NOT EXISTS answer_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER NOT NULL,
        time_spent REAL NOT NULL, is_correct INTEGER NOT NULL,
        strokes TEXT DEFAULT '[]', session_id INTEGER,
        user_theta_snapshot REAL, irt_theta_snapshot REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS knowledge_nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        subject TEXT NOT NULL, ideal_retention REAL DEFAULT 0.8,
        current_mastery REAL DEFAULT 0.0, rolling_accuracy REAL DEFAULT 0.5,
        irt_theta REAL DEFAULT 0.0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS knowledge_dependency (
        node_id INTEGER NOT NULL, prerequisite_node_id INTEGER NOT NULL,
        PRIMARY KEY (node_id, prerequisite_node_id),
        FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
        FOREIGN KEY(prerequisite_node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS question_node_mapping (
        question_id INTEGER NOT NULL, node_id INTEGER NOT NULL,
        PRIMARY KEY (question_id, node_id),
        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
        FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS user_study_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_start TEXT NOT NULL,
        last_action TEXT NOT NULL, total_questions INTEGER DEFAULT 0,
        accumulated_minutes REAL DEFAULT 0.0, current_fatigue REAL DEFAULT 0.0
    );
    CREATE TABLE IF NOT EXISTS cat_exam_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        current_theta REAL DEFAULT 0.0, task_count INTEGER DEFAULT 0,
        max_tasks INTEGER DEFAULT 20, is_completed INTEGER DEFAULT 0,
        question_history TEXT DEFAULT '[]', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS cat_exam_details (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
        question_id INTEGER NOT NULL, user_answer TEXT, is_correct INTEGER,
        theta_before REAL, theta_after REAL, response_time_secs INTEGER,
        FOREIGN KEY(session_id) REFERENCES cat_exam_sessions(id)
    );

    -- Hot-path indexes (avoid full-table scans under concurrent load)
    CREATE INDEX IF NOT EXISTS idx_answer_records_qid ON answer_records(question_id);
    CREATE INDEX IF NOT EXISTS idx_answer_records_sid ON answer_records(session_id);
    CREATE INDEX IF NOT EXISTS idx_qnm_node_id ON question_node_mapping(node_id);
'''


def _init_sqlite_schema():
    import time
    for attempt in range(5):
        try:
            db = sqlite3.connect(DATABASE, timeout=3.0)
            db.executescript(_SQLITE_DDL)

            # Only seed config if the table is empty (avoids write lock on every startup)
            existing = db.execute('SELECT COUNT(*) FROM config').fetchone()[0]
            if existing == 0:
                for k, v in DEFAULT_CONFIG.items():
                    db.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))
                db.commit()
            db.close()
            return
        except Exception:
            try:
                db.close()
            except Exception:
                pass
            if attempt < 4:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("SQLite schema init failed after 5 retries — database is locked")


def _init_mysql_schema():
    import pymysql
    conn = pymysql.connect(
        host=MYSQL_CONFIG["host"], port=MYSQL_CONFIG["port"],
        user=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"],
        charset=MYSQL_CONFIG["charset"],
    )
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_CONFIG['database']}` CHARACTER SET utf8mb4")
    conn.commit()
    conn.close()

    db = _connect_mysql()
    tables = [
        """CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTO_INCREMENT, content TEXT NOT NULL,
            answer TEXT NOT NULL, subject VARCHAR(64) DEFAULT '', type VARCHAR(64) DEFAULT '',
            difficulty DOUBLE DEFAULT 0.5, avg_cost DOUBLE DEFAULT 5.0,
            source VARCHAR(32) DEFAULT 'manual', content_type VARCHAR(16) DEFAULT 'text',
            image_path VARCHAR(255) DEFAULT '', answer_image_path VARCHAR(255) DEFAULT '',
            irt_a DOUBLE DEFAULT 1.0, irt_b DOUBLE DEFAULT 0.0, irt_c DOUBLE DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS user_question_state (
            question_id INTEGER PRIMARY KEY, lambda_ DOUBLE DEFAULT 0.3,
            last_review VARCHAR(64) NOT NULL DEFAULT '', accuracy DOUBLE DEFAULT 0.0,
            times_correct INTEGER DEFAULT 0, times_wrong INTEGER DEFAULT 0,
            consecutive_correct INTEGER DEFAULT 0,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS answer_records (
            id INTEGER PRIMARY KEY AUTO_INCREMENT, question_id INTEGER NOT NULL,
            time_spent DOUBLE NOT NULL, is_correct INTEGER NOT NULL,
            strokes JSON, session_id INTEGER,
            user_theta_snapshot DOUBLE, irt_theta_snapshot DOUBLE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS config (
            `key` VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS knowledge_nodes (
            id INTEGER PRIMARY KEY AUTO_INCREMENT, name VARCHAR(255) NOT NULL UNIQUE,
            subject VARCHAR(64) NOT NULL, ideal_retention DOUBLE DEFAULT 0.8,
            current_mastery DOUBLE DEFAULT 0.0, rolling_accuracy DOUBLE DEFAULT 0.5,
            irt_theta DOUBLE DEFAULT 0.0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS knowledge_dependency (
            node_id INTEGER NOT NULL, prerequisite_node_id INTEGER NOT NULL,
            PRIMARY KEY (node_id, prerequisite_node_id),
            FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(prerequisite_node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS question_node_mapping (
            question_id INTEGER NOT NULL, node_id INTEGER NOT NULL,
            PRIMARY KEY (question_id, node_id),
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS user_study_sessions (
            id INTEGER PRIMARY KEY AUTO_INCREMENT, session_start VARCHAR(64) NOT NULL,
            last_action VARCHAR(64) NOT NULL, total_questions INTEGER DEFAULT 0,
            accumulated_minutes DOUBLE DEFAULT 0.0, current_fatigue DOUBLE DEFAULT 0.0
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS cat_exam_sessions (
            id INTEGER PRIMARY KEY AUTO_INCREMENT, user_id INTEGER NOT NULL,
            current_theta DOUBLE DEFAULT 0.0, task_count INTEGER DEFAULT 0,
            max_tasks INTEGER DEFAULT 20, is_completed INTEGER DEFAULT 0,
            question_history JSON, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB""",
        """CREATE TABLE IF NOT EXISTS cat_exam_details (
            id INTEGER PRIMARY KEY AUTO_INCREMENT, session_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL, user_answer TEXT, is_correct INTEGER,
            theta_before DOUBLE, theta_after DOUBLE, response_time_secs INTEGER,
            FOREIGN KEY(session_id) REFERENCES cat_exam_sessions(id)
        ) ENGINE=InnoDB""",
    ]
    for sql in tables:
        db.execute(sql)

    # MySQL indexes (CREATE INDEX IF NOT EXISTS not supported — catch duplicates)
    _mysql_indexes = [
        "CREATE INDEX idx_answer_records_qid ON answer_records(question_id)",
        "CREATE INDEX idx_answer_records_sid ON answer_records(session_id)",
        "CREATE INDEX idx_qnm_node_id ON question_node_mapping(node_id)",
    ]
    for idx_sql in _mysql_indexes:
        try:
            db.execute(idx_sql)
        except Exception:
            pass  # index already exists

    for k, v in DEFAULT_CONFIG.items():
        db.execute(
            "INSERT IGNORE INTO config (`key`, value) VALUES (?, ?)", (k, v)
        )
    db.commit()
    db.close()


# --- Run schema init at import time ---
if DB_TYPE == "mysql":
    try:
        _init_mysql_schema()
    except Exception as e:
        import logging
        logging.getLogger("Practice").warning("MySQL schema init failed: %s", e)
else:
    _init_sqlite_schema()
    # SQLite incremental migrations (no-op on MySQL since schema is created fresh)
    try:
        db = sqlite3.connect(DATABASE)
        # v3-v6 were already handled in the unified CREATE above via IF NOT EXISTS
        db.execute("UPDATE questions SET irt_b = 6.0 * difficulty - 3.0 WHERE irt_b = 0.0 OR irt_b IS NULL")
        # v7: consecutive_correct for wrong-question reinforcement mode
        try:
            db.execute("ALTER TABLE user_question_state ADD COLUMN consecutive_correct INTEGER DEFAULT 0")
        except Exception:
            pass  # column already exists
        db.commit()
        db.close()
    except Exception:
        pass

    # MySQL migration: consecutive_correct column (safe to re-run)
    if DB_TYPE == "mysql":
        try:
            mig_conn = _create_mysql_conn()
            mig_conn.execute(
                "ALTER TABLE user_question_state ADD COLUMN consecutive_correct INTEGER DEFAULT 0"
            )
            mig_conn.commit()
            mig_conn.close()
        except Exception:
            pass  # column already exists


# ================================================================
# Backward-compatible re-exports
# ================================================================
from practice.repository.knowledge_repo import (  # noqa: E402
    get_prerequisite_retentions, get_node_avg_retention,
    get_node_sliding_accuracy, get_node_avg_lambda, update_node_mastery,
)
from practice.repository.question_repo import (  # noqa: E402
    get_config, get_config_float, get_config_int,
)
