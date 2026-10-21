#!/usr/bin/env python3
"""Package existing, validated PDF performances for offline native playtesting.

No recognition, network requests, or changes to the website snapshot. The MIDI
export is checked against the saved performance before its tempo map is used.
"""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import xml.etree.ElementTree as ET

import pymupdf
from livesheet.playback import midi_timeline, beat_at
from livesheet.recognition import validate_package

ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n').encode()


def staff_regions(mapping):
    """Optional visual bands from recognized staff lines, never page halves.

    Only the same single piano part supported by native hand selection is safe.
    Bound each band by neighbouring staves; page titles and inter-system space
    stay outside the wash. Cross-staff note conflicts are rejected by the app.
    """
    if any(n.get('part', 0) != 0 or n.get('staff') not in (1, 2) for n in mapping['notes']):
        return []
    result = []
    for page in mapping['pages']:
        width, height = page['width'], page['height']
        bands = []
        for system in page['systems']:
            if len(system['staves']) != 2:
                return []
            for staff, stave in enumerate(system['staves']):
                lines = stave['lines']
                if len(lines) != 5 or any(len(line) < 2 for line in lines):
                    return []
                xs = [x for line in lines for x, _ in line]
                ys = [y for line in lines for _, y in line]
                top, bottom = min(ys), max(ys)
                space = (bottom - top) / 4
                if space <= 0:
                    return []
                heads = [n['box'] for n in mapping['notes'] if n.get('box')
                         and n.get('page') == page['number'] and str(n.get('system')) == str(system['id'])
                         and n.get('staff') == staff + 1]
                ink_top = min([top] + [b[1] for b in heads])
                ink_bottom = max([bottom] + [b[1] + b[3] for b in heads])
                bands.append(dict(system=str(system['id']), staff=staff, left=min(xs), right=max(xs),
                                  top=top, bottom=bottom, ink_top=ink_top, ink_bottom=ink_bottom, space=space))
        bands.sort(key=lambda b: b['top'])
        def separator(upper, lower):
            middle = (upper['bottom'] + lower['top']) / 2
            if upper['ink_bottom'] < lower['ink_top'] and not upper['ink_bottom'] < middle < lower['ink_top']:
                return (upper['ink_bottom'] + lower['ink_top']) / 2
            return middle
        for i, band in enumerate(bands):
            top = max(0, min(band['top'] - 3 * band['space'], band['ink_top'] - band['space']))
            bottom = min(height, max(band['bottom'] + 3 * band['space'], band['ink_bottom'] + band['space']))
            if i:
                top = max(top, separator(bands[i-1], band))
            if i + 1 < len(bands):
                bottom = min(bottom, separator(band, bands[i+1]))
            left = max(0, band['left'] - band['space'])
            right = min(width, band['right'] + band['space'])
            if bottom <= top or right <= left:
                return []
            result.append(dict(page=page['number']-1, system=band['system'], staff=band['staff'],
                               box=[left/width, top/height, (right-left)/width, (bottom-top)/height],
                               staffBox=[band['left']/width, band['top']/height,
                                         (band['right']-band['left'])/width, (band['bottom']-band['top'])/height]))
    return result


def notation_analysis(score):
    """Preserve written chords/tie continuations, without expanding repeats.

    Match the app's difficulty inputs: one anchor per written note or chord,
    ordered by onset, staff and voice. MIDI would lose spelling and tied notes.
    Unknown mode is left unknown; fifths alone cannot distinguish major/minor.
    """
    result = []
    for part_index, part in enumerate(score.findall('part')):
        divisions = 1
        key = None
        for index, measure in enumerate(part.findall('measure')):
            if len(result) <= index:
                result.append({'keys': [], 'anchors': []})
            onset = 0
            previous = None
            groups = []
            for child in measure:
                if child.tag == 'attributes':
                    divisions = int(child.findtext('divisions', str(divisions)))
                    assert divisions > 0
                    signature = child.find('key')
                    if signature is not None:
                        fifths = signature.findtext('fifths')
                        key = {'fifths': int(fifths)} if fifths is not None else None
                        if key is not None:
                            assert -7 <= key['fifths'] <= 7
                            mode = signature.findtext('mode')
                            if mode in ('major', 'minor'):
                                key['mode'] = mode
                elif child.tag in ('backup', 'forward'):
                    onset += (1 if child.tag == 'forward' else -1) * int(child.findtext('duration', '0')) / divisions
                elif child.tag == 'note':
                    duration = int(child.findtext('duration', '0')) / divisions
                    chord = child.find('chord') is not None
                    if not chord:
                        previous = {'onset': onset, 'order': len(groups), 'pitchLabels': [], 'midis': [],
                                    'staffs': [], 'voice': int(child.findtext('voice', '1'))}
                        groups.append(previous)
                    pitch = child.find('pitch')
                    if (pitch is not None and child.get('print-object') != 'no'
                            and child.find('cue') is None and previous is not None):
                        step = pitch.findtext('step')
                        alter = int(pitch.findtext('alter', '0'))
                        octave = int(pitch.findtext('octave'))
                        assert step in 'CDEFGAB' and -2 <= alter <= 2
                        accidental = {-2: 'bb', -1: 'b', 0: '', 1: '#', 2: 'x'}[alter]
                        previous['pitchLabels'].append(f'{step}{accidental}{octave}')
                        previous['midis'].append(12 * (octave + 1) + dict(C=0,D=2,E=4,F=5,G=7,A=9,B=11)[step] + alter)
                        previous['staffs'].append(part_index * 32 + int(child.findtext('staff', '1')))
                    if not chord:
                        onset += duration
            result[index]['keys'].append(key)
            result[index]['anchors'].extend(g for g in groups if g['pitchLabels'])
    for measure in result:
        keys = measure.pop('keys')
        measure['key'] = keys[0] if keys and all(k == keys[0] for k in keys) else None
        groups = sorted(measure.pop('anchors'), key=lambda a: (a['onset'], min(a['staffs']), a['voice'], a['order']))
        measure['analysisAnchors'] = [dict(firstMidi=g['midis'][0], pitchLabels=g['pitchLabels'],
                                           staffs=sorted(set(g['staffs']))) for g in groups]
    return result


