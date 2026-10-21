#!/usr/bin/env python3
"""Read-only recognition audit. Disagreement is evidence to review, not accuracy.

Compare cached Audiveris mappings and an optional manually checked excerpt.
Never changes a recognition result or selects a winner automatically.
"""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile
import pymupdf


def source_evidence(source):
    # Legacy MuseScore 2 MScore encoding, inspected against this printed score.
    # Other fonts/encodings are deliberately not guessed.
    heads = {'\ue12b': 'whole', '\ue12c': 'half', '\ue12d': 'filled'}
    pages = []
    with pymupdf.open(source) as pdf:
        for page in pdf:
            counts = Counter()
            fonts = set()
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        fonts.add(span['font'])
                        if span['font'] == 'MScore':
                            counts.update(heads[c['c']] for c in span['chars'] if c['c'] in heads)
            pages.append(dict(page=page.number+1, imageObjects=len(page.get_images()),
                              vectorPaths=len(page.get_drawings()), fonts=sorted(fonts),
                              legacyMScoreNoteheadGlyphs=dict(counts)))
        return dict(sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    creator=pdf.metadata.get('creator'), pages=pages)


def xml_root(path):
    with ZipFile(path) as archive:
        container = ET.fromstring(archive.read('META-INF/container.xml'))
        name = next(e.get('full-path') for e in container.iter() if e.tag.split('}')[-1] == 'rootfile')
        return ET.fromstring(archive.read(name))


def meter_budgets(paths):
    budgets = []
    for path in paths:
        root = xml_root(Path(path))
        parts = root.findall('part')
        meters = [None] * len(parts)
        for measures in zip(*(part.findall('measure') for part in parts)):
            for i, measure in enumerate(measures):
                time = measure.find('attributes/time')
                if time is not None:
                    beats, types = time.findall('beats'), time.findall('beat-type')
                    meters[i] = (sum(Fraction(sum(int(v) for v in b.text.split('+')) * 4, int(t.text))
                                     for b, t in zip(beats, types)) if beats and len(beats) == len(types) else None)
            # Polymeter needs separate part checks; do not pretend it is one meter.
            budgets.append(float(meters[0]) if meters and meters[0] is not None and all(m == meters[0] for m in meters) else None)
    return budgets


def signature(note):
    return (note.get('part', 0), note.get('staff', 1), note['midi'], round(note['onset'], 6),
            round(note['duration'], 6), tuple(sorted(note.get('ties', []))))


def profile(path, truth=None):
    mapping = json.loads(path.read_text())
    meters = meter_budgets(mapping['musicxmlFiles'])
    if len(meters) != len(mapping['measures']): raise ValueError('measure-count mismatch')
    notes = defaultdict(list)
    for note in mapping['notes']: notes[note['measureIndex']].append(note)
    overfull = []
    for i, (measure, budget) in enumerate(zip(mapping['measures'], meters)):
        if budget is not None and measure['duration'] > budget + .001:
            overfull.append(dict(index=i, label=measure['number'], page=measure['page'],
                                 expectedBeats=budget, recognizedBeats=measure['duration']))
    checked = []
    for expected in (truth or {}).get('measures', []):
        actual = Counter(signature(n) for n in notes[expected['index']])
        wanted = Counter(signature(n) for n in expected['notes'])
        checked.append(dict(index=expected['index'], exact=actual == wanted,
                            missingOrChanged=sum((wanted-actual).values()),
                            extraOrChanged=sum((actual-wanted).values())))
    summary = mapping['summary']
    return dict(path=str(path), measureCount=len(mapping['measures']),
                writtenNotes=len(mapping['notes']), detectedHeads=summary['omrHeads'],
                unusedHeads=summary['unusedOmrHeads'], unmappedNotes=summary['unmatchedXmlNotes'],
                overfullMeasures=overfull, checkedExcerpt=checked), notes


def audit(source, mappings, truth=None):
    evidence = source_evidence(source)
    if truth and truth['sourceSHA256'] != evidence['sha256']: raise ValueError('reference belongs to another PDF')
    results, groups = [], []
    for path in mappings:
        result, notes = profile(path, truth)
        results.append(result); groups.append(notes)
    comparisons = []
    for i in range(1, len(groups)):
        differing = []
        for measure in sorted(set(groups[0]) | set(groups[i])):
            a = Counter(signature(n) for n in groups[0][measure])
            b = Counter(signature(n) for n in groups[i][measure])
            if a != b: differing.append(measure)
        comparisons.append(dict(left=0, right=i, differentMeasureIndices=differing))
    return dict(source=evidence, profiles=results, comparisons=comparisons,
                interpretation='Diagnostics only. Matching outputs and notehead counts do not prove musical accuracy.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('mappings', type=Path, nargs='+')
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source, args.mappings, json.loads(args.reference.read_text()) if args.reference else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(args.output)

if __name__ == '__main__': main()
