import unittest

from macroflow.ui.animation import UiAnimator, animator_for, ease_back, ease_out


class FakeRoot:
    def __init__(self):
        self.now = 0.0
        self.jobs = []
        self.focused = True
        self.window_state = "normal"
        self.bindings = {}

    def bind_all(self, event, callback, add=None):
        self.bindings[event] = callback

    def bind(self, event, callback, add=None):
        self.bindings[event] = callback

    def after(self, delay, callback):
        self.jobs.append((delay, callback))
        return callback

    def after_cancel(self, job):
        self.jobs = [entry for entry in self.jobs if entry[1] is not job]

    def state(self):
        return self.window_state

    def focus_displayof(self):
        return self if self.focused else None

    def _root(self):
        return self

    def frame(self, seconds):
        self.now += seconds
        delay, callback = self.jobs.pop(0)
        assert delay == 16
        callback()


class AnimationTests(unittest.TestCase):
    def test_only_one_frame_loop_serves_multiple_animations(self):
        root = FakeRoot()
        animator = UiAnimator(root, clock=lambda: root.now)
        seen = []
        animator.animate("one", 160, lambda value: seen.append(("one", value)))
        animator.animate("two", 160, lambda value: seen.append(("two", value)))
        self.assertEqual(len(root.jobs), 1)
        root.frame(0.16)
        self.assertEqual(len(seen), 2)
        self.assertEqual(root.jobs, [])

    def test_focus_and_minimize_pause_then_resume_without_skipping(self):
        root = FakeRoot()
        animator = UiAnimator(root, clock=lambda: root.now)
        seen = []
        animator.animate("one", 160, seen.append)
        root.focused = False
        animator._pause_if_unfocused()
        self.assertEqual(root.jobs, [])
        root.now += 5
        root.focused = True
        animator._resume()
        root.frame(0.08)
        self.assertLess(seen[-1], 1)
        root.window_state = "iconic"
        animator._pause_if_unmapped(type("Event", (), {"widget": root})())
        self.assertEqual(root.jobs, [])
        root.window_state = "normal"
        animator._resume()
        root.frame(0.08)
        self.assertEqual(seen[-1], 1)

    def test_bad_callback_does_not_stop_other_animation(self):
        root = FakeRoot()
        animator = UiAnimator(root, clock=lambda: root.now)
        seen = []
        animator.animate("bad", 160, lambda _value: 1 / 0)
        animator.animate("good", 160, seen.append)
        root.frame(0.08)
        self.assertEqual(len(root.jobs), 1)
        root.frame(0.08)
        self.assertEqual(seen[-1], 1)

    def test_singleton_and_easing_curves(self):
        root = FakeRoot()
        self.assertIs(animator_for(root), animator_for(root))
        self.assertEqual(ease_out(0), 0)
        self.assertEqual(ease_out(1), 1)
        self.assertEqual(ease_back(1), 1)
        self.assertGreater(ease_back(0.8), 1)
