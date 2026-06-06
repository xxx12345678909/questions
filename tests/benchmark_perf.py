"""
Zero-dependency concurrency benchmark for the adaptive review scheduling system.

Compares two architectural modes on /api/answer:
  A (sync):  mastery recompute runs inline on the HTTP thread  → high lock contention
  B (async): mastery recompute is offloaded to a background worker → minimal latency

Usage:
  python tests/benchmark_perf.py          # run both A and B
  python tests/benchmark_perf.py sync     # run only sync mode
  python tests/benchmark_perf.py async    # run only async mode
"""
import threading
import time
import json
import sys
import urllib.request
import urllib.error

# ================================================================
# Configuration
# ================================================================
BASE_URL = "http://127.0.0.1:8080/practice/api/answer"
QUESTIONS_URL = "http://127.0.0.1:8080/practice/api/questions?limit=100"
CONCURRENT_USERS = 30       # virtual concurrent users
REQUESTS_PER_USER = 10      # requests per user
TOTAL_EXPECTED = CONCURRENT_USERS * REQUESTS_PER_USER

# Shared state (guarded by status_lock)
latencies = []
error_count = 0
lock_error_count = 0
success_count = 0
status_lock = threading.Lock()

# Discovered question IDs (populated at startup to avoid data-penetration 404s)
_valid_question_ids = []


def _discover_question_ids():
    """Fetch real question IDs from the server — self-healing against DB gaps."""
    global _valid_question_ids
    try:
        req = urllib.request.Request(QUESTIONS_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            _valid_question_ids = [q["id"] for q in data.get("questions", [])]
    except Exception:
        pass
    if not _valid_question_ids:
        _valid_question_ids = [1]  # fallback
    return _valid_question_ids


def _pick_question_id(seq_id):
    """Map a sequence number to a real question ID (cycle through discovered IDs)."""
    if not _valid_question_ids:
        _discover_question_ids()
    return _valid_question_ids[seq_id % len(_valid_question_ids)]


def send_answer_request(user_id, seq_id, sync_mode=False):
    """Simulate one answer-submission HTTP request."""
    global error_count, lock_error_count, success_count

    url = BASE_URL + ("?sync=1" if sync_mode else "")
    payload = {
        "question_id": _pick_question_id(seq_id),
        "is_correct": (seq_id % 2 == 0),
        "time_spent": 15.5 + seq_id,
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            duration = (time.time() - start) * 1000.0
            with status_lock:
                latencies.append(duration)
                success_count += 1
    except urllib.error.HTTPError as e:
        duration = (time.time() - start) * 1000.0
        with status_lock:
            latencies.append(duration)
            error_count += 1
            if e.code == 500:
                lock_error_count += 1
    except Exception:
        with status_lock:
            error_count += 1


def virtual_user_thread(user_id, sync_mode):
    """Per-user thread loop."""
    for seq_id in range(REQUESTS_PER_USER):
        send_answer_request(user_id, seq_id, sync_mode)
        time.sleep(0.02)


def _reset_state():
    global latencies, error_count, lock_error_count, success_count
    latencies = []
    error_count = 0
    lock_error_count = 0
    success_count = 0


def _print_report(label, global_duration):
    if not latencies:
        print("  (no data collected)")
        return {}

    latencies.sort()
    avg = sum(latencies) / len(latencies)
    p95 = latencies[min(int(len(latencies) * 0.95), len(latencies) - 1)]
    p99 = latencies[min(int(len(latencies) * 0.99), len(latencies) - 1)]
    qps = TOTAL_EXPECTED / global_duration if global_duration > 0 else 0
    lock_pct = (lock_error_count / TOTAL_EXPECTED) * 100

    print(f"  Duration    : {global_duration:.3f}s")
    print(f"  QPS         : {qps:.1f} req/s")
    print(f"  Success     : {success_count}  Errors: {error_count}")
    print(f"  SQLite 500s : {lock_error_count} ({lock_pct:.1f}%)")
    print(f"  Min / Avg   : {latencies[0]:.1f} / {avg:.1f} ms")
    print(f"  P95 / P99   : {p95:.1f} / {p99:.1f} ms")
    print(f"  Max         : {latencies[-1]:.1f} ms")
    return {"label": label, "qps": qps, "avg_ms": avg, "p95_ms": p95,
            "p99_ms": p99, "lock_pct": lock_pct, "errors": error_count}


def run_benchmark(sync_mode, label):
    """Run one benchmark pass."""
    _reset_state()
    ids = _discover_question_ids()
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Users: {CONCURRENT_USERS}  x  Reqs/user: {REQUESTS_PER_USER}  =  {TOTAL_EXPECTED} total")
    print(f"  Question pool: {len(ids)} IDs ({min(ids)}-{max(ids)})")
    print(f"{'='*60}")

    threads = []
    t0 = time.time()
    for i in range(CONCURRENT_USERS):
        t = threading.Thread(target=virtual_user_thread, args=(i, sync_mode))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    return _print_report(label, elapsed)


def print_comparison(results):
    """Side-by-side comparison table."""
    if len(results) < 2:
        return
    a, b = results[0], results[1]
    print(f"\n{'='*70}")
    print("  COMPARISON SUMMARY")
    print(f"{'='*70}")
    print(f"  {'Metric':<20} {'A (Sync)':<18} {'B (Async)':<18} {'Improvement':<15}")
    print(f"  {'-'*20} {'-'*18} {'-'*18} {'-'*15}")

    def _cmp_row(name, a_val, b_val, unit="", lower_is_better=True):
        if a_val == 0:
            return
        if lower_is_better:
            change = (a_val - b_val) / a_val * 100 if a_val else 0
        else:
            change = (b_val - a_val) / a_val * 100 if a_val else 0
        arrow = "↓" if (lower_is_better and b_val < a_val) or (not lower_is_better and b_val > a_val) else "↑"
        print(f"  {name:<20} {a_val:>8.1f}{unit:<10} {b_val:>8.1f}{unit:<10} {arrow} {abs(change):.0f}%")

    _cmp_row("Avg Latency (ms)", a["avg_ms"], b["avg_ms"], "", True)
    _cmp_row("P95 Latency (ms)", a["p95_ms"], b["p95_ms"], "", True)
    _cmp_row("P99 Latency (ms)", a["p99_ms"], b["p99_ms"], "", True)
    _cmp_row("QPS (req/s)", a["qps"], b["qps"], "", False)
    _cmp_row("Lock Error Rate %", a["lock_pct"], b["lock_pct"], "", True)
    print(f"{'='*70}\n")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    results = []

    if mode in ("sync", "both"):
        results.append(run_benchmark(sync_mode=True, label="A组：同步阻塞模式 (sync)"))

    if mode in ("async", "both"):
        results.append(run_benchmark(sync_mode=False, label="B组：异步队列模式 (async)"))

    if len(results) == 2:
        print_comparison(results)

    print("Benchmark complete.")
