"""Export deliberately wrong MusicXML and require the listening check to reject it."""
import json
from pathlib import Path
import xml.etree.ElementTree as E
from livesheet.testing.pdf_performance_reference import read_reference, compare
from livesheet.testing.prepare_pdf_reference_set import checked_run,MSCORE,save,sha

CASES=[('late-opening-tempo','opening-tempo'),('replayed-ties','tied-chords'),
       ('missing-fermata','fermata-release'),('missing-pedal','pedal-release'),
       ('missing-dynamics','articulation-dynamics')]


def run(base):
    manifest=json.loads((base/'manifest.json').read_text());entries={e['id']:e for e in manifest['entries']}
    out=base/'mutation-checks';out.mkdir(exist_ok=True);jobs=[];references={}
    for name,family in CASES:
        entry=entries[family+'--leland'];folder=base/entry['path']
        for f,digest in entry['hashes'].items():
            if sha(folder/f)!=digest:raise ValueError('Reference changed')
        if not entry['referenceValid']:raise ValueError('Invalid reference')
        references[name]=folder
        tree=E.parse(folder/'source.musicxml');root=tree.getroot();measure=root.find('part/measure')
        if name=='late-opening-tempo':
            direction=measure.find('direction');measure.remove(direction)
            measure.insert(list(measure).index(measure.findall('note')[4]),direction)
        elif name in ('replayed-ties','missing-fermata'):
            tags={'tie','tied'} if name=='replayed-ties' else {'fermata'}
            for parent in root.iter():
                for child in list(parent):
                    if child.tag in tags:parent.remove(child)
        else:
            tag='pedal' if name=='missing-pedal' else 'dynamics'
            for bar in root.findall('part/measure'):
                for direction in bar.findall('direction'):
                    if direction.find('.//'+tag) is not None:bar.remove(direction)
        source=out/(name+'.musicxml');tree.write(source,encoding='utf-8',xml_declaration=True)
        jobs.append({'in':str(source),'out':[str(out/(name+'.mid')),str(out/(name+'.mpos'))]})
    save(out/'job.json',jobs);checked_run([MSCORE,'-j',out/'job.json'],out/'export.log')
    reports=[]
    for name,_ in CASES:
        expected=json.loads((references[name]/'reference-performance.json').read_text())
        actual=read_reference(out/(name+'.mid'),out/(name+'.mpos'));result=compare(expected,actual)
        reports.append(dict(mutation=name,detected=not result['passed'],comparison=result))
    save(out/'report.json',reports)
    if not all(r['detected'] for r in reports):raise ValueError('A known playback defect escaped detection')
    print('All five real-export mutations detected')
