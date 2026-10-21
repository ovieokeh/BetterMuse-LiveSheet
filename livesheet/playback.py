#!/usr/bin/env python3
"""Prepare local, engine-independent playback packages. Requires mido and Pillow.
MusicXML: MuseScore MIDI and position exports from the same import.
PDF: recognized MusicXML playback, with original-page geometry from the recognition adapter.
No network calls. No source files or recognition results are modified.
"""
import os
import bisect
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
import mido
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = Path('data/input')
TRIAL = Path('data/unused-trial')
CACHE = Path('data/performance')
PUBLIC = Path('data/output')
MSCORE = os.environ.get('PDF_MUSESCORE', '/usr/local/bin/mscore')
ENGINE_VERSION = None


def default_config():
    # CLI compatibility only. Uploads supply an explicit immutable configuration.
    from livesheet.pdf_pipeline.config import ExportConfig
    return ExportConfig(BUNDLE, CACHE, PUBLIC, TRIAL, MSCORE, ENGINE_VERSION)


def midi_timeline(path):
    midi = mido.MidiFile(path)
    tempo, seconds, ticks = 500000, 0., 0
    active, sustained = defaultdict(deque), defaultdict(list)
    pedal, notes, tempos = {}, [], [(0., 0., tempo)]
    def finish(note, end):
        note['duration'] = round(max(.01, end - note['start']), 6)
        notes.append(note)
    for msg in mido.merge_tracks(midi.tracks):
        seconds += mido.tick2second(msg.time, midi.ticks_per_beat, tempo)
        ticks += msg.time
        if msg.type == 'set_tempo':
            tempo = msg.tempo
            tempos.append((seconds, ticks / midi.ticks_per_beat, tempo))
        elif msg.type == 'note_on' and msg.velocity:
            active[(msg.channel, msg.note)].append({'midi': msg.note, 'velocity': msg.velocity,
                'start': round(seconds, 6), 'beat': ticks / midi.ticks_per_beat})
        elif msg.type == 'note_off' or (msg.type == 'note_on' and not msg.velocity):
            key = (msg.channel, msg.note)
            if active[key]:
                note = active[key].popleft()
                if pedal.get(msg.channel): sustained[msg.channel].append(note)
                else: finish(note, seconds)
        elif msg.type == 'control_change' and msg.control == 64:
            pedal[msg.channel] = msg.value >= 64
            if not pedal[msg.channel]:
                for note in sustained.pop(msg.channel, []): finish(note, seconds)
    for values in list(active.values()) + list(sustained.values()):
        for note in values: finish(note, seconds)
    notes.sort(key=lambda n: (n['start'], n['midi']))
    for i, n in enumerate(notes): n['id'] = f'n{i}'
    return notes, max(seconds, max((n['start'] + n['duration'] for n in notes), default=0)), tempos


def beat_at(seconds, tempos):
    i = bisect.bisect_right([x[0] for x in tempos], seconds + .001) - 1
    start, beat, tempo = tempos[max(0, i)]
    return beat + (seconds - start) * 1000000 / tempo


def positions(path):
    tree = ET.parse(path)
    elements = {int(e.get('id')): {'page': int(e.get('page')), 'rawBox': [float(e.get(k)) for k in ['x','y','sx','sy']]} for e in tree.findall('elements/element')}
    events = [{'element': int(e.get('elid')), 'start': float(e.get('position')) / 1000} for e in tree.findall('events/event')]
    return elements, events


def image_page(source, dest, public_url, *, config=None):
    config = config or default_config()
    with Image.open(source) as image:
        pixels = image.convert('RGB')
        existing = config.public / public_url.removeprefix('/playback/')
        reused = False
        if existing.exists():
            try:
                with Image.open(existing) as previous:
                    if previous.size == pixels.size and ImageChops.difference(pixels, previous.convert('RGB')).getbbox() is None:
                        if existing != dest:
                            shutil.copy2(existing, dest)
                        reused = True
            except (OSError, ValueError):
                pass
        if not reused:
            pixels.save(dest, 'WEBP', lossless=True, method=4)
        return {'url': public_url, 'width': image.width, 'height': image.height}



def written_notes_source(source, target):
    """Exclude chord-symbol accompaniment, not written notes, from PDF export.

    MuseScore realizes MusicXML harmony as an extra MIDI track. OMR chord
    labels may be misread and can sustain to the end of the score. Keep the
    recognition original intact; only the disposable playback input changes.
    """
    if source.suffix.lower() == '.mxl':
        with ZipFile(source) as archive:
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            name = next(e.get('full-path') for e in container.iter() if e.tag.split('}')[-1] == 'rootfile')
            root = ET.fromstring(archive.read(name))
    else:
        root = ET.parse(source).getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag.split('}')[-1] == 'harmony': parent.remove(child)
    ET.ElementTree(root).write(target, encoding='utf-8', xml_declaration=True)
    return target


