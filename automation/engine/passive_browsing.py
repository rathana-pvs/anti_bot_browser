"""Bounded plans for passive feed browsing; does not dispatch clicks or input."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowseStep:
    direction: str
    notches: int
    pause_seconds: float
    reason: str


class PassiveBrowsing:
    def __init__(self, behavior, width, height):
        self.behavior = behavior
        self.rng = behavior.stream('warming')
        self.mouse_rng = behavior.stream('mouse')
        # Both supported surfaces have their scrollable content right of the
        # management/navigation rail. These bounds exclude Chrome and the footer.
        self.bounds = (int(width * .45), int(height * .40),
                       int(width * .78), int(height * .80))
        self.net_down = 0
        self.unchanged = 0
        self.last_direction = None
        self.recovery_used = False
        self.stopped_reason = None

    def pointer_target(self, current, *, first=False):
        left, top, right, bottom = self.bounds
        x, y = current
        outside = not (left <= x <= right and top <= y <= bottom)
        if outside or first:
            return (int(self.mouse_rng.triangular(left, right, (left + right) / 2)),
                    int(self.mouse_rng.triangular(top, bottom, (top + bottom) / 2)))
        # Resting is the normal state. Occasional short repositioning follows
        # the current point instead of jumping to a new random screen position.
        if self.mouse_rng.random() >= .30:
            return None
        dx = (right - left) * self.mouse_rng.triangular(-.20, .20, 0)
        dy = (bottom - top) * self.mouse_rng.triangular(-.20, .20, 0)
        return (round(min(right, max(left, x + dx))), round(min(bottom, max(top, y + dy))))

    def next_step(self):
        # In a natural session the account's persona sets scroll
        # size, how often it rereads, and reading speed.
        persona = self.behavior.persona if getattr(self.behavior, 'natural', False) else None
        direction, reason = 'down', 'browse'
        if self.unchanged >= 2:
            if self.recovery_used or self.net_down <= 0:
                self.stopped_reason = 'view_unchanged'
                return None
            direction, reason = 'up', 'check_scroll_boundary'
            self.recovery_used = True
            self.unchanged = 0
        elif self.net_down > 0 and self.last_direction == 'down' and self.rng.random() < (persona.reread_rate if persona else .15):
            direction, reason = 'up', 'reread'
        if direction == 'down':
            notches = self.rng.randint(*persona.scroll_notches) if persona else self.rng.choice((1, 2, 2, 3, 3, 4))
        else:
            notches = min(self.net_down, self.rng.randint(1, 2))
        reading = persona.reading_speed if persona else 1.0
        pause = min(8.0, max(1.0, self.rng.triangular(1.5, 6.0, 3.2) * self.behavior.pace * reading))
        return BrowseStep(direction, notches, pause, reason)

    def record(self, step, changed):
        self.net_down = max(0, self.net_down + (step.notches if step.direction == 'down' else -step.notches))
        self.last_direction = step.direction
        self.unchanged = 0 if changed else self.unchanged + 1
