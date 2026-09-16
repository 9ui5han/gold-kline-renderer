"""Background refresher for official macro event feeds."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable


logger = logging.getLogger(__name__)


class MacroAutoRefresh:
    """Run a refresh callback in a single stoppable daemon thread."""

    def __init__(self, refresh_callback: Callable[[], object], *, interval_seconds: float = 300) -> None:
        self.refresh_callback = refresh_callback
        self.interval_seconds = max(0.01, float(interval_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def run_once(self) -> None:
        try:
            self.refresh_callback()
        except Exception:
            logger.warning("Automatic macro refresh failed", exc_info=True)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            self.run_once()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="macro-auto-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
