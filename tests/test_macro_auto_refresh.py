import threading
import unittest

from app.macro_auto_refresh import MacroAutoRefresh


class MacroAutoRefreshTests(unittest.TestCase):
    def test_run_once_calls_refresh_callback(self):
        calls = []
        watcher = MacroAutoRefresh(lambda: calls.append("refreshed"), interval_seconds=300)

        watcher.run_once()

        self.assertEqual(calls, ["refreshed"])

    def test_start_runs_callback_after_interval_and_stop_ends_worker(self):
        calls = []
        ready = threading.Event()

        def refresh():
            calls.append("refreshed")
            ready.set()

        watcher = MacroAutoRefresh(refresh, interval_seconds=0.01)
        watcher.start()
        self.assertTrue(ready.wait(1))
        watcher.stop()

        self.assertGreaterEqual(len(calls), 1)
        self.assertFalse(watcher.is_running)

