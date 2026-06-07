"""Production WSGI entry point — Waitress multi-threaded server.

Usage:
    python wsgi.py                   # default: 8 threads, port 8080
    python wsgi.py --port 5000       # custom port
    python wsgi.py --threads 16      # custom thread count

Tuning:
    threads = 2× CPU cores (I/O-heavy workload, GIL released during DB calls)
    MySQL pool size in db.py should be >= threads
"""
import os
import sys

# Suppress Flask/Werkzeug request logs — saves ~0.1ms per request in stderr I/O
os.environ.setdefault("WERKZEUG_RUN_MAIN", "true")
import logging as _logging
_logging.getLogger("werkzeug").setLevel(_logging.WARNING)

from practice import create_app

app = create_app()

if __name__ == "__main__":
    port = 8080
    threads = 8

    if "--port" in sys.argv:
        try:
            idx = sys.argv.index("--port")
            port = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass
    if "--threads" in sys.argv:
        try:
            idx = sys.argv.index("--threads")
            threads = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass

    try:
        from waitress import serve
        print(f"Waitress WSGI server starting on http://0.0.0.0:{port} (threads={threads})")
        serve(app, host="0.0.0.0", port=port, threads=threads,
              connection_limit=200, channel_timeout=120)
    except ImportError:
        print("waitress not installed — falling back to Flask dev server")
        app.run(host="0.0.0.0", port=port, debug=False)
