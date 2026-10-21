"""Independent MIDI reading and strict, absolute-time playback comparison.

No recognition or production playback parser is imported. Never fit tempo,
transpose pitches, deduplicate attacks or align away missing measures.
"""
from collections import Counter, defaultdict, deque
from pathlib import Path
import math
import xml.etree.ElementTree as E
import mido


def read_midi(path):
    midi=mido.MidiFile(path); ticks=0; seconds=0.; us=500000
    active=defaultdict(deque); held=defaultdict(list); pedal=defaultdict(bool)
    notes=[]; tempos=[dict(beat=0.,seconds=0.,bpm=120.)]; controls=[]
    def finish(n):
        notes.append({**n,'duration':seconds-n['start']})
    for event in mido.merge_tracks(midi.tracks):
        seconds+=event.time/midi.ticks_per_beat*us/1_000_000
        ticks+=event.time
        if event.type=='set_tempo':
            us=event.tempo
            mark=dict(beat=ticks/midi.ticks_per_beat,seconds=seconds,bpm=60_000_000/us)
            if abs(tempos[-1]['beat']-mark['beat'])<1e-9:tempos[-1]=mark
            elif abs(tempos[-1]['bpm']-mark['bpm'])>1e-6:tempos.append(mark)
        elif event.type=='note_on' and event.velocity>0:
            active[event.channel,event.note].append(dict(midi=event.note,start=seconds,
                beat=ticks/midi.ticks_per_beat,velocity=event.velocity))
        elif event.type=='note_off' or event.type=='note_on' and event.velocity==0:
            queue=active[event.channel,event.note]
            if not queue:raise ValueError('Unmatched MIDI note release')
            n=queue.popleft()
            if pedal[event.channel]:held[event.channel].append(n)
            else:finish(n)
        elif event.type=='control_change' and event.control==64:
            controls.append(dict(seconds=seconds,value=event.value,channel=event.channel))
            pedal[event.channel]=event.value>=64
            if not pedal[event.channel]:
                for n in held.pop(event.channel,[]):finish(n)
    if any(active.values()) or any(held.values()):raise ValueError('Unreleased reference notes or pedal')
    return dict(notes=sorted(notes,key=lambda n:(n['start'],n['midi'])),tempos=tempos,pedal=controls,
                duration=max(seconds,max((n['start']+n['duration'] for n in notes),default=0)))


def read_reference(midi, positions):
    result=read_midi(midi)
    tree=E.parse(positions)
    result['measures']=[dict(id='m'+e.get('elid'),start=float(e.get('position'))/1000)
                        for e in tree.findall('events/event')]
    return result


def compare(expected, actual, onset_tolerance=.035, duration_tolerance=.05, velocity_tolerance=4):
    """Compare complete performances, preserving duplicate notes and repeat visits."""
    for score in (expected,actual):
        for n in score['notes']:
            if not all(math.isfinite(float(n[k])) for k in ('midi','start','duration','velocity')):
                raise ValueError('Nonfinite playback event')
    available=set(range(len(actual['notes'])));missing=[];changed=[];matched=0
    for index,n in enumerate(expected['notes']):
        candidates=[i for i in available if actual['notes'][i]['midi']==n['midi'] and
                    abs(actual['notes'][i]['start']-n['start'])<=onset_tolerance]
        if not candidates:
            missing.append(dict(index=index,midi=n['midi'],start=n['start']));continue
        # Match duplicate unisons by closest duration after onset, never collapse them.
        i=min(candidates,key=lambda i:(abs(actual['notes'][i]['start']-n['start']),
                                      abs(actual['notes'][i]['duration']-n['duration'])))
        available.remove(i);a=actual['notes'][i];matched+=1
        errors={k:dict(expected=n[k],actual=a[k]) for k,tol in [('duration',duration_tolerance),('velocity',velocity_tolerance)] if abs(a[k]-n[k])>tol}
        if errors:changed.append(dict(index=index,midi=n['midi'],start=n['start'],errors=errors))
    visits_a=expected.get('measures',[]);visits_b=actual.get('measures',[])
    route=[m['id'] for m in visits_a]==[m['id'] for m in visits_b]
    timing=route and all(abs(a['start']-b['start'])<=onset_tolerance for a,b in zip(visits_a,visits_b))
    tempo_errors=[]
    if 'tempos' in actual and 'tempos' in expected:
        def at(marks,beat):return next(m['bpm'] for m in reversed(marks) if m['beat']<=beat+1e-8)
        for beat in sorted({round(m['beat'],6) for s in (expected,actual) for m in s['tempos']}):
            a,b=at(expected['tempos'],beat),at(actual['tempos'],beat)
            if abs(a-b)>.1:tempo_errors.append(dict(beat=beat,expected=a,actual=b))
    end_error=abs(expected['duration']-actual['duration'])
    passed=not (missing or available or changed or tempo_errors) and route and timing and end_error<=duration_tolerance
    return dict(passed=passed,attackTimingAndOrderPass=not (missing or available) and route and timing,
                releaseAndVelocityPass=not changed,tempoPass=not tempo_errors,expectedAttacks=len(expected['notes']),actualAttacks=len(actual['notes']),matchedAttacks=matched,
                missingAttacks=missing,extraAttacks=[actual['notes'][i] for i in sorted(available)],changedEvents=changed,
                measureOrderMatches=route,measureTimingMatches=timing,tempoErrors=tempo_errors,endDifferenceSeconds=end_error,
                tolerances=dict(onsetSeconds=onset_tolerance,durationSeconds=duration_tolerance,velocity=velocity_tolerance))


def defects(row):
    """Stable failed assertions, keyed by reference event rather than total counts."""
    if row['status'] in ('failed','reference-blocked'):return {('preparation',)}
    r=row['result'];found=set()
    for n in r['missingAttacks']:found.add(('attack',n['index']))
    for n in r['changedEvents']:
        for field in n['errors']:found.add((field,n['index']))
    occurrences=Counter()
    for n in r['extraAttacks']:
        key=('extra',n['midi'],round(n['start'],3));occurrences[key]+=1
        found.add((*key,occurrences[key]))
    for field in ('measureOrderMatches','measureTimingMatches'):
        if not r[field]:found.add((field,))
    for t in r['tempoErrors']:found.add(('tempo',round(t['beat'],4)))
    if r['endDifferenceSeconds']>r['tolerances']['durationSeconds']:found.add(('end',))
    return found


def regressions(baseline,current):
    if baseline['benchmark']['referenceManifestSHA256']!=current['benchmark']['referenceManifestSHA256']:
        raise ValueError('Different reference manifests cannot establish a regression')
    if baseline['benchmark'].get('comparatorSHA256')!=current['benchmark'].get('comparatorSHA256'):
        raise ValueError('Comparator changed; re-evaluate retained baseline outputs before comparing')
    old={r['id']:r for r in baseline['rows']};new={r['id']:r for r in current['rows']}
    if old.keys()!=new.keys():raise ValueError('Regression cases changed or disappeared')
    findings=[]
    for ident,before in old.items():
        after=new[ident]
        if before['status'] in ('failed','reference-blocked'):
            # A formerly unmeasurable case is new evidence, not a previous pass.
            continue
        added=defects(after)-defects(before)
        if added:findings.append(dict(id=ident,newFailedAssertions=sorted(added,key=str)))
    return dict(passed=not findings,findings=findings,
                previouslyBlocked=[r['id'] for r in baseline['rows'] if r['status'] in ('failed','reference-blocked')],
                scope='No previously correct assertion may become wrong; improvements elsewhere cannot cancel losses. Full quality pass is separate.')
