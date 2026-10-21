"""Source-proven context for whole notes: slur endpoints, held voices and ties.

Never ignores an ambiguous curve. Adjacent tied whole chords are repaired as
one transaction, with complete head coverage and unchanged original events.
"""
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import xml.etree.ElementTree as ET
from livesheet.pdf_rhythm_repair import load_mxl, write_mxl, Decline
from livesheet.pdf_clef_recovery import ClefEvidence, timeline, active
from livesheet.pdf_whole_recovery import recover_bar, remap
from livesheet.pdf_rhythm_evidence import HEADS
from livesheet.pdf_music_fonts import SMUFL_FONTS

VERSION='printed-whole-context-v1'


def geometry(engine,index):
    b=engine.mapping['measures'][index];p=engine.mapping['pages'][b['page']-1]
    system=next(s for s in p['systems'] if s['id']==b['system'])
    if len(system['staves'])!=2:raise Decline('context-two-staves-required')
    w,h=engine.vector.proof.page_sizes[b['page']-1];sx,sy=w/p['width'],h/p['height']
    bounds=[(s['lines'][0][0][1]*sy,s['lines'][-1][0][1]*sy) for s in system['staves']]
    gap=(bounds[0][1]-bounds[0][0])/4;mid=(bounds[0][1]+bounds[1][0])/2
    symbols=[g for g in engine.vector.symbols[b['page']-1] if bounds[0][0]-5*gap<g['y']<bounds[-1][1]+5*gap]
    heads=[dict(g,kind=HEADS[g['font']][g['c']],staff=1 if g['y']<mid else 2) for g in symbols if g['c'] in HEADS.get(g['font'],{})]
    local=[g for g in heads if b['left']*sx<g['x']<b['right']*sx]
    return dict(bar=b,page=p,sx=sx,sy=sy,bounds=bounds,gap=gap,heads=heads,local=local,symbols=symbols,drawings=engine.vector.proof.drawings[b['page']-1])


def lens(d,gap):
    its=d['items']
    if not d.get('fill') or len(its)!=2 or any(i[0]!='c' for i in its):return None
    if any(abs(a[k]-b[k])>gap*.05 for a,b in [(its[0][1],its[1][-1]),(its[0][-1],its[1][1])] for k in (0,1)):return None
    q=list(its[0][1:])
    if q[0][0]>q[-1][0]:q.reverse()
    if q[-1][0]-q[0][0]<gap*.5:return None
    return q,list(its[1][1:])


def unequal_pitch_slurs(geo):
    """A complete slur with unequal printed pitches cannot be a tie."""
    found=set();gap=geo['gap']
    for i,d in enumerate(geo['drawings']):
        curves=lens(d,gap)
        if not curves:continue
        q,_=curves;a,b=q[0],q[-1]
        if abs(a[1]-b[1])<gap:continue
        ends=[]
        for x,y in (a,b):
            hs=[g for g in geo['heads'] if g['x']-gap*.3<x<g['x']+g['w']+gap*.3 and abs(g['y']-y)<gap*1.5]
            if len(hs)!=1:break
            ends.append(hs[0])
        if len(ends)==2 and ends[0]['staff']==ends[1]['staff'] and abs(ends[0]['y']-ends[1]['y'])>gap:
            found.add(i)
    return found


def held_voice_context(engine,index,measure,divisions):
    geo=geometry(engine,index);gap=geo['gap'];context=dict(curves=unequal_pitch_slurs(geo))
    # A whole above/below a fully recognized moving line is a separate voice.
    nodes=[n for n in measure.findall('note') if n.find('pitch') is not None]
    records=[r for r in engine.mapping['notes'] if r['measureIndex']==index]
    for staff in (1,2):
        gs=[g for g in geo['local'] if g['staff']==staff]
        wholes=[g for g in gs if g['kind']=='whole'];moving=[g for g in gs if g['kind']!='whole']
        if not wholes or not moving:continue
        if len(wholes)!=1:raise Decline('context-multiple-held-voices')
        pairs=[(n,r) for n,r in zip(nodes,records) if r['staff']==staff]
        if len(pairs)!=len(moving) or len({r['voice'] for n,r in pairs})!=1:raise Decline('context-incomplete-moving-voice')
        if any(n.find('grace') is not None or n.find('time-modification') is not None or n.findtext('stem')!='down' for n,r in pairs):raise Decline('context-unsupported-moving-voice')
        intervals=sorted({(F(str(r['onset'])),F(str(r['duration']))) for n,r in pairs})
        cursor=F(0)
        for onset,dur in intervals:
            if onset!=cursor:raise Decline('context-moving-voice-not-complete')
            cursor+=dur
        if cursor!=4:raise Decline('context-moving-voice-not-four-beats')
        if min(g['y'] for g in moving)-wholes[0]['y']<gap:raise Decline('context-held-voice-not-separated')
        if abs(wholes[0]['x']-min(r['box'][0]*geo['sx'] for n,r in pairs if r['onset']==0))>gap*.4:raise Decline('context-held-onset-not-aligned')
        context['mixed']=True
    if not context.get('mixed') and not context['curves']:raise Decline('no-supported-whole-context')
    return context


