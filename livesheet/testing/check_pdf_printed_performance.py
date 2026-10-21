#!/usr/bin/env python3
"""Separate printed-expression reference; never rewrite the original benchmark.

Only hidden numeric overrides beside authored p/f are removed. Every visible
marking and all other notation remain intact. The same pinned renderer and
unchanged absolute-time comparator supply the new reference and evaluation.
Recognition jobs receive no reference data. Holdouts remain sealed.
"""
import argparse
from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as E
from livesheet.testing.check_pdf_performance import DEFAULT, selection, verify_files, tools_key
from livesheet.testing.pdf_performance_fixtures import verify_renderer
from livesheet.testing.pdf_performance_reference import compare, read_reference, read_midi
from livesheet.pdf_experimental import fingerprint
from livesheet.testing.prepare_pdf_reference_set import checked_run, save, sha, MSCORE

VERSION='printed-expression-v1'


def printed_notation(root):
    """Independent reference transformation, restricted to this declared policy."""
    result=deepcopy(root);removed=[]
    for mi,m in enumerate(result.findall('part/measure')):
        for direction in m.findall('direction'):
            sounds=[s for s in direction.findall('sound') if 'dynamics' in s.attrib]
            if not sounds:continue
            marks=direction.findall('direction-type/dynamics')
            if len(marks)!=1 or len(marks[0])!=1 or marks[0][0].tag not in ('p','f'):
                raise ValueError('Unprinted/unsupported reference dynamic override')
            for sound in sounds:
                removed.append(dict(measureIndex=mi,mark=marks[0][0].tag,value=sound.attrib.pop('dynamics')))
                if not sound.attrib and not len(sound):direction.remove(sound)
    return result,removed


def verify_nonvelocity(original,derived):
    result=compare(original,derived)
    if (not result['attackTimingAndOrderPass'] or not result['tempoPass'] or
        result['endDifferenceSeconds']>result['tolerances']['durationSeconds'] or
        any(set(e['errors'])!={'velocity'} for e in result['changedEvents'])):
        raise ValueError('Printed reference changed more than dynamics strength')
    return result


def build(base):
    original=json.loads((base/'manifest.json').read_text());out=base/VERSION
    if (out/'manifest.json').exists():raise ValueError('Reference already exists; verify it or use a new version')
    if not original['complete'] or tools_key()['musescore']!=original['renderer']:raise ValueError('Incomplete reference or changed renderer')
    out.mkdir(exist_ok=True);entries=[];jobs=[]
    for e in selection(original,None,False):
        folder=verify_files(base,e)
        if not e['referenceValid'] or e['visualReview']!='passed':raise ValueError('Unreviewed/invalid reference')
        dest=out/e['id'];dest.mkdir(exist_ok=True)
        entry=dict(id=e['id'],family=e['family'],split=e['split'],sourcePDFSHA256=e['hashes']['score.pdf'],path=e['id'],removedOverrides=[])
        if e['family']=='articulation-dynamics':
            with ZipFile(folder/'score.mxl') as z:
                name=E.fromstring(z.read('META-INF/container.xml')).find('.//{*}rootfile').get('full-path')
                root=E.fromstring(z.read(name))
            root,removed=printed_notation(root)
            if [(r['measureIndex'],r['mark']) for r in removed]!=[(0,'p'),(1,'f')]:raise ValueError('Unexpected authored markings')
            entry['removedOverrides']=removed
            source=dest/'printed.musicxml';E.ElementTree(root).write(source,encoding='utf-8',xml_declaration=True)
            jobs.append(dict(**{'in':str(source),'out':[str(dest/'score.mid'),str(dest/'score.mpos')]}))
        entries.append(entry)
    save(out/'export-job.json',jobs);checked_run([MSCORE,'-j',out/'export-job.json'],out/'export.log',timeout=180)
    for entry in entries:
        dest=out/entry['path'];folder=base/'cases'/entry['id']
        original_performance=json.loads((folder/'reference-performance.json').read_text())
        if entry['removedOverrides']:
            performance=read_reference(dest/'score.mid',dest/'score.mpos')
            proof=verify_nonvelocity(original_performance,performance)
            authored=verify_renderer(entry['family'],performance)
            if not authored['passed']:raise ValueError('Printed expression violates authored musical intent')
            save(dest/'reference-check.json',dict(nonvelocity=proof,authored=authored))
            names=['printed.musicxml','score.mid','score.mpos','reference-check.json']
        else:performance=original_performance;names=[]
        save(dest/'reference-performance.json',performance);names+=['reference-performance.json']
        entry['hashes']={name:sha(dest/name) for name in names}
    save(out/'manifest.json',dict(version=VERSION,complete=True,originalManifestSHA256=sha(base/'manifest.json'),
        builderSHA256=sha(Path(__file__)),comparatorSHA256=sha(Path(__file__).with_name('pdf_performance_reference.py')),
        renderer=original['renderer'],policy='Printed p/f with renderer defaults; hidden numeric overrides removed from reference only. All other assertions and tolerances unchanged.',entries=entries))
    return out


