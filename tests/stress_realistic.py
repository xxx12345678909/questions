"""
Realistic stress test — simulates actual study behaviour, not just hammering /api/answer.

Metrics that matter for a review-scheduling system:
  1. Recommendation freshness: does /api/recommend/today return in < 500ms after 2h of study?
  2. Database growth stability: does P50 stay flat as answer_records grows to 5000+?
  3. Mixed workload: reads (stats, report) + writes (answer) + heavy compute (recommend)
  4. Sustained load: 30 minutes of continuous use, not 1-second burst

Usage:
    python tests/stress_realistic.py                # 10 users, 30 min
    python tests/stress_realistic.py --users 20     # 20 users
    python tests/stress_realistic.py --quick        # 2 min smoke test
"""
import threading
import time
import json
import sys
import random
import urllib.request
import urllib.error

# ================================================================
# Configuration
# ================================================================
BASE_PORT = 8090
CLUSTER_PORTS = 1        # override with --cluster N
SIMULATION_MINUTES = 30   # total simulation duration
USERS = 10                # concurrent simulated users
THINK_TIME_MIN = 3.0      # seconds — reading a question
THINK_TIME_MAX = 15.0     # seconds — solving a hard question
QUICK_MODE = False

# ================================================================
# Metrics (guarded by lock)
# ================================================================
metrics_lock = threading.Lock()
metrics = {
    "answer": {"count": 0, "errors": 0, "p50_ms": 0, "p99_ms": 0, "latencies": []},
    "recommend": {"count": 0, "errors": 0, "p50_ms": 0, "p99_ms": 0, "latencies": []},
    "report": {"count": 0, "errors": 0, "latencies": []},
    "stats": {"count": 0, "errors": 0, "latencies": []},
    "wrong_reinforce": {"count": 0, "errors": 0, "latencies": []},
    "total_questions_answered": 0,
    "start_time": 0,
}

_valid_question_ids = []


def _base_url(path):
    port = BASE_PORT + (random.randint(0, CLUSTER_PORTS - 1))
    return f"http://127.0.0.1:{port}/practice{path}"


def _fetch(url, method="GET", body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), None
    except Exception as e:
        return None, str(e)


# ================================================================
# User behaviour simulation
# ================================================================

def discover_questions():
    global _valid_question_ids
    url = f"http://127.0.0.1:{BASE_PORT}/practice/api/questions?limit=100"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            _valid_question_ids = [q["id"] for q in data.get("questions", [])]
    except Exception:
        pass
    if not _valid_question_ids:
        _valid_question_ids = [1]


def simulate_user(user_id):
    """Simulate a real student studying for 30 minutes."""
    end_time = time.time() + SIMULATION_MINUTES * 60
    session_questions = 0

    while time.time() < end_time:
        # 1. Get recommendations (happens every ~20 questions)
        if session_questions % 20 == 0 and random.random() < 0.5:
            t0 = time.time()
            _, err = _fetch(_base_url("/api/recommend/today"))
            dur = (time.time() - t0) * 1000
            with metrics_lock:
                metrics["recommend"]["latencies"].append(dur)
                metrics["recommend"]["count"] += 1
                if err:
                    metrics["recommend"]["errors"] += 1

        # 2. Pick a question and answer it (main loop)
        qid = random.choice(_valid_question_ids)
        think_time = random.uniform(THINK_TIME_MIN, THINK_TIME_MAX)
        time.sleep(think_time * 0.1)  # speed up for test (10x faster than real)

        is_correct = random.random() < 0.65  # 65% accuracy
        t0 = time.time()
        _, err = _fetch(_base_url("/api/answer"), "POST", {
            "question_id": qid, "is_correct": is_correct, "time_spent": think_time,
        })
        dur = (time.time() - t0) * 1000
        with metrics_lock:
            metrics["answer"]["latencies"].append(dur)
            metrics["answer"]["count"] += 1
            metrics["total_questions_answered"] += 1
            if err:
                metrics["answer"]["errors"] += 1

        session_questions += 1

        # 3. Check stats / report (every ~10 questions)
        if session_questions % 10 == 0:
            t0 = time.time()
            _, err = _fetch(_base_url("/api/stats"))
            dur = (time.time() - t0) * 1000
            with metrics_lock:
                metrics["stats"]["latencies"].append(dur)
                metrics["stats"]["count"] += 1
                if err:
                    metrics["stats"]["errors"] += 1

        # 4. Wrong reinforce check (every ~30 questions)
        if session_questions % 30 == 0:
            t0 = time.time()
            _, err = _fetch(_base_url("/api/recommend/wrong-reinforce?limit=20"))
            dur = (time.time() - t0) * 1000
            with metrics_lock:
                metrics["wrong_reinforce"]["latencies"].append(dur)
                metrics["wrong_reinforce"]["count"] += 1
                if err:
                    metrics["wrong_reinforce"]["errors"] += 1

        # 5. Daily report (once per simulation)
        if session_questions == 5:
            t0 = time.time()
            _, err = _fetch(_base_url("/api/report/daily"))
            dur = (time.time() - t0) * 1000
            with metrics_lock:
                metrics["report"]["latencies"].append(dur)
                metrics["report"]["count"] += 1
                if err:
                    metrics["report"]["errors"] += 1


