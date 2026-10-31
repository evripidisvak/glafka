import logging
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from gi.repository import GLib

from glafka.core.errors import RECOVERABLE_ERRORS
from glafka.core.library import LibraryManager

log = logging.getLogger(__name__)

T = TypeVar("T")


def _on_main_thread(fn: Callable[..., None], *args: Any) -> None:
    def call() -> bool:
        fn(*args)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(call)


class Job:
    """Handle for a submitted job; safe to use from any thread."""

    def __init__(self, root: Path) -> None:
        self.root = root  # library the job works on
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


class LibraryWorker:
    """One background thread with its own library connection.

    Jobs run one at a time, in order; import's check-then-write logic relies on that.
    Callbacks run on the main thread, so they may touch GTK. Jobs must not.
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="glafka-worker")
        self._library: LibraryManager | None = None  # worker thread only
        self._jobs: set[Job] = set()  # main thread only

    def submit(
        self,
        root: Path,
        job_fn: Callable[[LibraryManager], Iterable[T]],
        on_item: Callable[[T], None],
        on_done: Callable[[Exception | None], None],
    ) -> Job:
        """Run job_fn(library) on the worker; on_item per yielded item, then on_done(error).

        Cancelling stops before the next item; on_done still runs.
        """
        job = Job(root)
        self._jobs.add(job)

        def finish(error: Exception | None) -> None:
            self._jobs.discard(job)
            on_done(error)

        def run() -> None:
            error: Exception | None = None
            items: Iterable[T] | None = None
            try:
                if not job.cancelled:
                    items = job_fn(self._library_for(root))
                    iterator = iter(items)
                    while not job.cancelled:
                        try:
                            item = next(iterator)
                        except StopIteration:
                            break
                        _on_main_thread(on_item, item)
            except RECOVERABLE_ERRORS as e:  # expected (bad file, wrong password): the UI shows it
                log.warning("Background job failed: %s", e)
                error = e
            except Exception as e:  # a bug: keep the traceback
                log.exception("Background job failed")
                error = e
            finally:
                close = getattr(items, "close", None)
                if close is not None:
                    close()  # a generator stops cleanly between items
            _on_main_thread(finish, error)

        self._executor.submit(run)
        return job

    def cancel_all(self) -> None:
        for job in list(self._jobs):
            job.cancel()

    def shutdown(self) -> None:
        """Cancel all jobs; the current item finishes, then the connection closes in its thread."""
        self.cancel_all()
        self._executor.submit(self._close_library)
        self._executor.shutdown(wait=False)

    # Worker thread only

    def _library_for(self, root: Path) -> LibraryManager:
        if self._library is None or self._library.root_dir != root:
            self._close_library()
            self._library = LibraryManager.open(root)
        return self._library

    def _close_library(self) -> None:
        if self._library is not None:
            self._library.close()
            self._library = None
