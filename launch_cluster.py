"""Launch multiple Waitress instances for multi-process scaling.

Usage:
    python launch_cluster.py              # 3 instances on ports 8080-8082
    python launch_cluster.py 4            # 4 instances
    python launch_cluster.py 4 9000       # 4 instances starting at port 9000
"""
import subprocess
import sys
import time
import signal
import os

INSTANCES = int(sys.argv[1]) if len(sys.argv) > 1 else 3
BASE_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8090
THREADS = 6  # per instance

processes = []

def launch(port):
    env = os.environ.copy()
    env["DB_TYPE"] = "mysql"
    env["MYSQL_POOL_SIZE"] = str(THREADS + 2)
    p = subprocess.Popen(
        [sys.executable, "wsgi.py", "--port", str(port), "--threads", str(THREADS)],
        env=env,
    )
    return p

def cleanup(*_):
    print("\nShutting down...")
    for p in processes:
        p.terminate()
    for p in processes:
        p.wait()
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

print(f"Launching {INSTANCES} Waitress instances ({THREADS} threads each)...")
print(f"Total: {INSTANCES * THREADS} threads across {INSTANCES} processes")
print()

for i in range(INSTANCES):
    port = BASE_PORT + i
    p = launch(port)
    processes.append(p)
    print(f"  [{i+1}] http://127.0.0.1:{port}  pid={p.pid}")
    time.sleep(0.5)  # stagger startup

print(f"\nAll {INSTANCES} instances running. Press Ctrl+C to stop.")
print(f"Benchmark: set BASE_URL to any port and each process handles ~500 QPS")

try:
    for p in processes:
        p.wait()
except KeyboardInterrupt:
    cleanup()
