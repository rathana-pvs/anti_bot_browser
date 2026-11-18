"""Opt-in sampler that shows what keeps the backend's event loop busy.

Create ``data/debug_loop_profile`` and restart the app; while it runs, the busiest
event-loop stacks are written to ``data/loop_profile.txt`` every 15 seconds.
Delete the flag file and restart to turn it off. Only code locations are recorded.
"""
from collections import Counter
from pathlib import Path
import sys
import threading
import time

INTERVAL_S = 0.01
WRITE_EVERY_S = 15
DEPTH = 12
# The loop waiting for I/O: idle, not work.
IDLE_FRAMES = {('selectors.py', 'select'), ('base_events.py', '_run_once_select')}


def _stack(frame):
    entries = []
    while frame is not None and len(entries) < 60:
        code = frame.f_code
        entries.append((Path(code.co_filename).name, code.co_name, frame.f_lineno, code.co_filename))
        frame = frame.f_back
    return entries


def _label(entries):
    if entries and (entries[0][0], entries[0][1]) in IDLE_FRAMES:
        return None
    # Innermost frames first; prefer the project's own frames so the cause is readable.
    own = [entry for entry in entries if '/backend/' in entry[3] or '/automation/' in entry[3]]
    chosen = (entries[:4] + [entry for entry in own if entry not in entries[:4]])[:DEPTH]
    return ' <- '.join(f'{name}:{line} {func}' for name, func, line, _ in chosen)


def start(flag: Path, output: Path) -> bool:
    if not flag.exists():
        return False
    loop_thread = threading.get_ident()
    counts: Counter = Counter()
    totals = {'samples': 0, 'busy': 0}
    started = time.monotonic()

    def write():
        samples = max(totals['samples'], 1)
        lines = [f'{time.strftime("%H:%M:%S")}  {time.monotonic() - started:.0f}s sampled, '
                 f'event loop busy {totals["busy"] * 100 / samples:.0f}% of the time', '']
        for label, count in counts.most_common(30):
            lines.append(f'{count * 100 / samples:5.1f}%  {label}')
        output.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def run():
        last_write = time.monotonic()
        while True:
            time.sleep(INTERVAL_S)
            frame = sys._current_frames().get(loop_thread)
            if frame is None:
                return
            totals['samples'] += 1
            label = _label(_stack(frame))
            if label:
                totals['busy'] += 1
                counts[label] += 1
            if time.monotonic() - last_write >= WRITE_EVERY_S:
                last_write = time.monotonic()
                try:
                    write()
                except OSError:
                    pass

    threading.Thread(target=run, name='loop-profiler', daemon=True).start()
    return True
