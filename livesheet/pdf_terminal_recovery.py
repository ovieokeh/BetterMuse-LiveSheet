"""Recover a source-proven final whole-note bar misclassified as cautionary.

This is an opt-in recognition stage, before the unchanged performance and later
repair guards. It preserves original exports. Only a final second-ending bar,
with explicit barlines, a bracket and full whole-head evidence, is supported.
"""
from copy import deepcopy
from fractions import Fraction as F
from zipfile import ZipFile
import hashlib
import re
import xml.etree.ElementTree as E
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl
from livesheet.pdf_clef_recovery import ClefEvidence, timeline
from livesheet.pdf_context_recovery import geometry
from livesheet.pdf_attribute_recovery import opening_attributes
from livesheet.pdf_whole_recovery import recover_bar, remap
from livesheet.pdf_repeat_recovery import recover as recover_endings


def omitted_region(omr,mapping):
    """Require the final OMR stack to be an empty cautionary region."""
    with ZipFile(omr) as archive:
        names=sorted((n for n in archive.namelist() if re.fullmatch(r'sheet#\d+/sheet#\d+\.xml',n)),key=lambda n:int(re.search(r'\d+',n)[0]))
        if not names or not mapping.get('measures') or not mapping.get('pages'):raise Decline('terminal-no-mapped-sheet')
        root=E.fromstring(archive.read(names[-1]));systems=root.findall('page/system')
        if not systems:raise Decline('terminal-no-system')
        system=systems[-1];stacks=system.findall('stack')
        if not stacks or stacks[-1].get('special')!='CAUTIONARY':raise Decline('no-terminal-cautionary-region')
        last=stacks[-1];previous=mapping['measures'][-1];page=mapping['pages'][-1]
        if len(system.findall('part'))!=1 or len(system.findall('part/staff'))!=2:raise Decline('terminal-requires-piano-part')
        if (len(names)!=len(mapping['pages']) or system.get('id')!=previous['system'] or previous['page']!=len(names) or
            page['systems'][-1]['id']!=system.get('id') or F(last.get('duration','0'))!=0 or last.findall('slot')):
            raise Decline('terminal-region-not-empty-final-stack')
        left,right=float(last.get('left')),float(last.get('right'))
        if abs(left-previous['right'])>1 or right<=left:raise Decline('terminal-region-not-adjacent')
        if any(abs(staff['lines'][0][-1][0]-right)>2 for staff in page['systems'][-1]['staves']):
            raise Decline('terminal-region-not-at-staff-end')
        return dict(page=previous['page'],system=previous['system'],left=left,right=right)


def verify_barlines(geo):
    gap=geo['gap'];left=geo['bar']['left']*geo['sx'];right=geo['bar']['right']*geo['sx']
    if right-left<gap*6:raise Decline('terminal-region-too-narrow')
    # A repeat boundary can contain thin and thick strokes. Require full staff
    # coverage at both boundaries, not a note stem or a bracket hook.
    vertical=[]
    for d in geo['drawings']:
        if d.get('stroke') is None or d.get('fill') is not None or not 0<(d.get('width') or 0)<gap*.8:continue
        for item in d['items']:
            if item[0]=='l' and abs(item[1][0]-item[2][0])<gap*.05:
                a,b=item[1:];vertical.append((a[0],min(a[1],b[1]),max(a[1],b[1])))
    for x in (left,right):
        for top,bottom in geo['bounds']:
            if not any(abs(v[0]-x)<gap*.5 and v[1]<top+gap*.1 and v[2]>bottom-gap*.1 for v in vertical):
                raise Decline('terminal-printed-barline-missing')
    if any(left+gap<v[0]<right-gap and v[1]<=geo['bounds'][0][0] and v[2]>=geo['bounds'][0][1] for v in vertical):
        raise Decline('terminal-has-intermediate-barline')


