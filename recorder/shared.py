import queue
import threading
import time
from typing import Tuple


def monotonic_ns() -> int:
    """
    Monotonic host clock in nanoseconds.

    Uses perf_counter_ns() when available and falls back to perf_counter()
    for older Python versions.
    """
    if hasattr(time, "perf_counter_ns"):
        return time.perf_counter_ns()

    return int(time.perf_counter() * 1_000_000_000)


class SharedRecordingState:
    def __init__(self) -> None:
        self.start_event = threading.Event()
        self.stop_event = threading.Event()

        self.session_t0_ns = 0

        self.errors = queue.Queue()

    def report_error(self, source: str, message: str) -> None:
        self.errors.put((source, message))
        self.stop_event.set()

    def get_errors(self):
        errors = []

        while not self.errors.empty():
            errors.append(self.errors.get())

        return errors