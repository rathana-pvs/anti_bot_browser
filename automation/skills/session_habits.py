"""Natural-session habits around a publish job.

Only used when the account's natural session switch is on. Staying after a
publish is passive: pointer moves, scrolling and reading pauses. It never
clicks, reacts, comments or follows, and it never runs after an uncertain or
failed result.
"""

import time

from engine.passive_browsing import PassiveBrowsing

MAX_LINGER_SECONDS = 60.0


class SessionHabits:
    def __init__(self, task):
        self.task = task

    @property
    def session(self):
        return getattr(self.task, "behavior_session", None)

    def wants_to_linger(self) -> bool:
        session = self.session
        return bool(
            session is not None
            and getattr(session, "natural", False)
            and getattr(self.task, "result_status", None) == "published"
            # Under resource pressure the machine needs this container back.
            and self._machine_scale() > 0.5
        )

    def _machine_scale(self) -> float:
        signal = getattr(self.task, "resource_signal", None)
        return signal()["elastic_scale"] if callable(signal) else 1.0

    def linger(self) -> dict:
        """Stay on the page for a short, bounded time after publishing."""
        task = self.task
        session = self.session
        if not session.chance(session.persona.linger_rate):
            return {"lingered": False, "reason": "not_chosen"}
        budget = min(MAX_LINGER_SECONDS, session.session_seconds(*session.persona.linger_seconds))
        # A busy machine shortens the stay.
        budget *= self._machine_scale()
        started = time.monotonic()
        deadline = started + budget
        width, height = task.client.get_screen_dimensions()
        browsing = PassiveBrowsing(session, width, height)
        scrolls = 0
        task.log("INFO", f"Natural session: staying on the page for up to {budget:.0f}s after publishing.")
        while time.monotonic() < deadline:
            step = browsing.next_step()
            if step is None:
                break
            target = browsing.pointer_target(task.client.get_mouse_position(), first=scrolls == 0)
            if target is not None:
                task.human.move_to(*target)
            if time.monotonic() >= deadline:
                break
            task.human.scroll(step.direction, notches=step.notches)
            scrolls += 1
            # The page is not re-read here; treat every scroll as a visible change.
            browsing.record(step, True)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(remaining, step.pause_seconds))
        # Return to where the post was, in short bursts, within a small cap.
        return_deadline = time.monotonic() + 6.0
        while browsing.net_down > 0 and time.monotonic() < return_deadline:
            notches = min(browsing.net_down, 3)
            task.human.scroll("up", notches=notches)
            browsing.net_down -= notches
        seconds = round(time.monotonic() - started, 2)
        telemetry = getattr(task, "telemetry", None)
        if telemetry is not None:
            telemetry.record_step("linger", seconds * 1000.0, outcome="completed", scrolls=scrolls)
        return {"lingered": True, "linger_seconds": seconds, "linger_scrolls": scrolls}
