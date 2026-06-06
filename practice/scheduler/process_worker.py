"""
Process-isolated background worker with debounce/dedup — replaces threading
to bypass the Python GIL.  Uses multiprocessing.Queue for IPC so the heavy
mastery-recalc loop runs on a fully independent CPU core.

Windows-safe: uses 'spawn' context + env-var guard to prevent recursive forking.
"""
import multiprocessing
import os
import queue
import time
import logging

# Ensure spawn context on Windows (fork is not available)
if hasattr(multiprocessing, "set_start_method"):
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass  # already set

# Cross-process-safe bounded queue singleton
task_queue = multiprocessing.Queue(maxsize=2000)
logger = logging.getLogger("ProcessWorkerEngine")

# Environment variable to prevent recursive worker spawning in child processes
_ENV_WORKER_FLAG = "PRACTICE_PROCESS_WORKER_CHILD"


class ProcessTaskType:
    RECALC_MASTERY = "RECALC_MASTERY"


def run_debounced_worker_loop(task_q, sqlite_db_factory_func, interval_seconds=2.0):
    """
    Subprocess event-loop — fully decoupled from the main-process GIL.

    1. Block until the first task arrives (avoid CPU spin).
    2. Open a debounce window (interval_seconds), drain the queue
       non-blockingly, and merge duplicate node_ids into a dedup set.
    3. Execute update_node_mastery once per unique node_id, then loop.
    """
    # Mark this process as a worker child so practice/__init__.py won't re-spawn
    os.environ[_ENV_WORKER_FLAG] = "1"

    db = sqlite_db_factory_func()
    logger.info(
        "Adaptive-scheduler subprocess started (pid=%d, GIL-free, debounce=%ss).",
        os.getpid(), interval_seconds,
    )

    while True:
        try:
            # --- Block for the first task ---
            first_task = task_q.get(block=True)

            window_start = time.time()
            debounce_set = set()

            if first_task.get("type") == ProcessTaskType.RECALC_MASTERY:
                debounce_set.add(first_task["payload"]["node_id"])

            # --- Non-blocking drain within the debounce window ---
            while time.time() - window_start < interval_seconds:
                try:
                    next_task = task_q.get(block=False)
                    if next_task.get("type") == ProcessTaskType.RECALC_MASTERY:
                        debounce_set.add(next_task["payload"]["node_id"])
                except queue.Empty:
                    time.sleep(0.05)

            # --- Commit deduplicated work ---
            if debounce_set:
                logger.info(
                    "Debounce window closed — %d unique node(s) to recompute.",
                    len(debounce_set),
                )
                # Import inside the loop so the child process loads its own copy
                from practice.engine import update_node_mastery

                for node_id in debounce_set:
                    try:
                        update_node_mastery(db, node_id)
                    except Exception:
                        logger.exception(
                            "Mastery recompute failed for node #%s", node_id
                        )
                try:
                    db.commit()
                except Exception:
                    logger.exception("Subprocess db.commit() failed")

        except Exception:
            logger.exception("Subprocess pipeline exception")


def init_isolated_process_cluster(sqlite_db_factory_func):
    """
    Launch the background compute subprocess (called at app startup).

    On Windows this requires 'spawn' context.  Returns the Process handle
    or None if the worker is already running in a child process.
    """
    # --- Guard: prevent recursive spawning inside the worker child ---
    if os.environ.get(_ENV_WORKER_FLAG) == "1":
        logger.info("Running inside worker child — skipping cluster init.")
        return None

    try:
        p = multiprocessing.Process(
            target=run_debounced_worker_loop,
            args=(task_queue, sqlite_db_factory_func),
            daemon=True,
        )
        p.start()
        logger.info(
            "Process-isolated worker cluster launched (pid=%d, platform=%s).",
            p.pid, os.name,
        )
        return p
    except Exception:
        logger.exception("Process worker failed to mount (platform=%s)", os.name)
        return None