def tie_curve(d,a,b,gap):
    curves=lens(d,gap)
    if not curves:return False
    q,other=curves;x0,y0=q[0];x1,y1=q[-1]
    if not gap*.5<x1-x0 or abs(y1-y0)>gap*.12:return False
    if abs(a['y']-b['y'])>gap*.2:return False
    if not a['x']+a['w']*.45<x0<a['x']+a['w']+gap*.5:return False
    if not b['x']-gap*.5<x1<b['x']+b['w']*.55:return False
    if not gap*.12<abs(y0-a['y'])<gap*(1 if a.get('font')=='Petaluma' else .85):return False
    side=1 if y0>a['y'] else -1
    if not all(gap*.08<side*(p[1]-y0)<gap*2.5 for p in q[1:3]+other[1:3]):return False
    return all(x0-gap*.1<=p[0]<=x1+gap*.1 for p in q+other)


def split_tie_curve(d, head, geo, outgoing):
    """Require a closed half-tie at the system edge or just before the first head."""
    gap=geo['gap'];curves=lens(d,gap)
    if not curves:return None
    q,other=curves;x0,y0=q[0];x1,y1=q[-1]
    if abs(y1-y0)>gap*.12 or not gap*.12<abs(y0-head['y'])<gap*.85:return None
    if outgoing:
        if not head['x']+head['w']*.45<x0<head['x']+head['w']+gap*.5:return None
        if abs(x1-geo['bar']['right']*geo['sx'])>gap*.5:return None
    else:
        if not head['x']-gap*2<x0<head['x']-gap*.5:return None
        if not head['x']-gap*.5<x1<head['x']+head['w']*.55:return None
        if any(h['staff']==head['staff'] and h['x']<head['x']-gap*.25 for h in geo['local']):return None
    side=1 if y0>head['y'] else -1
    if not all(gap*.08<side*(p[1]-y0)<gap*2.5 for p in q[1:3]+other[1:3]):return None
    if not all(x0-gap*.1<=p[0]<=x1+gap*.1 for p in q+other):return None
    return side


def source_pitch(engine,index,g,key):
    geo=geometry(engine,index);box=[g['x']/geo['sx'],(g['y']-geo['gap']/2)/geo['sy'],g['w']/geo['sx'],geo['gap']/geo['sy']]
    rec=dict(measureIndex=index,staff=g['staff'],box=box)
    p=engine.pitch_at(rec,None,key,{})
    return (p['step'],p['octave'],p['alter'])


def note_pitch(n):return(n.findtext('pitch/step'),int(n.findtext('pitch/octave')),int(n.findtext('pitch/alter','0')))


