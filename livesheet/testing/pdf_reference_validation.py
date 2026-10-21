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


def xml_meter_issues(path):
    """Check generated MusicXML bar lengths independently of recognition.

    This is deliberately limited to the generated corpus's single, whole-bar
    meters. Unknown or unsupported meters block verification, rather than being
    silently treated as valid. Chords, rests and voices use the independent
    reference reader's rational measure extents.
    """
    from livesheet.pdf_reference import read_score, read_xml

    score = read_score(path)
    root = read_xml(path)
    issues = []
    for pi, part in enumerate(root.findall('part')):
        budget = None
        for mi, bar in enumerate(part.findall('measure')):
            times = bar.findall('attributes/time')
            if len(times) > 1:
                raise ValueError('Multiple meters in reference measure')
            if times:
                time = times[0]
                if (time.get('number') is not None or len(time.findall('beats')) != 1
                        or len(time.findall('beat-type')) != 1):
                    raise ValueError('Unsupported reference meter')
                beats, unit = int(time.findtext('beats')), int(time.findtext('beat-type'))
                if beats <= 0 or unit <= 0:
                    raise ValueError('Nonpositive reference meter')
                budget = F(beats * 4, unit)
                # A time change after notes would need separate segment budgets.
                seen_duration = False
                for node in bar:
                    if node.tag in ('note', 'forward', 'backup'):
                        seen_duration = True
                    if node.tag == 'attributes' and node.find('time') is not None and seen_duration:
                        raise ValueError('Mid-measure reference meter change')
            if budget is None:
                raise ValueError('Missing reference meter')
            extent = F(score['parts'][pi]['measures'][mi]['extent'])
            partial = bar.get('implicit') == 'yes'
            if not (0 < extent <= budget if partial else extent == budget):
                issues.append(dict(part=pi + 1, measure=mi + 1,
                                   beats=str(extent), budget=str(budget)))
    return issues


def classify_references(entries, corpus):
    """Keep explicitly documented invalid originals out of accuracy totals.

    Their immutable hashes and exact defects must still match. Any new invalid
    reference, stale exception, or invalid replacement blocks the benchmark.
    Private listening references have no authored MusicXML and remain reviewed
    event regressions, not claims of authored-source validity.
    """
    import hashlib
    import json

    policy_path = corpus / 'reference-diagnostics.json'
    policy = json.loads(policy_path.read_text()) if policy_path.exists() else {}
    by_id = {entry['id']: entry for entry in entries}
    if not set(policy) <= set(by_id):
        raise ValueError('Diagnostic policy names an unknown reference')
    checks = {}
    for entry in entries:
        ident = entry['id']
        path = corpus / ident / 'source.musicxml'
        if entry['family'] == 'private-reviewed':
            checks[ident] = dict(kind='reviewed-events', issues=[])
            continue
        issues = xml_meter_issues(path)
        if issues and ident not in policy:
            raise ValueError(f'Invalid reference {ident}: {issues}')
        if ident in policy:
            rule = policy[ident]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if not issues or issues != rule['issues'] or digest != rule['sourceSHA256']:
                raise ValueError(f'Stale diagnostic policy: {ident}')
            outcome = rule['expectedOutcome']
            if outcome not in ('historical-comparison', 'overfull-rejection'):
                raise ValueError(f'Unknown diagnostic outcome: {ident}')
            checks[ident] = dict(kind='invalid-source-diagnostic', issues=issues,
                                replacement=rule['replacement'], expectedOutcome=outcome)
        else:
            checks[ident] = dict(kind='authored-meter-checked', issues=[])
    for ident in policy:
        replacement = checks.get(policy[ident]['replacement'])
        if replacement is None or replacement['kind'] != 'authored-meter-checked':
            raise ValueError(f'Diagnostic needs a valid replacement: {ident}')
    return checks


def verification_summary(rows):
    """Invalid-source diagnostics can never count as successful playback tests."""
    from collections import Counter

    scored = [r for r in rows if r['reference']['kind'] != 'invalid-source-diagnostic']
    diagnostics = [r for r in rows if r not in scored]
    diagnostics_passed = all(r['diagnosticCheckPassed'] for r in diagnostics)
    return dict(passed=bool(scored) and all(r['passed'] for r in scored) and diagnostics_passed,
                counts=dict(Counter('passed' if r['passed'] else 'failed' for r in scored)),
                diagnosticCount=len(diagnostics), diagnosticChecksPassed=diagnostics_passed)


def diagnostic_outcome(reference, comparison_passed, job):
    """Require the documented result; unrelated worker failures must stay red."""
    import json

    if reference['expectedOutcome'] == 'historical-comparison':
        return comparison_passed is True
    if comparison_passed or (job / 'completion.json').exists():
        return False
    try:
        failure = json.loads((job / 'failure.json').read_text())
        quality = json.loads((job / 'quality.json').read_text())
    except (OSError, ValueError):
        return False
    return (failure.get('error') == 'repair_validation_failed'
            and quality.get('status') == 'failed'
            and quality.get('reason') == 'ValueError: repair-introduced-overfull-measure')
