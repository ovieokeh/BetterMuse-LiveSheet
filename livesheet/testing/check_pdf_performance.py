#!/usr/bin/env python3
"""Local playback benchmark: build independently checked PDFs, then run real workers.

Recognition receives only source.pdf. Expected MIDI/notation never enter its job
folder. Nonzero evaluation exit means a mismatch, failure or invalid reference;
these are findings, not automatically waived baseline failures.
"""
import argparse
from collections import Counter
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
import xml.etree.ElementTree as E

from livesheet.testing.pdf_performance_fixtures import CASES, VARIANTS, create, authored, verify_renderer
from livesheet.testing.pdf_performance_reference import read_reference, read_midi, compare
from livesheet.pdf_reference import read_score, compare as written_compare
from livesheet.pdf_experimental import fingerprint
from livesheet.testing.prepare_pdf_reference_set import checked_run,save,sha,MSCORE,AUDIVERIS

ROOT=Path(__file__).resolve().parents[2]
DEFAULT=ROOT/'artifacts/test-results/pdf-performance-v2'


def tools_key():
    return dict(musescore=subprocess.check_output([str(MSCORE),'--version'],text=True,timeout=30).strip(),
                audiverisSHA256=sha(AUDIVERIS.parent.parent/'app/audiveris.jar'))


def build(base, variants=None):
    import pymupdf
    from livesheet.testing.pdf_reference_validation import validate_authored
    # Fail before writing artifacts. Never silently render an inconsistent oracle.
    for spec in CASES.values():
        validate_authored(spec)
    base.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(base).free<10*1024**3:raise ValueError('10 GiB disk floor reached')
    manifest=dict(schemaVersion=2,complete=False,renderer=tools_key()['musescore'],
                  builderSHA256=sha(Path(__file__)),fixturesSHA256=sha(Path(__file__).with_name('pdf_performance_fixtures.py')),
                  splitPolicy='All variants of a musical family share one split. Holdouts require explicit opt-in; never tune recognition against them.',entries=[])
    if variants and (base/'manifest.json').exists():
        manifest['entries']=[e for e in json.loads((base/'manifest.json').read_text())['entries'] if e['variant'] not in variants]
    for variant,style in VARIANTS.items():
        if variants and variant not in variants:continue
        stylefile=base/(variant+'.mss');r=E.Element('museScore',version='4.0');s=E.SubElement(r,'Style')
        for k,v in dict(musicalSymbolFont=style['font'],musicalTextFont=style['text'],pageWidth=style['width'],pageHeight=style['height'],pagePrintableWidth=style['width']-1.18,titleFontSize=14,composerFontSize=8,Spatium=style['spatium']).items():E.SubElement(s,k).text=str(v)
        E.ElementTree(r).write(stylefile,encoding='utf-8',xml_declaration=True)
        imports=[];renders=[]
        for family,spec in CASES.items():
            ident=family+'--'+variant;folder=base/'cases'/ident;folder.mkdir(parents=True,exist_ok=True)
            source=folder/'source.musicxml';expected=create(family,source)
            if variant=='bravura-compact':
                tree=E.parse(source)
                for m in tree.getroot().find('part').findall('measure')[1:]:m.insert(0,E.Element('print',{'new-system':'yes'}))
                tree.write(source,encoding='utf-8',xml_declaration=True)
            save(folder/'expected-written.json',expected);save(folder/'authored-performance.json',authored(family))
            sourcecheck=written_compare(expected,read_score(source))
            if sourcecheck['differences']:raise ValueError('Authored source mismatch: '+ident)
            imports.append({'in':str(source),'out':str(folder/'render.mscz')})
            renders.append({'in':str(folder/'render.mscz'),'out':[str(folder/('score.'+ext)) for ext in ['pdf','mxl']]})
        save(base/'import-job.json',imports);checked_run([MSCORE,'-S',stylefile,'-j',base/'import-job.json'],base/(variant+'-import.log'),timeout=180)
        save(base/'render-job.json',renders);checked_run([MSCORE,'-j',base/'render-job.json'],base/(variant+'-render.log'),timeout=180)
        # Compare the correct notation and recognized notation through the same
        # MusicXML import route. Native project playback uses different release
        # defaults that MusicXML cannot preserve.
        exports=[{'in':str(base/'cases'/(family+'--'+variant)/'score.mxl'),
                  'out':[str(base/'cases'/(family+'--'+variant)/('score.'+ext)) for ext in ['mid','mpos']]}
                 for family in CASES]
        save(base/'performance-job.json',exports)
        checked_run([MSCORE,'-j',base/'performance-job.json'],base/(variant+'-performance.log'),timeout=180)
        for family,spec in CASES.items():
            ident=family+'--'+variant;folder=base/'cases'/ident
            expected=json.loads((folder/'expected-written.json').read_text())
            notation=written_compare(expected,read_score(folder/'score.mxl'))
            performance=read_reference(folder/'score.mid',folder/'score.mpos');check=verify_renderer(family,performance)
            save(folder/'reference-performance.json',performance)
            with pymupdf.open(folder/'score.pdf') as pdf:
                fonts=sorted({font[3] for p in pdf for font in p.get_fonts()})
                pages=len(pdf)
                # Preserve a review image for each layout, separate from the job.
                pdf[0].get_pixmap(matrix=pymupdf.Matrix(1.2,1.2),alpha=False).save(folder/'review.png')
            font_present=any(style['font'].lower() in f.lower() for f in fonts)
            passed=not notation['differences'] and check['passed'] and font_present
            save(folder/'reference-check.json',dict(passed=passed,written=notation,performance=check,fonts=fonts,fontPresent=font_present))
            files=['source.musicxml','score.pdf','score.mxl','score.mid','score.mpos','expected-written.json','authored-performance.json','reference-performance.json','reference-check.json']
            entry=dict(id=ident,family=family,variant=variant,split=spec.get('split','development'),path=str(folder.relative_to(base)),
                       hashes={f:sha(folder/f) for f in files},pages=pages,referenceValid=passed,visualReview='pending')
            manifest['entries'].append(entry);save(base/'manifest.json',manifest)
            print(ident,'reference-ok' if passed else 'REFERENCE-BLOCKED',check['errors'],flush=True)
    manifest['complete']=True;save(base/'manifest.json',manifest)