def whole_tie_pair(engine,index,measures,divisions,keys,clefs):
    a,b=measures[index:index+2];ga,gb=geometry(engine,index),geometry(engine,index+1);gap=ga['gap']
    cross_system=ga['bar']['system']!=gb['bar']['system']
    if ga['bar']['page']!=gb['bar']['page']:raise Decline('context-cross-page-tie')
    if cross_system:
        systems=[s['id'] for s in ga['page']['systems']]
        if systems.index(gb['bar']['system'])!=systems.index(ga['bar']['system'])+1:
            raise Decline('context-nonadjacent-systems')
        before=[j for j,m in enumerate(engine.mapping['measures']) if (m['page'],m['system'])==(ga['bar']['page'],ga['bar']['system'])]
        after=[j for j,m in enumerate(engine.mapping['measures']) if (m['page'],m['system'])==(gb['bar']['page'],gb['bar']['system'])]
        if max(before)!=index or min(after)!=index+1:raise Decline('context-tie-not-at-system-break')
    if a.find("barline[@location='right']/repeat") is not None or b.find("barline[@location='left']/repeat") is not None:raise Decline('context-repeat-between-ties')
    # A tied whole-note staff can coexist with independent moving voices on
    # the other staff. Scope the transaction to complete whole-note staffs.
    staffs = {staff for staff in (1,2) if all(
        any(h['staff']==staff for h in g['local']) and
        all(h['kind']=='whole' and h['font'] in SMUFL_FONTS for h in g['local'] if h['staff']==staff)
        for g in (ga,gb))}
    if not staffs: raise Decline('context-not-whole-chords')
    if any(n.find('rest') is not None and int(n.findtext('staff','1')) in staffs
           for m in (a,b) for n in m.findall('note')):raise Decline('context-tied-rest')
    voices={};curves=set();by_bar=[]
    for mi,m,g in [(index,a,ga),(index+1,b,gb)]:
        groups={}
        for h in g['local']:
            if h['staff'] not in staffs: continue
            k=(h['staff'],source_pitch(engine,mi,h,keys[mi]))
            if k in groups:raise Decline('context-duplicate-pitch')
            groups[k]=h
        by_bar.append(groups)
        records=[r for r in engine.mapping['notes'] if r['measureIndex']==mi and r['staff'] in staffs]
        if len(records)!=len([n for n in m.findall('note') if int(n.findtext('staff','1')) in staffs]) or any(r['onset']!=0 or r['duration']!=4 for r in records):raise Decline('context-existing-tie-onset-conflict')
        for n in m.findall('note'):
            if int(n.findtext('staff','1')) not in staffs: continue
            if n.find('pitch') is None or n.findtext('type')!='whole' or F(n.findtext('duration'))/divisions[mi]!=4:raise Decline('context-not-four-beat-whole')
            staff=int(n.findtext('staff','1'));voice=n.findtext('voice','1')
            if staff in voices and voices[staff]!=voice:raise Decline('context-conflicting-tie-voices')
            voices[staff]=voice
            if (staff,note_pitch(n)) not in groups:raise Decline('context-recognized-pitch-conflict')
            if n.find('time-modification') is not None or n.findall('dot'):raise Decline('context-modified-whole')
    if set(by_bar[0])!=set(by_bar[1]):raise Decline('context-tied-chords-differ')
    for k,h in by_bar[0].items():
        if cross_system:
            outgoing=[(i,side) for i,d in enumerate(ga['drawings']) if (side:=split_tie_curve(d,h,ga,True))]
            incoming=[(i,side) for i,d in enumerate(gb['drawings']) if (side:=split_tie_curve(d,by_bar[1][k],gb,False))]
            if len(outgoing)!=1 or len(incoming)!=1 or outgoing[0][1]!=incoming[0][1]:
                raise Decline('context-unproven-split-tie')
            matches=[outgoing[0][0],incoming[0][0]]
        else:
            matches=[i for i,d in enumerate(ga['drawings']) if tie_curve(d,h,by_bar[1][k],gap)]
            if len(matches)!=1:raise Decline('context-unproven-whole-tie')
        if any(j in curves for j in matches):raise Decline('context-ambiguous-whole-tie')
        curves.update(matches)
    # A wholly omitted staff has no recognized voice ID. Allocate a fresh ID
    # for this source-proven whole-note lane, shared across both bars.
    used_voices={n.findtext('voice','1') for m in (a,b) for n in m.findall('note')}
    for staff in sorted({k[0] for k in by_bar[0]}-set(voices)):
        if any(int(n.findtext('staff','1'))==staff for m in (a,b) for n in m.findall('note')):
            raise Decline('context-missing-tie-voice')
        fresh=1
        while str(fresh) in used_voices:fresh+=1
        voices[staff]=str(fresh);used_voices.add(str(fresh))
    # Misclassified slurs may be replaced only when both endpoints are wholly
    # contained in this proven pair. Do not break a slur continuing elsewhere.
    slurs={}
    for mi,m in enumerate((a,b)):
        for n in m.findall('note'):
            if int(n.findtext('staff','1')) not in staffs: continue
            for slur in n.findall('notations/slur'):slurs.setdefault(slur.get('number','1'),[]).append((mi,slur.get('type')))
    if any(sorted(v)!=[(0,'start'),(1,'stop')] for v in slurs.values()):raise Decline('context-unresolved-slur')
    output=[]
    for mi,m,g in [(index,a,ga),(index+1,b,gb)]:
        context=dict(curves=curves,tied=True,voices=voices)
        try:new,records,proof=recover_bar(engine,mi,m,divisions[mi],keys[mi],clefs,context=context)
        except Decline as error:
            if str(error)!='no-missing-printed-whole-heads':raise
            new=deepcopy(m);records=[r for r in engine.mapping['notes'] if r['measureIndex']==mi];proof=dict(addedNotes=0)
        kind='start' if mi==index else 'stop'
        for n in new.findall('note'):
            if int(n.findtext('staff','1')) not in staffs: continue
            existing={t.get('type') for t in n.findall('tie')}
            if existing-{kind}:raise Decline('context-tie-continues-outside-pair')
            for t in n.findall('tie'):n.remove(t)
            duration=n.find('duration');n.insert(list(n).index(duration)+1,ET.Element('tie',type=kind))
            nt=n.find('notations')
            if nt is None:nt=ET.SubElement(n,'notations')
            for t in list(nt):
                if t.tag in ('tied','slur'):nt.remove(t)
            ET.SubElement(nt,'tied',type=kind)
        output.append((new,records,proof))
    return output,dict(tiedPitches=len(by_bar[0]),curveIndices=sorted(curves),crossSystem=cross_system)


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);part=root.find('part');measures=list(part)
    if any(len(a.findall('time'))>1 or a.find('time/senza-misura') is not None for a in part.findall('measure/attributes')):
        raise Decline('context-unsupported-staff-meters')
    clefs,keys=timeline(root);engine=ClefEvidence(pdf,mapping);report=dict(version=VERSION,accepted=[],declined=[])
    divisions={};meters={};d=F(1);meter=None
    for i,m in enumerate(measures):
        a=m.find('attributes')
        if a is not None:
            if a.findtext('divisions'):d=F(a.findtext('divisions'))
            if a.find('time') is not None:meter=(a.findtext('time/beats'),a.findtext('time/beat-type'))
        divisions[i]=d;meters[i]=meter
    replacements={}
    def eligible(i):
        m=measures[i];seen_note=False
        for child in m:
            if child.tag=='note':seen_note=True
            if child.tag=='attributes' and seen_note:return False
        return meters[i]==('4','4') and m.get('implicit')!='yes' and len(m.findall('attributes'))<=1
    for i,m in enumerate(measures):
        if i in replacements or not eligible(i):continue
        try:
            context=held_voice_context(engine,i,m,divisions[i])
            new,records,proof=recover_bar(engine,i,m,divisions[i],keys[i],clefs,context=context)
            replacements[i]=(new,records);report['accepted'].append(dict(measureIndex=i,**proof,context='held-voice-or-unequal-pitch-slur'))
            continue
        except Decline as error:reason=str(error)
        if i+1<len(measures) and i+1 not in replacements and eligible(i+1):
            try:
                output,proof=whole_tie_pair(engine,i,measures,divisions,keys,clefs)
                for j,(new,records,heads) in enumerate(output,i):
                    replacements[j]=(new,records);report['accepted'].append(dict(measureIndex=j,**heads,**proof,context='adjacent-tied-whole-chords'))
                continue
            except Decline as error:reason += '; tie: ' + str(error)
        report['declined'].append(dict(measureIndex=i,reason=reason))
    origins=[]
    for i,m in enumerate(measures):
        if i in replacements:
            new,records=replacements[i];part.remove(m);part.insert(i,new);origins.extend(records)
        else:origins.extend(r for r in mapping['notes'] if r['measureIndex']==i)
    write_mxl(source,target,root,name);result=remap(target,mapping,origins)
    after=deepcopy(engine.vector.proof);after.mapping=result
    report['remainingSourceGaps']=[dict(measureIndex=i,**p) for i,m in enumerate(part) if not (p:=after(i,[n for n in m.findall('note') if n.find('pitch') is not None]))['verified']]
    return result,report
