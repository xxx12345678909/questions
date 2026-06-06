"""Production WSGI entry point — Waitress multi-threaded server.

Usage:
    python wsgi.py                # default port 8080
    python wsgi.py --port 5000    # custom port
"""
import sys

from practice import create_app

app = create_app()

if __name__ == "__main__":
    port = 8080
    if "--port" in sys.argv:
        try:
            idx = sys.argv.index("--port")
            port = int(sys.argv[idx + 1])
        except (ValueError, IndexError):
            pass

    try:
        from waitress import serve
        print(f"Waitress WSGI server starting on port {port} (threads=8) ...")
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        print("waitress not installed — falling back to Flask dev server")
        app.run(host="0.0.0.0", port=port, debug=False)