def export_performance(source, cache, pdf, *, config=None):
    config = config or default_config()
    cache.mkdir(parents=True, exist_ok=True)
    formats = ['mid','mpos'] + ([] if pdf else ['spos','png'])
    expected = [cache / f'score.{ext}' for ext in formats if ext != 'png']
    fingerprint = hashlib.sha256(source.read_bytes() + (('web-score-v3-written-notes-' if pdf else 'web-score-v2-144dpi-') + config.engine_version).encode()).hexdigest()
    stamp = cache / 'fingerprint.txt'
    if not stamp.exists() or stamp.read_text() != fingerprint or not all(p.exists() for p in expected):
        export_source = written_notes_source(source, cache / 'written-notes.musicxml') if pdf else source
        job = [{'in':str(export_source), 'out':[str(cache / f'score.{ext}') for ext in formats]}]
        (cache / 'job.json').write_text(json.dumps(job))
        result = subprocess.run([config.musescore,'-F','-f','-r','144','-j',str(cache / 'job.json')], capture_output=True, text=True, timeout=180)
        (cache / 'conversion.log').write_text(result.stdout + result.stderr)
        if result.returncode or not all(p.exists() for p in expected):
            raise RuntimeError(f'Conversion failed: {source.name}')
        stamp.write_text(fingerprint)
    notes, duration, tempos = midi_timeline(cache / 'score.mid')
    elements, events = positions(cache / 'score.mpos')
    if {e['element'] for e in events} != set(elements):
        raise ValueError('incomplete-playback')
    return notes, duration, tempos, elements, events


def join_performances(performances):
    """Concatenate performed movements, keeping repeat evaluation independent."""
    notes, tempos, elements, events = [], [], {}, []
    seconds, beats = 0., 0.
    for part_notes, duration, part_tempos, part_elements, part_events in performances:
        offset = len(elements)
        if set(part_elements) != set(range(len(part_elements))):
            raise ValueError('non-contiguous movement measure IDs')
        notes.extend({**n, 'start':round(n['start']+seconds,6), 'beat':n['beat']+beats} for n in part_notes)
        tempos.extend((t+seconds,b+beats,tempo) for t,b,tempo in part_tempos)
        elements.update({i+offset:e for i,e in part_elements.items()})
        events.extend({'element':e['element']+offset,'start':round(e['start']+seconds,6)} for e in part_events)
        beats += beat_at(duration, part_tempos)
        seconds += duration
    for i,note in enumerate(notes):
        note['id'] = f'n{i}'
    return notes, seconds, tempos, elements, events


def save_positions(path, elements, events):
    root=ET.Element('score'); geometry=ET.SubElement(root,'elements'); timeline=ET.SubElement(root,'events')
    for ident,element in elements.items():
        ET.SubElement(geometry,'element',id=str(ident),page=str(element['page']),
                      **{k:str(v) for k,v in zip(['x','y','sx','sy'],element['rawBox'])})
    for event in events:
        ET.SubElement(timeline,'event',elid=str(event['element']),position=str(event['start']*1000))
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)