def arpeggiated_chords(score):
    """Keep explicitly marked XML chord groups, including unmarked member notes."""
    result = []
    for measure in score.findall('part/measure'):
        groups = []
        for note in measure.findall('note'):
            if note.find('chord') is None or not groups:
                groups.append([])
            groups[-1].append(note)
        result.extend({n.get('id') for n in group if n.get('id')}
                      for group in groups if any(n.find('notations/arpeggiate') is not None for n in group))
    return result


def rolled_note_source(note, onset, source_notes, chords, anchored_ids, used_ids):
    """Recover only a unique delayed member of an explicitly rolled, anchored chord.

    Stay within its written duration and before the next onset in that voice.
    Never reuse a written note within the same measure visit.
    """
    candidates = []
    for chord in chords:
        if not chord & anchored_ids:
            continue
        for ident in chord - used_ids:
            source = source_notes.get(ident)
            if source is None or source['measureIndex'] != int(note['measureId'][1:]) or source['midi'] != note['midi']:
                continue
            later = [n['onset'] for n in source_notes.values()
                     if n['measureIndex'] == source['measureIndex'] and n['part'] == source['part']
                     and n['voice'] == source['voice'] and n['onset'] > source['onset']]
            end = min([source['onset'] + source['duration']] + later)
            if source['onset'] <= onset < end:
                candidates.append(source)
    return candidates[0] if len(candidates) == 1 else None


