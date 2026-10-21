"""Repair a ledger-head error with a false dot in a complete single eighth-note lane.

Require a unique source head, its ledger line, stem and exactly one printed beam.
All other pitches/durations must agree. No timing is inferred from spacing.
"""
from copy import deepcopy
from fractions import Fraction as F
import xml.etree.ElementTree as E
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl
from livesheet.pdf_clef_recovery import ClefEvidence, timeline, active
from livesheet.pdf_context_recovery import geometry
from livesheet.pdf_vector_recovery import verticals, beam_count
from livesheet.pdf_whole_recovery import remap
from livesheet.pdf_music_fonts import SMUFL_FONTS


def dot_free(g, head):
    gap=g['gap'];left=head['x']+head['w'];y=head['y']
    # Known glyph dots and unknown nearby symbols both block removal. Also
    # reject vector ink where an engraver could have drawn a dot as a path.
    if any(left-gap*.1<s['x']<left+gap*1.6 and abs(s['y']-y)<gap for s in g['symbols']):return False
    for d in g['drawings']:
        if d.get('fill') is None:continue
        points=[p for item in d['items'] if item[0] in ('l','c') for p in item[1:]]
        if points and all(left-gap*.1<p[0]<left+gap*1.6 and abs(p[1]-y)<gap for p in points):return False
    return True


def ledger_at(g,head):
    gap=g['gap'];top,bottom=g['bounds'][head['staff']-1]
    if top-gap*.7<head['y']<bottom+gap*.7:return False
    matches=[]
    for d in g['drawings']:
        if d.get('stroke') is None or d.get('fill') is not None or not 0<(d.get('width') or 0)<gap*.25:continue
        for item in d['items']:
            if item[0]!='l':continue
            a,b=sorted(item[1:],key=lambda p:p[0])
            if abs(a[1]-head['y'])<gap*.1 and abs(b[1]-head['y'])<gap*.1 and (
                gap*.1<head['x']-a[0]<gap and gap*.1<b[0]-head['x']-head['w']<gap):matches.append(item)
    return len(matches)==1


