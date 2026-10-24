"""Reproduce the app's original synthesized news beds; no samples or downloads."""
from pathlib import Path
import math
import random
import struct
import wave

RATE = 24000
SECONDS = 20
NEW_TRACKS = {
    'urgent_wire': (132, (73.416, 65.406, 87.307, 65.406)),
    'alert_desk': (124, (82.407, 82.407, 97.999, 73.416)),
    'live_update': (116, (130.813, 164.814, 97.999, 146.832)),
    'developing_story': (96, (55, 65.406, 58.27, 73.416)),
    'major_headline': (108, (65.406, 87.307, 97.999, 73.416)),
    'washington_watch': (92, (110, 97.999, 130.813, 82.407)),
}


def synth(kind):
    rng = random.Random(20261021)
    bpm = {'breaking': 118, 'headlines': 104, 'politics': 88}[kind]
    beat = 60 / bpm
    roots = {'breaking': [73.416, 73.416, 87.307, 65.406],
             'headlines': [130.813, 97.999, 110, 87.307],
             'politics': [73.416, 65.406, 82.407, 55]}[kind]
    samples = bytearray()
    for i in range(RATE * SECONDS):
        t = i / RATE
        root = roots[int(t / (beat * 4)) % len(roots)]
        phase = t % beat
        eighth = t % (beat / 2)
        pad = sum(math.sin(math.tau * root * ratio * t) for ratio in (1, 1.5, 2)) * .055
        bass = math.sin(math.tau * root / 2 * t) * math.exp(-phase * 8) * .23
        kick_phase = t % (beat * (2 if kind == 'politics' else 1))
        kick = math.sin(math.tau * (48 * kick_phase + 12 * (1 - math.exp(-kick_phase * 25)))) * math.exp(-kick_phase * 24) * .24
        hat = rng.uniform(-1, 1) * math.exp(-eighth * 130) * (.018 if kind == 'politics' else .035)
        note = [1, 1.5, 2, 1.25, 1.5, 1, 1.25, 1.5][int(t / (beat / 2)) % 8]
        pulse = math.sin(math.tau * root * 4 * note * t) * math.exp(-eighth * 13) * (.028 if kind == 'politics' else .055)
        fade = min(1, t / .35, (SECONDS - t) / .7)
        value = math.tanh((pad + bass + kick + hat + pulse) * 1.3) * fade * .8
        samples.extend(struct.pack('<h', round(value * 32767)))
    return samples


def synth_new(kind):
    """Distinct original 30-second arrangements; preserve the legacy synth above."""
    bpm, roots = NEW_TRACKS[kind]
    rng = random.Random('news-bed-v1-' + kind)
    beat = 60 / bpm
    samples = bytearray()
    for i in range(RATE * 30):
        t = i / RATE
        root = roots[int(t / (beat * 4)) % 4]
        step = int(t / (beat / 2))
        eighth = t % (beat / 2)
        phase = t % beat
        bar = t % (beat * 4)
        noise = rng.uniform(-1, 1)
        bass = math.sin(math.tau * root / 2 * t) * math.exp(-phase * 9) * .19
        kick_phase = t % (beat * (2 if kind in ('developing_story', 'washington_watch') else 1))
        kick = math.sin(math.tau * (46 * kick_phase + 2 * (1 - math.exp(-kick_phase * 30)))) * math.exp(-kick_phase * 27) * .22
        pad = sum(math.sin(math.tau * root * ratio * t) for ratio in (1, 1.5, 2)) * .038
        hat = noise * math.exp(-eighth * 160) * .025
        note = (1, 1.5, 2, 1.5, 1.25, 1.5, 2, 1)[step % 8]
        tone = math.tau * root * note * t
        if kind == 'urgent_wire':
            lead = (math.sin(tone * 4) + .3 * math.sin(tone * 8)) * math.exp(-eighth * 23) * .065
            percussion = hat + noise * math.exp(-(t % (beat * 2)) * 38) * .06
        elif kind == 'alert_desk':
            # Rounded harmonic stabs, offset against the kick.
            stab = (t + beat / 2) % beat
            lead = sum(math.sin(tone * h) / h for h in (2, 4, 6)) * math.exp(-stab * 12) * .12
            percussion = hat + noise * math.exp(-((t + beat) % (beat * 2)) * 42) * .09
        elif kind == 'live_update':
            lead = (math.sin(tone * 4) + .25 * math.sin(tone * 6)) * math.exp(-eighth * 18) * .055
            percussion = hat * 1.4
            pad *= .55
        elif kind == 'developing_story':
            swell = .35 + .65 * min(1, t / 22)
            lead = math.sin(tone * 2) * math.exp(-phase * 6) * .055
            pad *= swell * (1 + .15 * math.sin(math.tau * t / (beat * 8)))
            percussion = hat * .4 + noise * math.exp(-bar * 12) * .07
        elif kind == 'major_headline':
            lead = sum(math.sin(math.tau * root * ratio * t) for ratio in (2, 3, 4)) * math.exp(-(t % (beat * 2)) * 2) * .045
            percussion = hat + noise * math.exp(-((t + beat) % (beat * 2)) * 35) * .08
            pad *= 1.4
        else:  # Washington watch: sparse, decaying piano-like harmonics.
            lead = (math.sin(tone * 2) + .35 * math.sin(tone * 4) + .12 * math.sin(tone * 6)) * math.exp(-phase * 7) * .07
            percussion = hat * .3
            bass *= .7
            kick *= .65
        fade = min(1, t / .25, (30 - t) / .8)
        value = math.tanh((bass + kick + pad + lead + percussion) * 1.3) * fade * .8
        samples.extend(struct.pack('<h', round(value * 32767)))
    return samples


def main():
    destination = Path(__file__).resolve().parents[1] / 'backend/services/reel_creator/audio'
    destination.mkdir(exist_ok=True)
    for kind in ('breaking', 'headlines', 'politics', *NEW_TRACKS):
        with wave.open(str(destination / f'{kind}-v1.wav'), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(synth_new(kind) if kind in NEW_TRACKS else synth(kind))


if __name__ == '__main__':
    main()
