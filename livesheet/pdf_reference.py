"""Independent, rational-beat reference reader and written-event comparison.

Never imports the production OMR adapter. Voice IDs are arbitrary and are not
scored. Measures align by printed order, not content: omissions remain visible.
This does not score performed repeat order, expression, rests or page geometry.
"""
from collections import Counter
from fractions import Fraction
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile, is_zipfile


def read_xml(path):
    path = Path(path)
    if is_zipfile(path):
        with ZipFile(path) as archive:
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            files = [n.get('full-path') for n in container.iter()
                     if n.tag.split('}')[-1] == 'rootfile']
            if not files:
                raise ValueError('Missing MusicXML rootfile')
            root = ET.fromstring(archive.read(files[0]))
    else:
        root = ET.parse(path).getroot()
    for node in root.iter():
        node.tag = node.tag.split('}')[-1]
    if root.tag != 'score-partwise':
        raise ValueError('Reference reader requires score-partwise MusicXML')
    return root


def read_score(path):
    root = read_xml(path)
    result = dict(schemaVersion=1, parts=[], unsupported=[])
    for pi, part in enumerate(root.findall('part')):
        measures = []
        divisions = None
        transpose = Fraction(0)
        for mi, measure in enumerate(part.findall('measure')):
            cursor = Fraction(0)
            end = Fraction(0)
            chord_start = None
            notes = []
            for node in measure:
                if node.tag == 'attributes':
                    if node.find('divisions') is not None:
                        divisions = Fraction(node.findtext('divisions'))
                        if divisions <= 0:
                            raise ValueError('Nonpositive divisions')
                    if node.find('transpose') is not None:
                        transpose = Fraction(node.findtext('transpose/chromatic', '0')) + 12 * int(node.findtext('transpose/octave-change', '0'))
                        if node.find('transpose/double') is not None:
                            result['unsupported'].append(f'part {pi} measure {mi}: doubled transposition')
                elif node.tag in ('backup', 'forward'):
                    if divisions is None:
                        raise ValueError('Duration before divisions')
                    cursor += Fraction(node.findtext('duration')) / divisions * (1 if node.tag == 'forward' else -1)
                    if cursor < 0:
                        raise ValueError(f'Negative measure cursor: part {pi}, measure {mi}')
                    end = max(end, cursor)
                    chord_start = None
                elif node.tag == 'note':
                    grace = node.find('grace') is not None
                    if not grace and (divisions is None or node.find('duration') is None):
                        raise ValueError('Timed note missing divisions or duration')
                    duration = Fraction(0) if grace else Fraction(node.findtext('duration')) / divisions
                    if duration < 0:
                        raise ValueError('Negative duration')
                    chord = node.find('chord') is not None
                    if chord and chord_start is None:
                        raise ValueError('Chord without preceding note')
                    onset = chord_start if chord else cursor
                    if not chord:
                        chord_start = onset
                        cursor += duration
                    end = max(end, onset + duration)
                    pitch = node.find('pitch')
                    if pitch is not None:
                        if node.get('print-object') == 'no':
                            result['unsupported'].append(f'part {pi} measure {mi}: hidden pitched note')
                        midi = 12 * (int(pitch.findtext('octave')) + 1) + dict(C=0,D=2,E=4,F=5,G=7,A=9,B=11)[pitch.findtext('step')] + Fraction(pitch.findtext('alter', '0'))
                        notes.append(dict(staff=int(node.findtext('staff', '1')), midi=str(midi),
                                          onset=str(onset), duration=str(duration), grace=grace,
                                          ties=sorted({t.get('type') for t in node.findall('tie')}),
                                          voice=node.findtext('voice', '1')))
                        if transpose:
                            result['unsupported'].append(f'part {pi} measure {mi}: transposing instrument')
                    elif node.find('unpitched') is not None:
                        result['unsupported'].append(f'part {pi} measure {mi}: unpitched note')
            bars = []
            for bar in measure.findall('barline'):
                for mark in bar:
                    if mark.tag in ('repeat', 'ending'):
                        bars.append(dict(kind=mark.tag, location=bar.get('location', 'right'),
                                         **{k:v for k,v in mark.attrib.items() if k in ('direction','times','number','type')}))
            measures.append(dict(index=mi, label=measure.get('number'), implicit=measure.get('implicit') == 'yes',
                                 extent=str(end), notes=notes, repeatMarks=bars))
        result['parts'].append(dict(id=part.get('id'), measures=measures))
    if not result['parts']:
        raise ValueError('No parts')
    result['unsupported'] = sorted(set(result['unsupported']))
    return result