def repair_bar(engine,index,measure,divisions,key,clefs,budget):
    nodes=measure.findall('note');records=[r for r in engine.mapping['notes'] if r['measureIndex']==index]
    if not nodes or len(nodes)!=len(records):raise Decline('ledger-incomplete-note-mapping')
    if any(n.find('pitch') is None or any(n.find(t) is not None for t in ('chord','grace','time-modification','tie','notations','accidental')) for n in nodes):
        raise Decline('ledger-unsupported-note')
    if any(n.tag in ('backup','forward') for n in measure):raise Decline('ledger-multiple-lanes')
    if len({(n.findtext('staff','1'),n.findtext('voice','1')) for n in nodes})!=1:raise Decline('ledger-multiple-voices')
    staff=int(nodes[0].findtext('staff','1'));g=geometry(engine,index);gap=g['gap']
    heads=sorted([h for h in g['local'] if h['staff']==staff],key=lambda h:h['x'])
    if len(heads)!=len(nodes) or any(h['kind']!='filled' or h['font'] not in SMUFL_FONTS for h in heads):raise Decline('ledger-source-head-count-or-kind')
    dotted=[i for i,n in enumerate(nodes) if len(n.findall('dot'))==1]
    if len(dotted)!=1 or any(len(n.findall('dot'))>1 for n in nodes):raise Decline('ledger-needs-one-suspect-dot')
    suspect=dotted[0]
    if any(n.findtext('type')!='eighth' or F(n.findtext('duration','0'))/divisions!=(F(3,4) if j==suspect else F(1,2)) for j,n in enumerate(nodes)):
        raise Decline('ledger-existing-rhythm-not-supported')
    if F(len(nodes),2)!=budget or measure.get('implicit')=='yes':raise Decline('ledger-lane-not-complete')
    after_suspect=False
    for child in measure:
        if after_suspect and child.tag not in ('note','barline'):raise Decline('ledger-later-metadata')
        if child is nodes[suspect]:after_suspect=True
    if not ledger_at(g,heads[suspect]) or not dot_free(g,heads[suspect]):raise Decline('ledger-or-dot-evidence-missing')
    stems=verticals(g['drawings'],gap);origins=[];pitches=[];accidentals={}
    for j,(n,r,h) in enumerate(zip(nodes,records,heads)):
        if not r.get('box') or abs(r['box'][0]*g['sx']-h['x'])>gap*.3:raise Decline('ledger-head-x-conflict')
        distance=abs((r['box'][1]+r['box'][3]/2)*g['sy']-h['y'])
        if j==suspect:
            if not gap*.3<distance<gap*.7:raise Decline('ledger-head-displacement-not-one-step')
        elif distance>gap*.25:raise Decline('ledger-other-head-conflict')
        stem=n.findtext('stem')
        if stem not in ('up','down'):raise Decline('ledger-stem-missing')
        stem_x=h['x']+(h['w'] if stem=='up' else 0)
        found=[v for v in stems if abs(v[0]-stem_x)<gap*.3 and abs((v[2] if stem=='up' else v[1])-h['y'])<gap*.35]
        if len(found)!=1 or beam_count(g['drawings'],found[0],stem,gap)!=1:raise Decline('ledger-beam-not-single-eighth')
        if not dot_free(g,h):raise Decline('ledger-possible-printed-dot')
        rec=deepcopy(r);rec['box']=[h['x']/g['sx'],(h['y']-gap/2)/g['sy'],h['w']/g['sx'],gap/g['sy']]
        rec['normalizedBox']=[v/d for v,d in zip(rec['box'],[g['page']['width'],g['page']['height']]*2)]
        p=engine.pitch_at(rec,n,key,accidentals)
        if p['clef']!=active(clefs[staff],index,r['onset']):raise Decline('ledger-clef-conflict')
        old=(n.findtext('pitch/step'),int(n.findtext('pitch/octave')),int(n.findtext('pitch/alter','0')))
        wanted=(p['step'],p['octave'],p['alter'])
        if j!=suspect and old!=wanted:raise Decline('ledger-other-pitch-conflict')
        if j==suspect:
            if old[2]!=wanted[2] or abs(old[1]*7+'CDEFGAB'.index(old[0])-wanted[1]*7-'CDEFGAB'.index(wanted[0]))!=1:
                raise Decline('ledger-pitch-not-one-step')
            rec.update(mapping='printed-ledger-head+stem+beam',pdfGlyphID=f"p{g['bar']['page']}-{h['x']:.4f}-{h['y']:.4f}")
        else:rec=deepcopy(r)
        origins.append(rec);pitches.append(p)
    new=deepcopy(measure);n=new.findall('note')[suspect];p=pitches[suspect]
    n.find('pitch/step').text=p['step'];n.find('pitch/octave').text=str(p['octave'])
    n.remove(n.find('dot'))
    if (divisions/2).denominator!=1:raise Decline('ledger-duration-not-representable')
    n.find('duration').text=str(int(divisions/2))
    return new,origins,dict(noteIndex=suspect,oldPitch=nodes[suspect].findtext('pitch/step')+nodes[suspect].findtext('pitch/octave'),
        pitch=p['step']+str(p['octave']),fromDuration='3/4',duration='1/2',printedHeads=len(heads),printedBeams=len(heads))


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);clefs,keys=timeline(root);engine=ClefEvidence(pdf,mapping)
    report=dict(version='printed-ledger-head-v1',accepted=[],declined=[]);part=root.find('part');origins=[];divisions=F(1);budget=None
    for i,m in enumerate(list(part)):
        a=m.find('attributes')
        if a is not None:
            if a.findtext('divisions'):divisions=F(a.findtext('divisions'))
            if a.find('time') is not None:
                budget=None
                if len(a.findall('time'))==1 and a.findtext('time/beats','').isdigit() and a.findtext('time/beat-type','').isdigit() and int(a.findtext('time/beat-type'))>0:
                    budget=F(a.findtext('time/beats'))*4/F(a.findtext('time/beat-type'))
        try:
            new,records,proof=repair_bar(engine,i,m,divisions,keys[i],clefs,budget)
            part.remove(m);part.insert(i,new);origins.extend(records);report['accepted'].append(dict(measureIndex=i,**proof))
        except Decline as error:
            origins.extend(r for r in mapping['notes'] if r['measureIndex']==i)
            report['declined'].append(dict(measureIndex=i,reason=str(error)))
    write_mxl(source,target,root,name)
    return remap(target,mapping,origins),report
