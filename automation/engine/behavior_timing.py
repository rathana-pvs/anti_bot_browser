"""Bounded timing and interruptible waits; no browser-specific decisions."""
import time


class EntryInterrupted(RuntimeError):
    def __init__(self, message, reason='input_ownership_lost'):
        super().__init__(message)
        self.reason = reason


class InteractionWait:
    def __init__(self, guard=lambda: True, clock=time.monotonic, sleep=time.sleep, progress=None, surface_guard=None):
        self.guard, self.clock, self.sleep, self.progress = guard, clock, sleep, progress
        self.surface_guard = surface_guard
        self._last_progress = clock()

    def check(self):
        if not self.guard():
            raise EntryInterrupted('Input ownership lost or execution cancelled')
        if self.progress and self.clock() - self._last_progress >= 5:
            self.progress()
            self._last_progress = self.clock()

    def checkpoint(self):
        self.check()
        if self.surface_guard is not None and not self.surface_guard():
            raise EntryInterrupted("Composer is no longer confirmed", 'composer_unconfirmed')
        # OCR can take seconds. Ownership must still hold after that read.
        self.check()

    def pause(self, seconds):
        end = self.clock() + max(0, seconds)
        self.check()
        while self.clock() < end:
            self.sleep(min(0.1, max(0, end - self.clock())))
            self.check()


class BehaviorTiming:
    def __init__(self, behavior):
        self.behavior = behavior

    def sample(self, low, typical, high, channel='pause'):
        value = self.behavior.stream(channel).triangular(low, high, typical)
        return min(high, max(low, value * self.behavior.pace))

    def review(self, text, image_count=0):
        base = min(12, max(2, 1.5 + len(text.split()) * .08 + image_count * .7))
        return min(12, max(2, base * self.behavior.stream('review').uniform(.9, 1.1)))
