"""Database layer — connection, init, migrations, and config helpers."""
import sqlite3
from flask import g

from practice import DATABASE, DEFAULT_CONFIG, practice_bp


# ================================================================
# Connection management
# ================================================================

def get_db():
    """Get a per-request SQLite connection stored on Flask's g."""
    if 'practice_db' not in g:
        g.practice_db = sqlite3.connect(DATABASE)
        g.practice_db.row_factory = sqlite3.Row
    return g.practice_db


@practice_bp.teardown_app_request
def close_db(_exception):
    db = g.pop('practice_db', None)
    if db is not None:
        db.close()


# ================================================================
# Schema initialization and migrations
# ================================================================

def init_db():
    """Create tables and seed default config."""
    db = sqlite3.connect(DATABASE)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            answer TEXT NOT NULL,
            subject TEXT DEFAULT '',
            type TEXT DEFAULT '',
            difficulty REAL DEFAULT 0.5,
            avg_cost REAL DEFAULT 5.0,
            source TEXT DEFAULT 'manual',
            content_type TEXT DEFAULT 'text',
            image_path TEXT DEFAULT '',
            answer_image_path TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_question_state (
            question_id INTEGER PRIMARY KEY,
            lambda_ REAL DEFAULT 0.3,
            last_review TEXT NOT NULL,
            accuracy REAL DEFAULT 0.0,
            times_correct INTEGER DEFAULT 0,
            times_wrong INTEGER DEFAULT 0,
            FOREIGN KEY (question_id) REFERENCES questions(id)
        );

        CREATE TABLE IF NOT EXISTS answer_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            time_spent REAL NOT NULL,
            is_correct INTEGER NOT NULL,
            strokes TEXT DEFAULT '[]',
            session_id INTEGER,
            user_theta_snapshot REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS knowledge_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            subject TEXT NOT NULL,
            ideal_retention REAL DEFAULT 0.8,
            current_mastery REAL DEFAULT 0.0,
            rolling_accuracy REAL DEFAULT 0.5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS knowledge_dependency (
            node_id INTEGER NOT NULL,
            prerequisite_node_id INTEGER NOT NULL,
            PRIMARY KEY (node_id, prerequisite_node_id),
            FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(prerequisite_node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS question_node_mapping (
            question_id INTEGER NOT NULL,
            node_id INTEGER NOT NULL,
            PRIMARY KEY (question_id, node_id),
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
            FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_start TEXT NOT NULL,
            last_action TEXT NOT NULL,
            total_questions INTEGER DEFAULT 0,
            accumulated_minutes REAL DEFAULT 0.0,
            current_fatigue REAL DEFAULT 0.0
        );
    ''')
    for k, v in DEFAULT_CONFIG.items():
        db.execute(
            'INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)', (k, v)
        )
    db.commit()
    db.close()


def _migrate_image_columns():
    """Add image columns to existing databases."""
    db = sqlite3.connect(DATABASE)
    try:
        db.execute('ALTER TABLE questions ADD COLUMN content_type TEXT NOT NULL DEFAULT "text"')
    except sqlite3.OperationalError:
        pass
    try:
        db.execute('ALTER TABLE questions ADD COLUMN image_path TEXT NOT NULL DEFAULT ""')
    except sqlite3.OperationalError:
        pass
    try:
        db.execute('ALTER TABLE questions ADD COLUMN answer_image_path TEXT NOT NULL DEFAULT ""')
    except sqlite3.OperationalError:
        pass
    db.commit()
    db.close()


def _migrate_knowledge_tables():
    """Add knowledge graph tables to existing databases."""
    db = sqlite3.connect(DATABASE)
    try:
        db.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                subject TEXT NOT NULL,
                ideal_retention REAL DEFAULT 0.8,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    except sqlite3.OperationalError:
        pass

    try:
        db.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_dependency (
                node_id INTEGER NOT NULL,
                prerequisite_node_id INTEGER NOT NULL,
                PRIMARY KEY (node_id, prerequisite_node_id),
                FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
                FOREIGN KEY(prerequisite_node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
            )
        ''')
    except sqlite3.OperationalError:
        pass

    try:
        db.execute('''
            CREATE TABLE IF NOT EXISTS question_node_mapping (
                question_id INTEGER NOT NULL,
                node_id INTEGER NOT NULL,
                PRIMARY KEY (question_id, node_id),
                FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
                FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
            )
        ''')
    except sqlite3.OperationalError:
        pass

    db.commit()
    db.close()


def _migrate_v3_schema():
    """Add v3 columns: session tracking, mastery, fatigue fields."""
    db = sqlite3.connect(DATABASE)
    migrations = [
        ("ALTER TABLE knowledge_nodes ADD COLUMN current_mastery REAL DEFAULT 0.0", "current_mastery"),
        ("ALTER TABLE knowledge_nodes ADD COLUMN rolling_accuracy REAL DEFAULT 0.0", "rolling_accuracy"),
        ("ALTER TABLE answer_records ADD COLUMN session_id INTEGER", "session_id"),
        ("ALTER TABLE answer_records ADD COLUMN user_theta_snapshot REAL", "user_theta_snapshot"),
    ]
    for sql, _ in migrations:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass
    db.commit()
    db.close()


def _migrate_v4_schema():
    """v4: Normalize user_question_state PK to question_id, fix source default."""
    db = sqlite3.connect(DATABASE)

    try:
        db.execute("UPDATE questions SET source = 'manual' WHERE source = '' OR source IS NULL")
    except sqlite3.OperationalError:
        pass

    try:
        cols = db.execute("PRAGMA table_info(user_question_state)").fetchall()
        col_names = [c[1] for c in cols]
        if 'id' in col_names and 'question_id' in col_names:
            db.execute('''
                CREATE TABLE IF NOT EXISTS user_question_state_v4 (
                    question_id INTEGER PRIMARY KEY,
                    lambda_ REAL DEFAULT 0.3,
                    last_review TEXT NOT NULL DEFAULT '',
                    accuracy REAL DEFAULT 0.0,
                    times_correct INTEGER DEFAULT 0,
                    times_wrong INTEGER DEFAULT 0,
                    FOREIGN KEY (question_id) REFERENCES questions(id)
                )
            ''')
            rows = db.execute(
                'SELECT question_id, lambda_, COALESCE(last_review, \'\'), accuracy, times_correct, times_wrong FROM user_question_state'
            ).fetchall()
            for r in rows:
                db.execute(
                    'INSERT OR IGNORE INTO user_question_state_v4 (question_id, lambda_, last_review, accuracy, times_correct, times_wrong) VALUES (?, ?, ?, ?, ?, ?)',
                    r
                )
            db.execute('DROP TABLE user_question_state')
            db.execute('ALTER TABLE user_question_state_v4 RENAME TO user_question_state')
    except sqlite3.OperationalError:
        pass

    db.commit()
    db.close()


def _migrate_v5_irt_schema():
    """v5: Add IRT 3PL columns for adaptive parameter calibration."""
    db = sqlite3.connect(DATABASE)

    irt_migrations = [
        "ALTER TABLE knowledge_nodes ADD COLUMN irt_theta REAL DEFAULT 0.0",
        "ALTER TABLE questions ADD COLUMN irt_a REAL DEFAULT 1.0",
        "ALTER TABLE questions ADD COLUMN irt_b REAL DEFAULT 0.0",
        "ALTER TABLE questions ADD COLUMN irt_c REAL DEFAULT 0.0",
        "ALTER TABLE answer_records ADD COLUMN irt_theta_snapshot REAL",
    ]
    for sql in irt_migrations:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass

    # Map existing [0,1] static difficulty to IRT [-3,3] scale: irt_b = 6 * difficulty - 3
    try:
        db.execute(
            "UPDATE questions SET irt_b = 6.0 * difficulty - 3.0 WHERE irt_b = 0.0 OR irt_b IS NULL"
        )
    except sqlite3.OperationalError:
        pass

    db.commit()
    db.close()


# ================================================================
# Backward-compatible re-exports (canonical locations in repository/)
# ================================================================

from practice.repository.knowledge_repo import (  # noqa: E402
    get_prerequisite_retentions, get_node_avg_retention,
    get_node_sliding_accuracy, get_node_avg_lambda, update_node_mastery,
)
from practice.repository.question_repo import (  # noqa: E402
    get_config, get_config_float, get_config_int,
)
