import threading
import time
import unittest

from macroflow.execution.player import MacroPlayer, PlaybackStopped


class PauseTests(unittest.TestCase):
    def test_pause_before_timeline_start_only_shifts_active_timeline(self):
        player = MacroPlayer()
        player.pause()
        player._pause_started_at = time.perf_counter() - 10
        player._timeline._base_wall_time = time.perf_counter() - 1
        before = player._timeline._base_wall_time
        player.resume()
        self.assertAlmostEqual(player._timeline._base_wall_time - before, 1, delta=0.1)

    def test_wait_freezes_until_resumed(self):
        player = MacroPlayer()
        done = threading.Event()
        player.pause()
        worker = threading.Thread(target=lambda: (player._wait(50), done.set()), daemon=True)
        worker.start()
        self.assertFalse(done.wait(0.15))
        player.resume()
        self.assertTrue(done.wait(1))
        worker.join(1)

    def test_stop_releases_paused_wait(self):
        player = MacroPlayer()
        stopped = threading.Event()
        player.pause()

        def wait():
            try:
                player._wait(1000)
            except PlaybackStopped:
                stopped.set()

        worker = threading.Thread(target=wait, daemon=True)
        worker.start()
        time.sleep(0.05)
        player.stop()
        self.assertTrue(stopped.wait(1))
        worker.join(1)