def verified(base):
    out=base/VERSION;manifest=json.loads((out/'manifest.json').read_text())
    if not manifest['complete'] or manifest['version']!=VERSION:raise ValueError('Incomplete/wrong printed reference')
    if manifest['originalManifestSHA256']!=sha(base/'manifest.json'):raise ValueError('Original manifest changed')
    if manifest['comparatorSHA256']!=sha(Path(__file__).with_name('pdf_performance_reference.py')):raise ValueError('Comparator changed')
    if manifest['renderer']!=tools_key()['musescore']:raise ValueError('Renderer changed')
    original=json.loads((base/'manifest.json').read_text());entries=selection(original,None,False)
    if {e['id'] for e in entries}!={e['id'] for e in manifest['entries']}:raise ValueError('Reference cases changed')
    for e in entries:verify_files(base,e)
    for e in manifest['entries']:
        for name,digest in e['hashes'].items():
            if sha(out/e['path']/name)!=digest:raise ValueError('Printed reference identity changed')
    return out,manifest


def evaluate(base):
    out,manifest=verified(base);strict=json.loads((base/'evaluation.json').read_text());locked=fingerprint()
    if not strict['complete'] or strict['algorithm']!=locked:raise ValueError('Run the original strict benchmark with current pipeline first')
    strict_rows={r['id']:r for r in strict['rows']}
    if set(strict_rows)!={e['id'] for e in manifest['entries']}:raise ValueError('Strict evaluation missing cases')
    rows=[]
    for e in manifest['entries']:
        previous=strict_rows[e['id']];row=dict(id=e['id'],status=previous['status'],originalStrictStatus=previous['status'])
        if previous['status'] in ('passed','mismatch'):
            job=base/'jobs'/e['id'];midi=job/('experimental/performance/score/score.mid' if previous['preparationStatus'] in ('applied','unchanged') else 'performance/score/score.mid')
            if sha(job/'score.json')!=previous['packageSHA256'] or sha(midi)!=previous['midiSHA256']:raise ValueError('Evaluated output changed')
            actual=json.loads((job/'score.json').read_text());actual['tempos']=read_midi(midi)['tempos']
            expected=json.loads((out/e['path']/'reference-performance.json').read_text());result=compare(expected,actual)
            row.update(status='passed' if result['passed'] else 'mismatch',result=result,packageSHA256=previous['packageSHA256'])
        else:row['error']=previous.get('error','Reference blocked')
        rows.append(row);print(e['id'],row['status'],flush=True)
    if fingerprint()!=locked:raise ValueError('Pipeline changed during evaluation')
    report=dict(version=VERSION,complete=True,algorithm=locked,benchmark=dict(referenceManifestSHA256=sha(out/'manifest.json'),comparatorSHA256=manifest['comparatorSHA256']),
        originalStrictEvaluationSHA256=sha(base/'evaluation.json'),originalStrictCounts=strict['counts'],rows=rows,counts=dict(Counter(r['status'] for r in rows)),scope=manifest['policy'])
    save(out/'evaluation.json',report)
    return all(r['status']=='passed' for r in rows)


def mutations(base):
    out,manifest=verified(base);folder=out/'mutation-checks';folder.mkdir(exist_ok=True)
    entries={e['family']:e for e in manifest['entries'] if e['id'].endswith('--leland')}
    from livesheet.testing.check_pdf_performance_mutations import CASES
    reports=[]
    for name,family in CASES:
        expected=json.loads((out/entries[family]['path']/'reference-performance.json').read_text())
        actual=read_reference(base/'mutation-checks'/(name+'.mid'),base/'mutation-checks'/(name+'.mpos'))
        result=compare(expected,actual);reports.append(dict(mutation=name,detected=not result['passed'],comparison=result))
    source=out/entries['articulation-dynamics']['path']/'printed.musicxml';jobs=[]
    for name in ('reversed-dynamics','missing-forte','late-forte'):
        tree=E.parse(source);root=tree.getroot();second=root.findall('part/measure')[1];d=next(d for d in second.findall('direction') if d.find('.//dynamics') is not None)
        if name=='reversed-dynamics':
            for node in root.findall('.//dynamics'):node[0].tag='f' if node[0].tag=='p' else 'p'
        elif name=='missing-forte':second.remove(d)
        else:
            second.remove(d);second.insert(list(second).index(second.findall('note')[1]),d)
        path=folder/(name+'.musicxml');tree.write(path,encoding='utf-8',xml_declaration=True)
        jobs.append({'in':str(path),'out':[str(folder/(name+'.mid')),str(folder/(name+'.mpos'))]})
    save(folder/'job.json',jobs);checked_run([MSCORE,'-j',folder/'job.json'],folder/'export.log',timeout=180)
    expected=json.loads((out/entries['articulation-dynamics']['path']/'reference-performance.json').read_text())
    for name in ('reversed-dynamics','missing-forte','late-forte'):
        result=compare(expected,read_reference(folder/(name+'.mid'),folder/(name+'.mpos')))
        reports.append(dict(mutation=name,detected=not result['passed'],comparison=result))
    save(folder/'report.json',reports)
    if not all(r['detected'] for r in reports):raise ValueError('A deliberate defect escaped detection')
    print('All eight real-export mutations detected')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['build','evaluate','mutations']);p.add_argument('--base',type=Path,default=DEFAULT);a=p.parse_args()
    base=a.base.resolve()
    if a.action=='build':build(base)
    elif a.action=='mutations':mutations(base)
    else:raise SystemExit(0 if evaluate(base) else 1)
