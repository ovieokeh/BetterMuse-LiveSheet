"""Audiveris adapter: MusicXML events mapped to unchanged PDF page coordinates.

This measures agreement between recognition outputs, not musical accuracy.
Original PDFs, OMR archives and MusicXML exports are never modified.
"""
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import pymupdf as fitz

def fraction(value):
    return Fraction(value or '0')


def part_index(identifier, fallback):
    # Audiveris keeps logical part IDs even when other parts are absent in a system.
    match = re.fullmatch(r'P?(\d+)', identifier or '')
    return int(match[1])-1 if match else fallback


def rect(element):
    b = element.find('bounds')
    return [float(b.get(k)) for k in ('x', 'y', 'w', 'h')] if b is not None else None


def read_omr(path):
    pages, measures = [], []
    with ZipFile(path) as archive:
        names = sorted((n for n in archive.namelist() if re.fullmatch(r'sheet#\d+/sheet#\d+\.xml', n)),
                       key=lambda n: int(re.search(r'\d+', n)[0]))
        for name in names:
            page_number = int(re.search(r'\d+', name)[0])
            root = ET.fromstring(archive.read(name))
            picture = root.find('picture')
            scale = float(root.find('scale/interline').get('main'))
            page = dict(number=page_number, width=int(picture.get('width')), height=int(picture.get('height')), heads=[], systems=[])
            pages.append(page)
            for system in root.findall('page/system'):
                inters = {e.get('id'): e for e in system.findall('sig/inters/*')}
                contains = defaultdict(list)
                for relation in system.findall('sig/relations/relation'):
                    if relation.find('containment') is not None:
                        contains[relation.get('source')].append(relation.get('target'))
                parts = system.findall('part')
                staves = [staff for part in parts for staff in part.findall('staff')]
                page['systems'].append(dict(id=system.get('id'), staves=[dict(id=s.get('id'), lines=[[[float(p.get('x')),float(p.get('y'))] for p in line.findall('point')] for line in s.findall('lines/line')]) for s in staves]))
                for h in inters.values():
                    if h.tag == 'head' and rect(h):
                        page['heads'].append(dict(id=h.get('id'), box=rect(h), grade=float(h.get('ctx-grade', h.get('grade', 0))), mapped=False))
                heads_by_id = {h['id']: h for h in page['heads']}
                # Cautionary signatures at a system end reuse the preceding stack ID.
                # They are layout reminders, not additional performed measures.
                for stack in system.findall('stack'):
                    if stack.get('special') == 'CAUTIONARY':
                        continue
                    slots = {s.get('id'): fraction(s.get('time-offset'))*4 for s in stack.findall('slot')}
                    events = []
                    for local_index, part in enumerate(parts):
                        staff_numbers = {s.get('id'): i+1 for i, s in enumerate(part.findall('staff'))}
                        measure_id = ('X' if stack.get('special') == 'SECOND_HALF' else '') + stack.get('id')
                        measure = next((m for m in part.findall('measure') if m.get('id') == measure_id), None)
                        if measure is None:
                            raise ValueError('recognized part is missing a measure')
                        for voice in measure.findall('voice'):
                            for entry in voice.findall('slots/entry'):
                                val = entry.find('value')
                                if val.get('status') != 'BEGIN' or entry.findtext('key') not in slots:
                                    continue
                                for hid in contains[val.get('chord')]:
                                    head = inters[hid]
                                    if head.tag != 'head' or hid not in heads_by_id:
                                        continue
                                    events.append(dict(part=part_index(part.get('id'), local_index), head=heads_by_id[hid], onset=slots[entry.findtext('key')], voice=voice.get('id'), staff=staff_numbers.get(head.get('staff')), staffPitch=float(head.get('pitch', 0)), chord=val.get('chord')))
                    measures.append(dict(page=page_number, system=system.get('id'), stack=stack.get('id'), left=float(stack.get('left')), right=float(stack.get('right')), scale=scale, events=events, abnormal=stack.get('abnormal') == 'true'))
    return pages, measures


def read_mxl(path):
    with ZipFile(path) as archive:
        container = ET.fromstring(archive.read('META-INF/container.xml'))
        rootfile = next(e.get('full-path') for e in container.iter() if e.tag.split('}')[-1] == 'rootfile')
        root = ET.fromstring(archive.read(rootfile))
    parts = root.findall('part')
    if not parts or len({len(p.findall('measure')) for p in parts}) != 1:
        raise ValueError('MusicXML parts have different measure counts')
    divisions = [1] * len(parts)
    quarter_start, events, measures = Fraction(0), [], []
    for mi, group in enumerate(zip(*(p.findall('measure') for p in parts))):
        if len({m.get('number') for m in group}) != 1:
            raise ValueError('MusicXML parts disagree on measure numbering')
        measure_events, measure_end = [], Fraction(0)
        for pi, measure in enumerate(group):
            cursor, last_start, end = Fraction(0), Fraction(0), Fraction(0)
            for node in measure:
                if node.tag == 'attributes' and node.find('divisions') is not None:
                    divisions[pi] = int(node.findtext('divisions'))
                    if divisions[pi] <= 0:
                        raise ValueError(f'Invalid MusicXML divisions in measure {measure.get("number")}: {divisions[pi]}')
                if node.tag in ('backup', 'forward'):
                    cursor += (1 if node.tag == 'forward' else -1)*fraction(node.findtext('duration'))/divisions[pi]
                if node.tag != 'note':
                    continue
                duration = fraction(node.findtext('duration'))/divisions[pi]
                start = last_start if node.find('chord') is not None else cursor
                if node.find('chord') is None:
                    last_start = start
                    cursor += duration
                end = max(end, start+duration)
                pitch = node.find('pitch')
                if pitch is None:
                    continue
                step, octave, alter = pitch.findtext('step'), int(pitch.findtext('octave')), float(pitch.findtext('alter', '0'))
                midi = (octave+1)*12 + dict(C=0,D=2,E=4,F=5,G=7,A=9,B=11)[step]+alter
                event = dict(id=f'm{mi+1}-n{len(measure_events)+1}', part=part_index(parts[pi].get('id'), pi), measure=measure.get('number'), measureIndex=mi, staff=int(node.findtext('staff','1')), voice=node.findtext('voice','1'), pitch=f'{step}{ {0:"",1:"♯",-1:"♭",2:"𝄪",-2:"𝄫"}.get(alter,str(alter))}{octave}', midi=midi, diatonic=octave*7+'CDEFGAB'.index(step), onset=start, duration=float(duration), start=float(quarter_start+start), x=float(node.get('default-x')) if node.get('default-x') else None, grace=node.find('grace') is not None, ties=[t.get('type') for t in node.findall('tie')])
                measure_events.append(event)
                events.append(event)
            measure_end = max(measure_end, end)
        measures.append(dict(number=group[0].get('number'), events=measure_events, start=float(quarter_start), duration=float(measure_end)))
        quarter_start += measure_end
    return measures, events


