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
    'enable_v5_process': 'true',
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

# Thread-based worker (legacy fallback)
from practice.scheduler.worker import init_worker_thread  # noqa: E402

try:
    init_worker_thread(_sqlite_db_factory)
except Exception:
    import logging
    logging.getLogger("Practice").warning(
        "Background async-computation thread worker failed to mount"
    )

# Process-isolated worker (primary — bypasses GIL, has debounce)
def _sqlite_process_safe_factory():
    import sqlite3
    conn = sqlite3.connect(DATABASE, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

# V5 process worker — only on Linux (Windows multiprocessing.Process requires __main__ guard)
import os as _os
import logging as _logging

_v5_enabled = DEFAULT_CONFIG.get('enable_v5_process', 'true')
try:
    import sqlite3 as _sql
    _cfg_conn = _sql.connect(DATABASE)
    _cfg_conn.row_factory = _sql.Row
    _row = _cfg_conn.execute("SELECT value FROM config WHERE key='enable_v5_process'").fetchone()
    if _row:
        _v5_enabled = _row['value']
    _cfg_conn.close()
except Exception:
    pass

if _v5_enabled in ('true', '1', 'yes', True) and _os.name == 'posix':
    # Linux/macOS: multiprocessing.Process with fork — full GIL bypass
    from practice.scheduler.process_worker import init_isolated_process_cluster  # noqa: E402
    try:
        _proc = init_isolated_process_cluster(_sqlite_process_safe_factory)
        if _proc:
            _logging.getLogger("Practice").info(
                "V5 process-isolated worker mounted (pid=%d)", _proc.pid
            )
    except Exception:
        _logging.getLogger("Practice").warning(
            "Process-isolated worker cluster failed to mount — using thread worker with debounce"
        )
elif _os.name == 'nt':
    _logging.getLogger("Practice").info(
        "Windows detected — using thread worker with debounce (multiprocessing skipped)"
    )
else:
    _logging.getLogger("Practice").info(
        "V5 process worker disabled — using thread worker with debounce"
    )


def create_app():
    """
    Application factory — wires the practice blueprint into a Flask app
    with all DB migrations run and background workers launched.

    Usage:
        from practice import create_app
        app = create_app()
    """
    from flask import Flask

    # Point template_folder at the project-root templates/ directory
    _project_root = os.path.dirname(os.path.dirname(__file__))
    app = Flask(__name__, template_folder=os.path.join(_project_root, "templates"))

    app.register_blueprint(practice_bp, url_prefix="/practice")

    @app.route("/")
    def root():
        from flask import redirect
        return redirect("/practice/")

    return app