def verify_files(base,entry):
    folder=base/entry['path']
    for name,digest in entry['hashes'].items():
        if sha(folder/name)!=digest:raise ValueError('Reference identity changed: '+entry['id']+'/'+name)
    return folder


def selection(manifest,ids,allow_holdout):
    entries=[e for e in manifest['entries'] if e['id'] in ids] if ids else [e for e in manifest['entries'] if e['split']=='development']
    if ids and set(ids)!={e['id'] for e in entries}:raise ValueError('Unknown case ID')
    if any(e['split']=='holdout' for e in entries) and not allow_holdout:raise ValueError('Holdout remains sealed; explicit --allow-holdout required')
    families={}
    for e in manifest['entries']:
        if families.setdefault(e['family'],e['split'])!=e['split']:raise ValueError('Family crosses development/holdout split')
    return entries


def evaluate(base,ids,allow_holdout,retry_failed=False,baseline=None):
    manifest=json.loads((base/'manifest.json').read_text())
    if not manifest['complete']:raise ValueError('Incomplete reference set')
    if manifest['fixturesSHA256']!=sha(Path(__file__).with_name('pdf_performance_fixtures.py')):raise ValueError('Fixture definitions changed; rebuild references')
    entries=selection(manifest,ids,allow_holdout);locked=fingerprint();engine=tools_key();rows=[]
    if engine['musescore']!=manifest['renderer']:raise ValueError('Renderer version changed; review new references before evaluating')
    benchmark=dict(comparatorSHA256=sha(Path(__file__).with_name('pdf_performance_reference.py')),
                   fixturesSHA256=manifest['fixturesSHA256'],referenceManifestSHA256=sha(base/'manifest.json'),
                   runnerSHA256=sha(Path(__file__)))
    for e in entries:
        row=dict(id=e['id'],family=e['family'],variant=e['variant'],split=e['split'],status='failed')
        try:
            folder=verify_files(base,e)
            if not e['referenceValid'] or e.get('visualReview')!='passed':
                row['status']='reference-blocked';rows.append(row);continue
            if shutil.disk_usage(base).free<10*1024**3:raise ValueError('10 GiB disk floor reached')
            job=base/'jobs'/e['id'];job.mkdir(parents=True,exist_ok=True)
            key=dict(pdfSHA256=e['hashes']['score.pdf'],algorithm=locked,engine=engine)
            stamp=job/'benchmark-inputs.json';old=json.loads(stamp.read_text()) if stamp.exists() else {}
            if old==key and (job/'failure.json').exists() and not retry_failed:
                row['cached']=True
                raise ValueError(json.loads((job/'failure.json').read_text()).get('error','cached-preparation-failure'))
            reusable=old==key and (job/'score.json').exists() and not (job/'failure.json').exists()
            row['cached']=reusable
            if not reusable:
                shutil.copy2(folder/'score.pdf',job/'source.pdf')
                (job/'failure.json').unlink(missing_ok=True)
                save(stamp,key)
                print('Preparing',e['id'],flush=True)
                checked_run([sys.executable,'-m','livesheet.worker','--job-dir',job,'--profile','experimental-v1'],job/'benchmark-worker.log',timeout=2400)
            if fingerprint()!=locked:raise ValueError('Pipeline changed during benchmark')
            actual=json.loads((job/'score.json').read_text());expected=json.loads((folder/'reference-performance.json').read_text())
            quality=json.loads((job/'quality.json').read_text())
            midi=job/('experimental/performance/score/score.mid' if quality['status'] in ('applied','unchanged') else 'performance/score/score.mid')
            actual['tempos']=read_midi(midi)['tempos']
            result=compare(expected,actual);row.update(status='passed' if result['passed'] else 'mismatch',result=result,preparationStatus=quality['status'],
                packageSHA256=sha(job/'score.json'),midiSHA256=sha(midi),mapping=actual.get('mapping'))
            if (job/'baseline-score.json').exists():
                baseline_package=json.loads((job/'baseline-score.json').read_text());row['baselineResult']=compare(expected,baseline_package)
            save(job/'performance-comparison.json',row)
        except Exception as exc:
            failed=base/'jobs'/e['id']/'failure.json'
            row['error']=json.loads(failed.read_text()).get('error',str(exc)) if failed.exists() else str(exc)
        rows.append(row)
        save(base/'evaluation.json',dict(complete=False,algorithm=locked,engine=engine,benchmark=benchmark,rows=rows))
        print(e['id'],row['status'],flush=True)
    if fingerprint()!=locked:raise ValueError('Pipeline changed during benchmark')
    report=dict(complete=True,algorithm=locked,engine=engine,benchmark=benchmark,rows=rows,counts=dict(Counter(r['status'] for r in rows)),
                scope='Absolute sounding pitches, attacks, durations, velocities, tempo segments and measure visits. Same pinned renderer for truth and recognition export; not a general accuracy percentage.')
    if baseline:
        from livesheet.testing.pdf_performance_reference import regressions
        report['regression']=regressions(json.loads(baseline.read_text()),report)
    save(base/'evaluation.json',report)
    write_report(base,report,manifest)
    return all(r['status']=='passed' for r in rows) and bool(rows)


