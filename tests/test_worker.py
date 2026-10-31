"""LibraryWorker with a real GLib main loop (no display needed)."""

import threading
import time

import pytest
from gi.repository import GLib

from glafka.ui.worker import LibraryWorker


@pytest.fixture
def worker():
    w = LibraryWorker()
    yield w
    w.shutdown()


def run_until(done: list, timeout: float = 5.0) -> None:
    """Pump the GLib main loop until `done` is non-empty."""
    loop = GLib.MainLoop()

    def check() -> bool:
        if done:
            loop.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(10, check)
    GLib.timeout_add(int(timeout * 1000), lambda: loop.quit() or GLib.SOURCE_REMOVE)
    loop.run()
    assert done, "worker did not finish in time"


def test_items_and_done_arrive_on_main_thread_in_order(tmp_path, worker):
    seen: list[tuple[int, bool]] = []
    done: list = []
    job_threads: list[str] = []

    def job(library):
        job_threads.append(threading.current_thread().name)
        assert library.root_dir == tmp_path
        yield from range(5)

    worker.submit(
        tmp_path,
        job,
        on_item=lambda i: seen.append((i, threading.current_thread() is threading.main_thread())),
        on_done=lambda error: done.append(error),
    )
    run_until(done)

    assert seen == [(i, True) for i in range(5)]
    assert done == [None]
    assert job_threads[0].startswith("glafka-worker")


def test_cancel_stops_between_items(tmp_path, worker):
    seen: list[int] = []
    done: list = []
    closed: list[bool] = []

    def job(_library):
        try:
            for i in range(100):
                time.sleep(0.01)
                yield i
        finally:
            closed.append(True)  # generator closed cleanly

    job_handle = worker.submit(tmp_path, job, on_item=seen.append, on_done=done.append)

    def on_first_item() -> bool:
        if seen:
            job_handle.cancel()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(5, on_first_item)
    run_until(done)

    assert job_handle.cancelled
    assert 1 <= len(seen) < 100
    assert done == [None]
    assert closed == [True]


def test_unexpected_error_reaches_on_done(tmp_path, worker):
    seen: list[int] = []
    done: list = []

    def job(_library):
        yield 1
        raise ValueError("bug in job")

    worker.submit(tmp_path, job, on_item=seen.append, on_done=done.append)
    run_until(done)

    assert seen == [1]
    assert isinstance(done[0], ValueError)


def test_jobs_run_one_at_a_time_in_order(tmp_path, worker):
    order: list[str] = []
    done: list = []

    def job(name):
        def run(_library):
            order.append(f"{name} start")
            time.sleep(0.05)
            order.append(f"{name} end")
            yield name

        return run

    worker.submit(tmp_path, job("a"), on_item=lambda _: None, on_done=lambda _e: None)
    worker.submit(tmp_path, job("b"), on_item=lambda _: None, on_done=done.append)
    run_until(done)

    assert order == ["a start", "a end", "b start", "b end"]
