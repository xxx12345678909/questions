"""
智能复习与刷题系统 (Intelligent Review & Practice System)
- Question bank CRUD with filtering and search
- Smart recommendation (3-pool: review/wrong/new, ratio-based priority)
- Canvas-based answering with stroke recording
- Forgetting curve: R(t) = exp(-lambda * hours_since_review)
- Closed-loop: answer submission updates user model, feeds back to recommendation

Layered architecture (v5):
  core/         — pure micro-memory mechanism (Ebbinghaus)
  graph/        — pure graph theory & knowledge topology
  adaptive/     — pure fatigue & IRT modeling
  repository/   — SQL data extraction (I/O decoupling)
  scheduler/    — recommendation orchestration
  routes/       — response layer (manage / graph / recommend)
"""
import os
from flask import Blueprint

practice_bp = Blueprint('practice', __name__)

DATABASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'practice.db')
UPLOADS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'practice_uploads')
os.makedirs(UPLOADS_FOLDER, exist_ok=True)

# Subject weights for priority computation
SUBJECT_WEIGHTS = {
    '高数': 1.2, '线代': 1.1, '408': 1.3, '英语': 0.9,
    '概率': 1.1, '政治': 0.9, '算法': 1.2, '数学': 1.1
}

DEFAULT_CONFIG = {
    'daily_question_budget': '30',
    'review_ratio': '0.6',
    'wrong_ratio': '0.2',
    'new_ratio': '0.2',
    'retention_threshold': '0.6',
    'max_consecutive_type': '5',
    'enable_irt': 'true',
}

# Database initialization
from practice.db import init_db, _migrate_image_columns, _migrate_knowledge_tables, _migrate_v3_schema, _migrate_v4_schema, _migrate_v5_irt_schema, _migrate_v6_cat_schema  # noqa: E402

init_db()
_migrate_image_columns()
_migrate_knowledge_tables()
_migrate_v3_schema()
_migrate_v4_schema()
_migrate_v5_irt_schema()
_migrate_v6_cat_schema()

# Register sub-blueprints on the main practice_bp
from practice.routes.manage import manage_bp       # noqa: E402
from practice.routes.graph import graph_bp         # noqa: E402
from practice.routes.recommend import recommend_bp # noqa: E402
from practice.routes.cat import cat_bp             # noqa: E402

practice_bp.register_blueprint(manage_bp)
practice_bp.register_blueprint(graph_bp)
practice_bp.register_blueprint(recommend_bp)
practice_bp.register_blueprint(cat_bp)

# ----------------------------------------------------------------
# Async worker — background daemon for heavy graph computation
# ----------------------------------------------------------------

def _sqlite_db_factory():
    """Return a fresh, independent SQLite connection for the worker thread.

    Enables WAL journal mode so that concurrent reads (HTTP threads) and
    writes (this worker) can coexist without SQLITE_BUSY conflicts.
    Uses extended timeout so busy writers queue up instead of failing fast.
    """
    import sqlite3
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

from practice.scheduler.worker import init_worker_thread  # noqa: E402

try:
    init_worker_thread(_sqlite_db_factory)
except Exception:
    import logging
    logging.getLogger("Practice").warning(
        "Background async-computation worker failed to mount"
    )