def enrich(ident, performance, midi_notes, tempos, recognition_root=None):
    if recognition_root is None:
        raise ValueError('recognition_root is required')
    cache = recognition_root
    mapping = None
    for path in sorted(cache.rglob('mapping.json')):
        candidate = json.loads(path.read_text())
        hashes = [digest(Path(p).read_bytes()) for p in candidate['musicxmlFiles']]
        if hashes == performance['provenance']['movementSourceSHA256']:
            mapping = candidate
            break
    assert mapping is not None, 'No recognition mapping matches the prepared performance'
    assert mapping['sha256'] == performance['sourceSHA256']
    source_notes = {n['id']: n for n in mapping['notes']}
    meters = []
    analysis = []
    notation = []
    rolled_chords = []
    # Preserve meter changes, including inherited signatures. Do not infer meter
    # from performance duration (pickups and fermatas need not fill a bar).
    for source in mapping['musicxmlFiles']:
        with zipfile.ZipFile(source) as archive:
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            path = next(n.attrib['full-path'] for n in container.iter() if n.tag.endswith('rootfile'))
            score = ET.fromstring(archive.read(path))
        offset = len(meters)
        groups = list(zip(*(p.findall('measure') for p in score.findall('part'))))
        for mi, group in enumerate(groups):
            note_number = 0
            for measure in group:
                for node in measure.findall('note'):
                    if node.find('pitch') is not None:
                        note_number += 1
                        node.set('id', f'm{offset + mi + 1}-n{note_number}')
        rolled_chords.extend(arpeggiated_chords(score))
        locations = []
        for n in mapping['notes']:
            if offset <= n['measureIndex'] < offset + len(groups):
                locations.append(dict(id=n['id'], measureID=f"m{n['measureIndex']}",
                    midi=int(n['midi']), staff=n['staff']-1, onset=float(n['onset']), page=n.get('page', 1)-1,
                    box=n.get('normalizedBox')))
        notation.append(dict(musicXML=base64.b64encode(ET.tostring(score, encoding='utf-8')).decode(),
                             measureIDs=[f'm{i}' for i in range(offset, offset + len(groups))],
                             notes=locations))
        analysis.extend(notation_analysis(score))
        part = score.find('part')
        meter = None
        for measure in part.findall('measure'):
            time = measure.find('attributes/time')
            if time is not None:
                try:
                    meter = {'beats': sum(int(v) for v in time.findtext('beats').split('+')),
                             'beatType': int(time.findtext('beat-type'))}
                except (ValueError, TypeError, AttributeError):
                    meter = None
            meters.append(meter)
    assert len(meters) == len(analysis) == len(mapping['measures'])
    notes = []
    used_by_visit = {}
    for note, raw in zip(performance['notes'], midi_notes):
        visit_index, visit = next((i, m) for i, m in enumerate(performance['measures'])
                                 if m['id'] == note['measureId'] and m['start'] <= note['start'] + .002 < m['end'] + .002)
        used = used_by_visit.setdefault(visit_index, set())
        source = source_notes.get(note.get('sourceNoteId'))
        if source is None:
            # Missing page geometry must not discard an unambiguous XML staff.
            onset = raw['beat'] - beat_at(visit['start'], tempos)
            anchors = {n.get('sourceNoteId') for n in performance['notes']
                       if n['measureId'] == visit['id'] and visit['start'] <= n['start'] < visit['end']}
            reserved = used | anchors
            source = rolled_note_source(note, onset, source_notes, rolled_chords, anchors, reserved)
            if source is None:
                candidates = [n for n in mapping['notes'] if n['measureIndex'] == int(note['measureId'][1:])
                              and n['midi'] == note['midi'] and n['id'] not in reserved
                              and abs(n['onset'] - onset) < .12]
                if len(candidates) == 1:
                    source = candidates[0]
        item = {**note, 'beat': raw['beat'],
                'durationBeats': beat_at(note['start'] + note['duration'], tempos) - raw['beat']}
        if source:
            used.add(source['id'])
            if not item.get('sourceNoteId'):
                item['sourceNoteId'] = source['id']
                if source.get('normalizedBox'):
                    item.update(box=source['normalizedBox'], page=source['page'] - 1)
            item.update(staff=source['staff'] - 1, voice=str(source['voice']), part=source['part'],
                        notatedDurationBeats=source['duration'])
        notes.append(item)
    visits = []
    for i, measure in enumerate(performance['measures']):
        item = {**measure, 'visitID': f'visit-{i}', 'startBeat': beat_at(measure['start'], tempos),
                'endBeat': beat_at(measure['end'], tempos)}
        meter = meters[int(measure['id'][1:])]
        if meter: item['meter'] = meter
        metadata = analysis[int(measure['id'][1:])]
        if metadata['key'] is not None: item['key'] = metadata['key']
        item['analysisAnchors'] = metadata['analysisAnchors']
        visits.append(item)
    return notes, visits, notation, staff_regions(mapping)


def prepare(ident, entry, source, performance, midi, recognition_root=None, performance_data=None):
    validate_package(performance, entry['pageCount'])
    assert performance['format'] == 'pdf'
    assert performance['sourceSHA256'] == digest(source.read_bytes())
    notes, duration, tempos = performance_data if performance_data is not None else midi_timeline(midi)
    assert abs(duration-performance['duration']) < .002
    assert len(notes) == len(performance['notes'])
    for actual, expected in zip(notes, performance['notes']):
        for key in ['midi', 'velocity', 'start', 'duration']:
            assert abs(actual[key]-expected[key]) < .002, (ident, key)
    # Keep the last tempo event at each timestamp (MIDI often overrides its default at 0).
    tempo_map = {round(t, 6): {'seconds': round(t, 6), 'beat': b, 'bpm': 60000000/us}
                 for t, b, us in tempos}
    with pymupdf.open(source) as pdf:
        pages = [{'width': page.rect.width, 'height': page.rect.height,
                  'rotation': page.rotation} for page in pdf]
    enriched_notes, visits, notation, regions = enrich(ident, performance, notes, tempos, recognition_root)
    body = dict(schemaVersion=2, format='bettermuse.pdf-playback', id=ident,
                sourceSHA256=performance['sourceSHA256'], sourceRevision=performance['revision'],
                coordinateSpace='displayed-crop-top-left', duration=duration, pages=pages,
                notes=enriched_notes, notation=notation, staffRegions=regions,
                suggestedTempo=None,
                measures=visits, cursors=performance['cursors'], tempoMap=list(tempo_map.values()),
                issues=performance['issues'], musicalReview='unreviewed',
                provenance=performance.get('provenance', {}))
    body['revision'] = digest(encode(body))
    return body