FIELDS = {
    'exactWrittenEvents': ('staff','midi','onset','duration','grace','ties'),
    'pitchAndOnset': ('staff','midi','onset','grace'),
    'rhythmIgnoringPitch': ('staff','onset','duration','grace'),
}


def signature(note, fields=FIELDS['exactWrittenEvents']):
    def value(k):
        v = note.get(k, [] if k == 'ties' else False if k == 'grace' else 1 if k == 'staff' else None)
        if k in ('midi','onset','duration'):
            return str(Fraction(str(v)))
        return tuple(sorted(v)) if k == 'ties' else v
    return tuple(value(k) for k in fields)


def counts(expected, actual):
    matched = sum((expected & actual).values())
    wanted, found = sum(expected.values()), sum(actual.values())
    return dict(expected=wanted, recognized=found, matched=matched,
                missingOrChanged=wanted-matched, extraOrChanged=found-matched,
                precision=matched/found if found else None,
                recall=matched/wanted if wanted else None)


def compare(reference, candidate):
    if reference.get('unsupported') or candidate.get('unsupported'):
        raise ValueError('Unsupported notation in reference or candidate; do not silently exclude it')
    totals = {key:(Counter(), Counter()) for key in FIELDS}
    rows = []
    exact_measures = 0
    for pi in range(max(len(reference['parts']), len(candidate['parts']))):
        expected = reference['parts'][pi]['measures'] if pi < len(reference['parts']) else []
        actual = candidate['parts'][pi]['measures'] if pi < len(candidate['parts']) else []
        for mi in range(max(len(expected), len(actual))):
            a = expected[mi] if mi < len(expected) else None
            b = actual[mi] if mi < len(actual) else None
            values = {}
            for key, fields in FIELDS.items():
                x = Counter(signature(n, fields) for n in (a or {}).get('notes', []))
                y = Counter(signature(n, fields) for n in (b or {}).get('notes', []))
                values[key] = counts(x,y)
                totals[key][0].update({(pi,mi,k):v for k,v in x.items()})
                totals[key][1].update({(pi,mi,k):v for k,v in y.items()})
            equal = a is not None and b is not None and values['exactWrittenEvents']['missingOrChanged'] == 0 and values['exactWrittenEvents']['extraOrChanged'] == 0
            exact_measures += equal
            repeats_match = a is not None and b is not None and a.get('repeatMarks', []) == b.get('repeatMarks', [])
            extent_match = a is not None and b is not None and Fraction(a['extent']) == Fraction(b['extent'])
            if not equal or not repeats_match or not extent_match:
                rows.append(dict(partIndex=pi, measureIndex=mi, referenceLabel=(a or {}).get('label'),
                                 candidateLabel=(b or {}).get('label'), missingMeasure=b is None,
                                 extraMeasure=a is None, metrics=values, extentMatches=extent_match,
                                 repeatMarksMatch=repeats_match))
    return dict(metrics={k:counts(*v) for k,v in totals.items()}, exactWrittenMeasures=exact_measures,
                expectedPartMeasures=sum(len(p['measures']) for p in reference['parts']),
                recognizedPartMeasures=sum(len(p['measures']) for p in candidate['parts']),
                differences=rows,
                scope='Written pitched events; part and measure ordinal alignment. Voice labels ignored. '
                      'Projection metrics overlap and do not identify unique error causes. Repeat marks and '
                      'measure extents are separate diagnostics. No performed-order, expression, rest or geometry accuracy claim.')
