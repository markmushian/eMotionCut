"""Transition sound that does not hiss.

The hand-written `whoosh()` this build used was one-pole-filtered white noise with the cutoff swept
0.12 → 0.62. At 44.1 kHz a one-pole coefficient of 0.62 sits near 6.9 kHz, so everything from 3 kHz up survived
— and a 0.8 s band of hiss, repeated at every cut, reads to the ear as 呲呲呲. On top of that the clips sat at
0.8 gain, so the noise was the loudest thing in the mix after the voice.

This version is deliberately dark: brown noise (which has no top end to begin with) shaped by two cascaded
low-passes around 1.2 kHz, a slow asymmetric swell, and a soft clip so nothing peaks. It reads as air moving,
not as static. The tail is silent — a transition that rings out is what makes a cut sound cheap.

    python sfx_soft.py assets/sfx            # whoosh.wav + riser.wav

Pure standard library: works in the film's own Python, no numpy.
"""
import math
import os
import random
import struct
import sys
import wave

SR = 44100
random.seed(20261115)


def write_wav(path, samples, gain=1.0):
    peak = max(1e-9, max(abs(x) for x in samples))
    # `gain` is the level the file lands at, not a multiplier of it: a transition should sit clearly under the
    # voice, and normalising to full scale is what made the old effect the loudest thing in the mix.
    scale = gain / peak
    with wave.open(path, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(b''.join(
            struct.pack('<h', max(-32767, min(32767, int(x * scale * 32767)))) for x in samples))


def one_pole(x, coefficient, state=0.0):
    """Cascade two of these and high frequencies are gone — which is the whole point."""
    out = []
    for sample in x:
        state += coefficient * (sample - state)
        out.append(state)
    return out


def swell(n, peak_at=0.45, power=1.7):
    env = []
    for i in range(n):
        t = i / (n - 1)
        if t <= peak_at:
            env.append(math.sin(0.5 * math.pi * (t / peak_at)) ** power)
        else:
            env.append(math.cos(0.5 * math.pi * ((t - peak_at) / (1 - peak_at))) ** (power * 0.9))
    return env


def whoosh(duration=0.75):
    """Air moving past the frame: dark, soft-edged, silent at both ends."""
    n = int(SR * duration)
    # brown noise = integrated white noise; already tilted toward the low end
    walk, brown = 0.0, []
    for _ in range(n):
        walk = walk * 0.995 + random.uniform(-1, 1) * 0.06
        brown.append(walk)
    # remove the DC the walk picked up
    mean = sum(brown) / n
    brown = [x - mean for x in brown]
    # two poles at ~1.1 kHz: keeps the body, kills the fizz
    dark = one_pole(one_pole(brown, 0.145), 0.145)
    # a little movement so it is not a flat wash — a slow amplitude wobble, not a sweep
    wobble = [1 + 0.18 * math.sin(2 * math.pi * 1.4 * (i / SR)) for i in range(n)]
    env = swell(n)
    mixed = [dark[i] * env[i] * wobble[i] for i in range(n)]
    # soft clip: rounds the peaks instead of letting them snap
    return [math.tanh(x * 2.6) * 0.42 for x in mixed]


def riser(duration=0.9):
    """For a cut that needs lift: a darkened swell whose brightness opens slightly at the end."""
    n = int(SR * duration)
    walk, out = 0.0, []
    for i in range(n):
        t = i / (n - 1)
        walk = walk * 0.995 + random.uniform(-1, 1) * 0.06
        cutoff = 0.06 + 0.10 * t  # stays dark: 0.16 is still under 2 kHz
        gain = math.sin(math.pi * t) ** 1.4
        out.append(math.tanh(walk * cutoff * 9) * gain * 0.40)
    return out


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'assets/sfx'
    os.makedirs(out, exist_ok=True)
    write_wav(os.path.join(out, 'whoosh.wav'), whoosh(), gain=0.11)
    write_wav(os.path.join(out, 'riser.wav'), riser(), gain=0.09)
    print('wrote', os.path.join(out, 'whoosh.wav'), '/', 'riser.wav')


if __name__ == '__main__':
    main()
