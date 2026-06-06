"""
Process-isolated background worker with debounce/dedup — replaces threading
to bypass the Python GIL.  Uses multiprocessing.Queue for IPC so the heavy
mastery-recalc loop runs on a fully independent CPU core.

Key optimisations:
  - multiprocessing.Process → owns a separate interpreter & GIL
  - debounce time-window (default 2.0s) → merges duplicate node_id tasks
    so that update_node_mastery is called at most once per node per window
"""
import multiprocessing
import queue
import time
import logging

# Cross-process-safe bounded queue singleton
task_queue = multiprocessing.Queue(maxsize=2000)
logger = logging.getLogger("ProcessWorkerEngine")


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
    db = sqlite_db_factory_func()
    logger.info("Adaptive-scheduler subprocess started (GIL-free, debounce active).")

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
    """Launch the background compute subprocess (called at app startup)."""
    p = multiprocessing.Process(
        target=run_debounced_worker_loop,
        args=(task_queue, sqlite_db_factory_func),
        daemon=True,
    )
    p.start()
    logger.info("Process-isolated worker cluster launched (pid=%d).", p.pid)
    return p
