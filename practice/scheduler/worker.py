"""
Asynchronous background-worker — lightweight in-process task queue with debounce.

The worker drains tasks from a thread-safe queue on a daemon thread,
each with its own SQLite connection to avoid multi-threaded context
deadlocks.  A 2-second debounce window merges duplicate node_id tasks
so update_node_mastery runs at most once per node per window.

This keeps heavy aggregation (mastery recalc, DAG path search) out of
the HTTP request/response hot-path.
"""
import queue
import threading
import logging
import time

task_queue = queue.Queue(maxsize=1000)
logger = logging.getLogger("PracticeWorker")

DEBOUNCE_SECONDS = 2.0


class GraphTaskType:
    UPDATE_NODE_MASTERY = "UPDATE_NODE_MASTERY"
    RECALC_ALL_GRAPH = "RECALC_ALL_GRAPH"


def background_worker_loop(db_factory_func):
    """Daemon event-loop with debounce — drains queue in time windows, deduplicates."""
    db = db_factory_func()
    logger.info(
        "Background adaptive-computation worker thread started (debounce=%ss).",
        DEBOUNCE_SECONDS,
    )

    while True:
        try:
            # --- Block for the first task ---
            first_task = task_queue.get(block=True)

            window_start = time.time()
            debounce_set = set()

            if first_task.get("type") == GraphTaskType.UPDATE_NODE_MASTERY:
                debounce_set.add(first_task["payload"].get("node_id"))

            # --- Non-blocking drain within the debounce window ---
            while time.time() - window_start < DEBOUNCE_SECONDS:
                try:
                    next_task = task_queue.get(block=False)
                    if next_task.get("type") == GraphTaskType.UPDATE_NODE_MASTERY:
                        debounce_set.add(next_task["payload"].get("node_id"))
                except queue.Empty:
                    time.sleep(0.05)

            # --- Execute deduplicated work ---
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
                    logger.exception("Worker db.commit() failed")

        except Exception:
            logger.exception("Async worker pipeline error")


def init_worker_thread(db_factory_func):
    """Launch the singleton daemon worker thread (called at app startup)."""
    t = threading.Thread(
        target=background_worker_loop,
        args=(db_factory_func,),
        daemon=True,
    )
    t.start()