def write_report(base,report,manifest):
    lines=['# PDF playback benchmark', '',
           'Targeted stress fixtures, not an estimate of accuracy on everyday PDFs.', '',
           f"Results: {report['counts']}. All mismatches and failures remain visible.", '',
           'Timing is compared in absolute seconds. No tempo fitting, pitch shifting or duplicate removal.', '',
           '| Case | Result | Missing attacks | Extra attacks | Changed releases/velocities | Tempo errors |',
           '| --- | --- | ---: | ---: | ---: | ---: |']
    for row in report['rows']:
        r=row.get('result',{});ident=row['id']
        counts=[str(len(r[k])) if k in r else '—' for k in ['missingAttacks','extraAttacks','changedEvents','tempoErrors']]
        lines.append(f"| [{ident}](cases/{ident}/score.pdf) | {row['status']} | "+' | '.join(counts)+' |')
    lines.extend(['','## Preparation failures',''])
    for row in report['rows']:
        if row.get('error'):lines.append(f"- {row['id']}: `{row['error']}`. [Log](jobs/{row['id']}/benchmark-worker.log)")
    tested={r['id'] for r in report['rows']}
    sealed=[e['id'] for e in manifest['entries'] if e['split']=='holdout' and e['id'] not in tested
            and not (base/'jobs'/e['id']/'benchmark-inputs.json').exists()]
    lines.extend(['','## Holdout','',f"{len(sealed)} source-rendered holdout PDFs remain untested by recognition.",'',
                  '## Evidence','',
                  '- [Full comparison and fingerprints](evaluation.json)',
                  '- [Reference manifest](manifest.json)',
                  '- [Deliberately broken export checks](mutation-checks/report.json)',
                  '', 'The reference uses one pinned engraving/export engine. Authored checks verify musical intent before its MIDI becomes a reference. Multiple engravers, scans, ornaments and cursor geometry remain outside this set.'])
    if (base/'holdout-first-evaluation.json').exists():
        lines.extend(['', '- [Frozen first holdout evaluation](holdout-first-evaluation.json)'])
    (base/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['build','evaluate','mutations','reviewed']);p.add_argument('--output',type=Path,default=DEFAULT)
    p.add_argument('--variants',nargs='+',choices=list(VARIANTS));p.add_argument('--ids',nargs='+');p.add_argument('--allow-holdout',action='store_true');p.add_argument('--retry-failed',action='store_true');p.add_argument('--baseline',type=Path);a=p.parse_args()
    if a.action=='build':build(a.output.resolve(),a.variants)
    elif a.action=='mutations':
        from livesheet.testing.check_pdf_performance_mutations import run
        run(a.output.resolve())
    elif a.action=='reviewed':
        if not a.ids:p.error('Specify --ids for the PDFs you have visually inspected')
        manifest=json.loads((a.output/'manifest.json').read_text())
        # Reviewing the source render is allowed while recognition holdouts stay sealed.
        for entry in selection(manifest,a.ids,True):
            verify_files(a.output,entry);entry['visualReview']='passed'
        save(a.output/'manifest.json',manifest)
    else:sys.exit(0 if evaluate(a.output.resolve(),a.ids,a.allow_holdout,a.retry_failed,a.baseline) else 1)