def build_score(base, record):
    sid = record['id']
    pages, omr_measures = read_omr(base/f'{sid}.omr')
    xml_measures, events, quarter_offset = [], [], 0.
    for source in record.get('musicxmlFiles', [str(base/f'{sid}.mxl')]):
        movement_measures, movement_events = read_mxl(Path(source))
        offset = len(xml_measures)
        movement_duration = max((m['start']+m['duration'] for m in movement_measures),default=0)
        for measure in movement_measures:
            measure['start'] += quarter_offset
        for event in movement_events:
            event['measureIndex'] += offset
            event['start'] += quarter_offset
            event['id'] = f"m{event['measureIndex']+1}-" + event['id'].split('-',1)[1]
        xml_measures.extend(movement_measures)
        events.extend(movement_events)
        quarter_offset += movement_duration
    if len(omr_measures) != len(xml_measures):
        raise ValueError(f'{sid}: measure count mismatch: OMR {len(omr_measures)}, XML {len(xml_measures)}')
    problems, residuals, counts = [], [], defaultdict(int)
    for om, xm in zip(omr_measures, xml_measures):
        # Match rhythm/voice/staff first. Order in a chord resolves its vertical heads.
        og, xg = defaultdict(list), defaultdict(list)
        for e in om['events']:
            og[(e['part'], e['voice'], e['staff'], e['onset'])].append(e)
        for e in xm['events']:
            xg[(e['part'], e['voice'], e['staff'], e['onset'])].append(e)
        for key, xs in xg.items():
            hs = og.get(key, [])
            if len(xs) != len(hs):
                problems.append(dict(measure=xm['number'],reason='group-count',xml=len(xs),omr=len(hs),group=str(key)))
                continue
            xs = sorted(xs, key=lambda e:e['diatonic'], reverse=True)
            hs = sorted(hs, key=lambda e:e['staffPitch'])
            for x, h in zip(xs, hs):
                box = h['head']['box']
                # Audiveris writes integer MusicXML tenths from left-of-head pixels.
                expected = om['left'] + x['x']*om['scale']/10 if x['x'] is not None else None
                residual = abs(box[0]-expected) if expected is not None else None
                if residual is None or residual > om['scale']*0.4:
                    problems.append(dict(measure=xm['number'], event=x['id'], reason='x-mismatch', residual=residual))
                    continue
                h['head']['mapped'] = True
                page = pages[om['page']-1]
                x.update(page=om['page'], system=om['system'], omrHeadID=h['head']['id'], omrChordID=h['chord'], box=box, normalizedBox=[box[0]/page['width'],box[1]/page['height'],box[2]/page['width'],box[3]/page['height']], xResidualPixels=round(residual,3), recognitionGrade=h['head']['grade'], mapping='measure+voice+onset+staff+pitch-order+x-check')
                residuals.append(residual)
                counts[om['page']] += 1
        xm.update(page=om['page'], system=om['system'], left=om['left'], right=om['right'])
        xm.pop('events')
    doc = fitz.open(base/record['pdf'])
    if len(pages) != len(doc) or [p['number'] for p in pages] != list(range(1, len(doc)+1)):
        raise ValueError('recognition omitted PDF pages')
    for page in pages:
        original = doc[page['number']-1]
        # Existing PDF rasters share these normalized page coordinates.
        page.update(image=record['previews'][page['number']-1], pdfWidth=original.rect.width,
                    pdfHeight=original.rect.height, mappedNotes=counts[page['number']])
        if not page['systems']:
            raise ValueError(f"no recognized systems on PDF page {page['number']}")
    doc.close()
    for event in events:
        event['onset'] = float(event['onset'])
    mapped = [e for e in events if 'box' in e]
    warnings = sorted(set(re.findall(r'\['+re.escape(sid)+r'(?:#\d+)?\].*?Measure\{#([^}]+)\}.*?too long', (base/'run.log').read_text())))
    summary = dict(id=sid, pages=len(pages), measures=len(xml_measures), xmlPitchedNotes=len(events), omrHeads=sum(len(p['heads']) for p in pages), mappedNotes=len(mapped), unmatchedXmlNotes=len(events)-len(mapped), unusedOmrHeads=sum(not h['mapped'] for p in pages for h in p['heads']), maxXResidualPixels=round(max(residuals,default=0),3), rhythmWarningMeasures=warnings, mappingProblems=problems)
    return dict(**record,pages=pages,measures=xml_measures,notes=events,summary=summary)