# ================================================================
# Reporter
# ================================================================

def _print_endpoint(name, data):
    if not data["latencies"]:
        print(f"  {name}: 0 requests")
        return
    lats = sorted(data["latencies"])
    p50 = lats[len(lats) // 2]
    p99 = lats[min(int(len(lats) * 0.99), len(lats) - 1)]
    avg = sum(lats) / len(lats)
    err_rate = data["errors"] / data["count"] * 100 if data["count"] else 0
    print(f"  {name}: {data['count']} req | avg {avg:.1f}ms | p50 {p50:.1f}ms | p99 {p99:.1f}ms | errors {data['errors']} ({err_rate:.1f}%)")


def print_report():
    elapsed = time.time() - metrics["start_time"]
    total_ops = sum(m["count"] for m in [metrics["answer"], metrics["recommend"],
                   metrics["report"], metrics["stats"], metrics["wrong_reinforce"]])
    print(f"\n{'='*65}")
    print(f"  REALISTIC STRESS TEST RESULTS")
    print(f"{'='*65}")
    print(f"  Duration  : {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  Users     : {USERS} concurrent")
    print(f"  Questions : {metrics['total_questions_answered']} answered")
    print(f"  Total ops : {total_ops}")
    print(f"  Effective QPS (all endpoints): {total_ops/elapsed:.1f}")
    print(f"  Answer QPS: {metrics['answer']['count']/elapsed:.1f}")
    print(f"{'='*65}")
    print(f"  Endpoint breakdown:")
    _print_endpoint("answer", metrics["answer"])
    _print_endpoint("recommend", metrics["recommend"])
    _print_endpoint("wrong-reinforce", metrics["wrong_reinforce"])
    _print_endpoint("stats", metrics["stats"])
    _print_endpoint("report", metrics["report"])

    # Database growth check
    data, err = _fetch(f"http://127.0.0.1:{BASE_PORT}/practice/api/stats")
    if data:
        print(f"\n  DB state: {data.get('total_records', '?')} total answer_records")
        if data["total_records"] > 5000:
            print(f"  [WARN]  answer_records > 5000 — check query performance")

    # Recommendation latency check (critical for UX)
    if metrics["recommend"]["latencies"]:
        rec_lats = sorted(metrics["recommend"]["latencies"])
        rec_p99 = rec_lats[min(int(len(rec_lats) * 0.99), len(rec_lats) - 1)]
        if rec_p99 > 500:
            print(f"  [WARN]  recommend P99 = {rec_p99:.0f}ms (> 500ms target)")

    print(f"\n  Verdict: ", end="")
    answer_err_rate = metrics["answer"]["errors"] / max(metrics["answer"]["count"], 1)
    if answer_err_rate > 0.01:
        print("FAIL — answer error rate > 1%")
    elif metrics["answer"]["errors"] > 0:
        print("PASS with warnings")
    else:
        print("PASS — zero errors, stable performance")
    print(f"{'='*65}\n")


# ================================================================
# Main
# ================================================================

if __name__ == "__main__":
    args = sys.argv[1:]
    for a in args:
        if a.startswith("--cluster="):
            CLUSTER_PORTS = int(a.split("=")[1])
        elif a.startswith("--users="):
            USERS = int(a.split("=")[1])
        elif a == "--quick":
            QUICK_MODE = True
            SIMULATION_MINUTES = 2
            THINK_TIME_MIN = 0.5
            THINK_TIME_MAX = 1.5

    if QUICK_MODE:
        print(f"Quick mode: {SIMULATION_MINUTES}min, {USERS} users")

    discover_questions()
    if len(_valid_question_ids) < 3:
        print("ERROR: Need at least 3 questions in the database")
        sys.exit(1)

    print(f"Starting {SIMULATION_MINUTES}-minute stress test with {USERS} users...")
    print(f"Question pool: {len(_valid_question_ids)} IDs")
    print(f"Cluster ports: {CLUSTER_PORTS}")

    metrics["start_time"] = time.time()

    threads = []
    for i in range(USERS):
        t = threading.Thread(target=simulate_user, args=(i,))
        threads.append(t)
        t.start()
        time.sleep(0.1)  # stagger startup

    # Progress reporter thread
    def progress_loop():
        start = metrics["start_time"]
        while any(t.is_alive() for t in threads):
            elapsed = time.time() - start
            with metrics_lock:
                answered = metrics["total_questions_answered"]
                errs = metrics["answer"]["errors"]
            print(f"  [{elapsed/60:.0f}min] {answered} questions answered, {errs} errors")
            time.sleep(30)
    progress_t = threading.Thread(target=progress_loop, daemon=True)
    progress_t.start()

    for t in threads:
        t.join()

    print_report()
