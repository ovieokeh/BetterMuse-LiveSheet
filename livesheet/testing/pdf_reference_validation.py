"""Check authored fixture consistency before creating any reference PDF.

This validates source intent, independently of recognition and MIDI matching.
It does not alter source events, fill bars or infer missing time signatures.
"""

from fractions import Fraction as F


def validate_authored(spec):
    meter = (4, 4)
    offset = F(0)
    pending = {}
    for index, bar in enumerate(spec["measures"]):
        meter = bar.get("time", meter)
        if len(meter) != 2 or any(not isinstance(v, int) or v <= 0 for v in meter):
            raise ValueError("Invalid authored meter")
        budget = F(meter[0] * 4, meter[1])
        ends = []
        for voice_index, voice in enumerate(bar["voices"]):
            cursor = F(0)
            for event in voice["events"]:
                pitches, onset, duration = event[:3]
                onset, duration = F(onset), F(duration)
                if onset != cursor or duration <= 0:
                    raise ValueError(
                        f"Measure {index+1}: overlapping events or an undeclared rest"
                    )
                cursor = onset + duration
                if len(set(pitches)) != len(pitches):
                    raise ValueError("Duplicate pitch in authored chord")
                ties = set(event[3] if len(event) > 3 else [])
                if not ties <= {"start", "stop"} or ties and not pitches:
                    raise ValueError("Invalid authored tie")
                for pitch in pitches:
                    key = (voice["staff"], voice_index, pitch)
                    start = offset + onset
                    if "stop" in ties:
                        if pending.pop(key, None) != start:
                            raise ValueError(
                                "Authored tie continuation is missing or noncontiguous"
                            )
                    elif key in pending:
                        raise ValueError("Authored tie reattacks without a stop")
                    if "start" in ties:
                        pending[key] = start + duration
            ends.append(cursor)
            pickup = bar.get("implicit", False) and index == 0
            if not (0 < cursor <= budget if pickup else cursor == budget):
                raise ValueError(
                    f"Measure {index+1}: voice fills {cursor} beats under {meter[0]}/{meter[1]}"
                )
        if not ends or len(set(ends)) != 1:
            raise ValueError("Authored staff durations disagree")
        offset += ends[0]
    if pending:
        raise ValueError("Unclosed authored tie")
