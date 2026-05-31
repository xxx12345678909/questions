"""
Asynchronous background-worker — lightweight in-process task queue.

The worker drains tasks from a thread-safe queue on a daemon thread,
each with its own SQLite connection to avoid multi-threaded context
deadlocks.  This keeps heavy aggregation (mastery recalc, DAG path
search) out of the HTTP request/response hot-path.
"""
import queue
import threading
import logging

task_queue = queue.Queue(maxsize=1000)
logger = logging.getLogger("PracticeWorker")


class GraphTaskType:
    UPDATE_NODE_MASTERY = "UPDATE_NODE_MASTERY"
    RECALC_ALL_GRAPH = "RECALC_ALL_GRAPH"


def background_worker_loop(db_factory_func):
    """Daemon event-loop — blocks on the task queue, processes graph tasks."""
    db = db_factory_func()
    logger.info("Background adaptive-computation worker thread started.")

    while True:
        try:
            task = task_queue.get(block=True)
            task_type = task.get("type")
            payload = task.get("payload", {})

            if task_type == GraphTaskType.UPDATE_NODE_MASTERY:
                node_id = payload.get("node_id")
                from practice.engine import update_node_mastery
                logger.info(
                    "Async recompute mastery for knowledge node #%s", node_id
                )
                update_node_mastery(db, node_id)

            task_queue.task_done()
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