def prepare(entry, trial, enabled, recognition_root=None, output_root=None, *, config=None):
    config = config or default_config()
    recognition_root = recognition_root if recognition_root is not None else config.trial
    output_root = output_root if output_root is not None else config.public
    ident = entry['id']; out = output_root / ident; out.mkdir(parents=True, exist_ok=True)
    cache = config.cache / ident; cache.mkdir(parents=True, exist_ok=True)
    pdf = entry['format'] == 'pdf'
    source = recognition_root / f'{ident}.mxl' if pdf and enabled else config.bundle / entry['assetPath']
    assert hashlib.sha256((config.bundle / entry['assetPath']).read_bytes()).hexdigest() == entry['sha256'], 'Source asset changed'
    package = {'schemaVersion': 1, 'id': ident, 'sourceSHA256': entry['sha256'], 'format': entry['format'],
               'ready': enabled, 'pages': [], 'notes': [], 'measures': [], 'cursors': [], 'duration': 0, 'issues': []}
    if enabled:
        sources = [Path(p) for p in trial.get('musicxmlFiles', [str(source)])] if pdf else [source]
        performances = [export_performance(item, cache if len(sources)==1 else cache/f'movement-{i+1}', pdf, config=config) for i,item in enumerate(sources)]
        notes, duration, tempos, elements, events = join_performances(performances) if len(sources)>1 else performances[0]
        if len(sources)>1:
            save_positions(cache/'score.mpos',elements,events)
        source_hashes = [hashlib.sha256(item.read_bytes()).hexdigest() for item in sources]
        package.update(notes=notes, duration=round(duration, 6), provenance={'engine':config.engine_version,
                       'recognition':'Audiveris 5.11.0' if pdf else None,
                       'preparedFromSHA256':source_hashes[0] if len(sources)==1 else hashlib.sha256(''.join(source_hashes).encode()).hexdigest(),
                       'movementSourceSHA256':source_hashes})
    if pdf and enabled:
        assert trial['sha256'] == entry['sha256'], 'OMR mapping belongs to another source PDF'
        assert len(elements) == len(trial['measures']), 'Measure alignment changed'
        sources = [Path(p['image']) for p in trial['pages']]
    elif enabled:
        sources = sorted(cache.glob('score-*.png'), key=lambda p: int(p.stem.split('-')[-1]))
    else:
        sources = [config.bundle / p for p in entry['previews']]
    assert sources
    for i, image in enumerate(sources):
        package['pages'].append(image_page(image, out / f'page-{i+1}.webp', f'/playback/{ident}/page-{i+1}.webp', config=config))
    if enabled:
        for index, event in enumerate(events):
            elid = event['element']; element = elements[elid]
            if pdf:
                m = trial['measures'][elid]; p = trial['pages'][m['page'] - 1]
                system = next(s for s in p['systems'] if s['id'] == m['system'])
                ys = [point[1] for staff in system['staves'] for line in staff['lines'] for point in line]
                top=max(0,min(ys)-35); bottom=min(p['height'],max(ys)+35)
                box = [m['left']/p['width'], top/p['height'], (m['right']-m['left'])/p['width'], (bottom-top)/p['height']]
                page = m['page'] - 1
                label = m['number']
            else:
                page = element['page']; p = package['pages'][page]
                box = [v / (12 * (p['width'] if i % 2 == 0 else p['height'])) for i,v in enumerate(element['rawBox'])]
                label = str(elid + 1)
            package['measures'].append({'id':f'm{elid}', 'label':label, 'page':page, 'box':box,
                'start':event['start'], 'end':events[index+1]['start'] if index+1 < len(events) else duration})
        starts = [m['start'] for m in package['measures']]
        note_candidates = defaultdict(list)
        used_at_onset = defaultdict(set)
        if pdf:
            for source_note in trial['notes']:
                if source_note.get('normalizedBox'):
                    note_candidates[(source_note['measureIndex'], source_note['midi'])].append(source_note)
        for note in notes:
            m = package['measures'][max(0, bisect.bisect_right(starts, note['start']+.002)-1)]
            note.update(measureId=m['id'], page=m['page'])
            if pdf:
                mi = int(m['id'][1:]); measure = trial['measures'][mi]
                beat = note['beat'] - beat_at(m['start'], tempos)
                candidates = note_candidates[(mi, note['midi'])]
                used = used_at_onset[note['start']]
                match = min(candidates, key=lambda n: (abs(n['onset'] - beat), n['id'] in used), default=None)
                if match and abs(match['onset'] - beat) < .12:
                    note.update(box=match['normalizedBox'], sourceNoteId=match['id'])
                    used.add(match['id'])
            note.pop('beat', None)
        if pdf:
            package['issues'] = ['recognition-unreviewed']
            if trial.get('exportRecovery'):
                recovery = trial['exportRecovery']
                package['provenance']['exportRecovery'] = {
                    key: recovery[key] for key in ['engineSHA256','patchSHA256','inputOmrSHA256','omittedWedges']}
                if recovery['omittedWedges']:
                    package['issues'].append('unplaced-dynamics-omitted')
            if trial['summary']['rhythmWarningMeasures']: package['issues'].append('rhythm-warnings')
            if trial['summary']['unmatchedXmlNotes']: package['issues'].append('partial-note-mapping')
            if trial['summary']['unusedOmrHeads']: package['issues'].append('unassigned-noteheads')
            package['mapping'] = {'mappedPlaybackNotes':sum('box' in n for n in notes), 'totalPlaybackNotes':len(notes)}
            # A group of original noteheads supplies a narrow moving cursor.
            groups = defaultdict(list)
            measures_by_id = {m['id']:m for m in package['measures']}
            for note in notes:
                if 'box' in note:
                    groups[note['start']].append(note)
            for start, group in sorted(groups.items()):
                n = group[0]; m = measures_by_id[n['measureId']]
                x = min(n['box'][0] for n in group)
                package['cursors'].append({'start':start,'page':n['page'],'box':[x,m['box'][1],.004,m['box'][3]]})
        else:
            segs, timing = positions(cache / 'score.spos')
            for event in timing:
                seg=segs[event['element']]; p=package['pages'][seg['page']]
                package['cursors'].append({'start':event['start'], 'page':seg['page'], 'box':[v/(12*(p['width'] if i%2==0 else p['height'])) for i,v in enumerate(seg['rawBox'])]})
        package['reviewStatus'] = 'needs-review' if pdf else 'unreviewed'
        assert notes and package['measures']
        for m in package['measures']:
            x,y,w,h=m['box']; assert 0 <= x < 1 and 0 <= y < 1 and w>0 and h>0 and x+w<1.01 and y+h<1.01, (ident,m)
    package['revision'] = hashlib.sha256(json.dumps(package,sort_keys=True).encode()).hexdigest()[:16]
    temporary = out / 'score.json.tmp'
    temporary.write_text(json.dumps(package,separators=(',',':'))+'\n')
    temporary.replace(out / 'score.json')
    return {'url':f'/playback/{ident}/score.json', 'ready':enabled, 'pages':len(sources)}