def recover(source,target,pdf,mapping,omr):
    region=omitted_region(omr,mapping)
    root,name=load_mxl(source);parts=root.findall('part')
    if len(parts)!=1 or len(parts[0])!=len(mapping['measures']):raise Decline('terminal-notation-count-conflict')
    original=deepcopy(root);part=parts[0];count=len(part)
    if len(part.findall('measure/attributes'))!=1 or part.find('measure/attributes') is None:
        raise Decline('terminal-changing-attributes-unsupported')
    if root.find('.//transpose') is not None or any(k.findtext('fifths','0')!='0' or k.find('key-step') is not None for k in root.findall('.//key')):
        raise Decline('terminal-key-or-transposition-unsupported')
    engine=ClefEvidence(pdf,mapping)
    opening,_=opening_attributes(engine,part[0]);attributes=opening.find('attributes')
    if (attributes.findtext('time/beats'),attributes.findtext('time/beat-type'))!=('4','4'):
        raise Decline('terminal-requires-printed-four-four')
    # Reject unrecognized key/accidental material in the opening header. C major
    # is not inferred merely because the recognizer omitted a key signature.
    first_geo=geometry(engine,0)
    for staff in (1,2):
        heads=[h for h in first_geo['local'] if h['staff']==staff]
        first=min(h['x'] for h in heads);top,bottom=first_geo['bounds'][staff-1]
        clef=engine.events[(first_geo['bar']['page'],first_geo['bar']['system'],staff)][0]
        header=[s for s in first_geo['symbols'] if clef['right']<s['x']<first and top-first_geo['gap']<s['y']<bottom+first_geo['gap']]
        dots=[s for s in header if s['c']=='\ue044']
        if dots and (len(dots)!=2 or abs(dots[0]['x']-dots[1]['x'])>.1*first_geo['gap'] or
                     abs(abs(dots[0]['y']-dots[1]['y'])-first_geo['gap'])>.1*first_geo['gap'] or
                     any(s['x']<=max(t['x']+t['w'] for t in header if t['c']=='\ue084') for s in dots)):
            raise Decline('terminal-uncertain-repeat-dots')
        if any(s['c'] not in ('\ue084','\ue044') or s['font'] not in ('Leland','Bravura','Petaluma') for s in header):
            raise Decline('terminal-uncertain-key-header')
    number=part[-1].get('number','')
    if not number.isdigit():raise Decline('terminal-unsupported-measure-number')
    added=E.Element('measure',number=str(int(number)+1));new_attrs=E.Element('attributes')
    # Make the source-proven state explicit only on the new bar. Earlier
    # notation is byte-preserved, including its existing recognition mistakes.
    for tag in ('divisions','time','staves','clef'):
        for node in attributes.findall(tag):new_attrs.append(deepcopy(node))
    added.append(new_attrs)
    work=deepcopy(mapping);work['measures'].append(dict(number=added.get('number'),start=mapping['measures'][-1]['start']+mapping['measures'][-1]['duration'],duration=0,**region))
    proof_root=deepcopy(root);proof_root.find('part').remove(proof_root.find('part')[0]);proof_root.find('part').insert(0,opening);proof_root.find('part').append(added)
    clefs,keys=timeline(proof_root);engine=ClefEvidence(pdf,work);geo=geometry(engine,count);verify_barlines(geo)
    if {h['staff'] for h in geo['local']}!={1,2} or any(h['kind']!='whole' for h in geo['local']):
        raise Decline('terminal-not-complete-whole-staffs')
    for symbol in geo['symbols']:
        if not region['left']*geo['sx']<symbol['x']<region['right']*geo['sx']:continue
        if any(all(symbol.get(k)==h.get(k) for k in ('x','y','c','font')) for h in geo['local']):continue
        if symbol['c']=='2' and symbol['y']<geo['bounds'][0][0]:continue
        raise Decline('terminal-unaccounted-printed-symbol')
    divisions=F(attributes.findtext('divisions','1'))
    new,records,proof=recover_bar(engine,count,added,divisions,keys[count],clefs)
    if proof['addedNotes']!=len(geo['local']):raise Decline('terminal-head-coverage-incomplete')
    part.append(new)
    target.parent.mkdir(parents=True,exist_ok=True)
    raw=target.with_name('before-endings.mxl');write_mxl(source,raw,root,name)
    work=remap(raw,work,mapping['notes']+records);work['summary']['measures']=count+1
    work,ending_report=recover_endings(raw,target,pdf,work,allow_terminal_open=True)
    after,_=load_mxl(target)
    # Existing measures may only have their proven ending metadata refreshed.
    # No prior note, timing, clef, direction, or repeat command may change.
    def content(m):
        m=deepcopy(m)
        for b in m.findall('barline'):
            for e in b.findall('ending'):b.remove(e)
            if not len(b):m.remove(b)
        for n in m.iter():n.text=(n.text or '').strip() or None;n.tail=None
        return E.tostring(m)
    if len(after.find('part'))!=count+1 or any(content(a)!=content(b) for a,b in zip(original.find('part'),after.find('part'))):
        raise ValueError('terminal-recovery-changed-existing-measures')
    if ending_report['printedEndings'][-1]['start']!=count or ending_report['printedEndings'][-1]['end']!=count:
        raise Decline('terminal-not-single-final-second-ending')
    report=dict(version='printed-terminal-whole-bar-v1',sourceSHA256=mapping['sha256'],
        originalMusicXMLSHA256=hashlib.sha256(source.read_bytes()).hexdigest(),candidateSHA256=hashlib.sha256(target.read_bytes()).hexdigest(),
        addedMeasureIndex=count,addedNotes=proof['addedNotes'],region=region,heads=proof['heads'],
        expectedMeasureVisits=ending_report['expectedMeasureVisits'],endings=ending_report,
        checks=dict(existingNotationPreserved=True,completePrintedHeads=True,printedBarlines=True,printedMeter=True))
    work['terminalRecovery']=report
    return work,report


def validate_traversal(package,report):
    expected=['m'+str(i) for i in report['expectedMeasureVisits']]
    if [m['id'] for m in package['measures']]!=expected:
        raise ValueError('terminal-performance-order-mismatch')
