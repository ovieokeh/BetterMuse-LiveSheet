"""Reject a fingering mistaken for a triplet in a complete beamed eighth lane.

Only an underfull 4/4 upper voice with a four-note beam ending in a false 3:2
triplet is supported. Printed stems/beams, fingering positions, complete heads,
rests, and simultaneous lower-staff anchors must independently agree.
"""
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import xml.etree.ElementTree as ET
from livesheet.pdf_rhythm_repair import load_mxl, write_mxl, Decline
from livesheet.pdf_vector_recovery import VectorRecovery, verticals
from livesheet.score_recognition import read_mxl

VERSION='printed-fingering-triplet-v1'


def beam_edges(drawing, gap):
    items=drawing['items']
    if not drawing.get('fill') or len(items) not in (3,4) or any(i[0]!='l' for i in items):return []
    edges=[i[1:] for i in items if abs(i[2][0]-i[1][0])>gap*2]
    return edges if len(edges)==2 else []


def single_beam(drawings,stem,gap):
    x,_,tip=stem;found=[]
    for d in drawings:
        edges=beam_edges(d,gap)
        if not edges or not all(min(a[0],b[0])-gap*.25<=x<=max(a[0],b[0])+gap*.25 for a,b in edges):continue
        ys=[a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]) for a,b in edges]
        if -gap*.4<tip-sum(ys)/2<gap*2:
            if not gap*.35<abs(ys[1]-ys[0])<gap*.65:return False
            found.append(abs(tip-sum(ys)/2)<gap*.4)
    return found==[True]


def repair_bar(vector,index,measure,divisions):
    nodes=[n for n in measure.findall('note') if n.find('pitch') is not None]
    records=[r for r in vector.mapping['notes'] if r['measureIndex']==index]
    proof=vector.proof(index,nodes)
    if not proof['verified']:raise Decline(proof['reason'])
    upper=[n for n in measure.findall('note') if n.findtext('staff','1')=='1']
    if len({n.findtext('voice') for n in upper})!=1:raise Decline('tuplet-multiple-upper-voices')
    if any(n.findtext('type')!='eighth' or n.find('chord') is not None or n.findall('dot') or n.find('grace') is not None for n in upper):raise Decline('tuplet-not-single-eighth-lane')
    altered=[n for n in upper if n.find('time-modification') is not None]
    if len(altered)!=3 or any(n.findtext('time-modification/actual-notes')!='3' or n.findtext('time-modification/normal-notes')!='2' or n.find('rest') is not None for n in altered):raise Decline('tuplet-not-three-to-two')
    if sum(F(n.findtext('duration'))/divisions for n in upper)!=F(7,2) or F(len(upper),2)!=4:raise Decline('tuplet-no-independent-meter-deficit')
    groups=[];group=[]
    for n in upper:
        beam=n.findtext("beam[@number='1']")
        if beam=='begin':group=[n]
        elif beam in ('continue','end') and group:
            group.append(n)
            if beam=='end':groups.append(group);group=[]
        else:group=[]
    candidates=[g for g in groups if len(g)==4 and g[1:]==altered]
    if len(candidates)!=1:raise Decline('tuplet-not-four-note-beam')
    group=candidates[0]
    if any(n.findtext('stem')!='down' for n in group):raise Decline('tuplet-unsupported-beam-side')
    bar=vector.mapping['measures'][index];page=vector.mapping['pages'][bar['page']-1]
    system=next(s for s in page['systems'] if s['id']==bar['system'])
    if len(system['staves'])!=2:raise Decline('tuplet-requires-two-staves')
    w,h=vector.proof.page_sizes[bar['page']-1];sx,sy=w/page['width'],h/page['height']
    top=system['staves'][0]['lines'][0][0][1]*sy;bottom=system['staves'][0]['lines'][-1][0][1]*sy;gap=(bottom-top)/4
    symbols=[s for s in vector.symbols[bar['page']-1] if bar['left']*sx<s['x']<bar['right']*sx and top-8*gap<s['y']<bottom+5*gap]
    by_node={id(n):r for n,r in zip(nodes,records)}
    drawings=vector.proof.drawings[bar['page']-1];lines=verticals(drawings,gap);stems=[];heads=[]
    for n in group:
        rec=by_node[id(n)];x,y,bw,bh=rec['box'];x*=sx;y=(y+bh/2)*sy
        found=[s for s in lines if abs(s[0]-x)<gap*.25 and abs(s[1]-y)<gap*.3]
        if len(found)!=1 or not single_beam(drawings,found[0],gap):raise Decline('tuplet-unproven-single-beam')
        stems.append(found[0]);heads.append((x,y))
    shared=[]
    for d in drawings:
        edges=beam_edges(d,gap)
        if len(edges)!=2:continue
        if all(all(min(a[0],b[0])-gap*.25<=x<=max(a[0],b[0])+gap*.25 for a,b in edges) and
               min(a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]) for a,b in edges)-gap*.2<=tip<=max(a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]) for a,b in edges)+gap*.2 for x,_,tip in stems):shared.append(d)
    if len(shared)!=1:raise Decline('tuplet-beam-not-shared-by-four')
    digits=[s for s in symbols if s['c'].isdigit()]
    # A tuplet number/bracket near the beam wins over the suspected fingering.
    if any(heads[0][0]-gap<s['x']<heads[-1][0]+2*gap and min(y for _,y in heads)<s['y']<max(t for _,_,t in stems)+3*gap for s in digits):raise Decline('tuplet-number-near-beam')
    # Fingering must sit above the staff, aligned with one head.
    finger=[s for s in digits if s['c']=='3' and abs(s['x']-heads[1][0])<gap*.4 and 0<top-s['y']<4*gap]
    if len(finger)!=1:raise Decline('tuplet-no-source-fingering')
    f=finger[0]
    peers=[s for s in digits if s['c'] in '125' and s['font']==f['font'] and abs(s['w']-f['w'])<gap*.1 and abs(s['y']-f['y'])<gap*.4]
    if len(peers)<2:raise Decline('tuplet-no-fingering-peers')
    # Ensure the upper rest is printed and no unaccounted upper rest is hidden.
    rests=[s for s in vector.proof.symbols[bar['page']-1] if bar['left']*sx<s['x']<bar['right']*sx and top-gap<s['y']<bottom+gap]
    rest_nodes=[n for n in upper if n.find('rest') is not None]
    if len(rests)!=len(rest_nodes) or any(s['kind']!='eighth-rest' for s in rests):raise Decline('tuplet-rest-evidence-conflict')
    # Lower voice proves the two half-bar anchors and total span independently.
    lower=[r for r in records if r['staff']==2]
    if {F(str(r['onset'])) for r in lower}!={F(0),F(2)} or any(F(str(r['duration']))!=2 for r in lower):raise Decline('tuplet-no-half-bar-anchors')
    for onset in (F(0),F(2)):
        anchor=min(r['box'][0]*sx for r in lower if F(str(r['onset']))==onset)
        n=upper[int(onset*2)]
        if n.find('rest') is not None:
            if len(rests)!=1 or abs(rests[0]['x']-anchor)>gap*.5:raise Decline('tuplet-rest-anchor-conflict')
        elif abs(by_node[id(n)]['box'][0]*sx-anchor)>gap*.5:raise Decline('tuplet-head-anchor-conflict')
    replacement=deepcopy(measure)
    # Preserve all other notes/metadata; only durations, tuplet marks and the
    # matching upper-voice rewind change. No re-spacing or guessed note onsets.
    before=list(measure);after=list(replacement)
    for n in altered:
        new=after[before.index(n)];new.find('duration').text=str(int(divisions/2));new.remove(new.find('time-modification'))
        for nt in new.findall('notations'):
            for t in nt.findall('tuplet'):nt.remove(t)
            if not len(nt):new.remove(nt)
    backups=replacement.findall('backup')
    if len(backups)!=1 or F(backups[0].findtext('duration'))/divisions!=F(7,2):raise Decline('tuplet-unsupported-rewind')
    backups[0].find('duration').text=str(int(4*divisions))
    return replacement,dict(correctedNotes=3,heads=proof,printedBeamNotes=4,printedFingeringPeers=len(peers))


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);vector=VectorRecovery(pdf,mapping);part=root.find('part')
    divisions=F(1);meter=None;report=dict(version=VERSION,accepted=[],declined=[])
    for i,m in enumerate(list(part)):
        a=m.find('attributes')
        if a is not None:
            if a.findtext('divisions'):divisions=F(a.findtext('divisions'))
            if a.find('time') is not None:meter=(a.findtext('time/beats'),a.findtext('time/beat-type'))
        if m.find('note/time-modification') is None:continue
        try:
            if meter!=('4','4') or m.get('implicit')=='yes' or (divisions/2).denominator!=1:raise Decline('tuplet-unsupported-meter')
            new,proof=repair_bar(vector,i,m,divisions);part.remove(m);part.insert(i,new)
            report['accepted'].append(dict(measureIndex=i,**proof))
        except Decline as e:report['declined'].append(dict(measureIndex=i,reason=str(e)))
    write_mxl(source,target,root,name);measures,notes=read_mxl(target)
    if len(notes)!=len(mapping['notes']):raise ValueError('tuplet-note-count-changed')
    result=deepcopy(mapping)
    for old,new in zip(result['notes'],notes):
        if any(old[k]!=new[k] for k in ('midi','staff','ties','measureIndex')):raise ValueError('tuplet-non-timing-change')
        old.update(onset=float(new['onset']),start=new['start'],duration=new['duration'])
    for old,new in zip(result['measures'],measures):old.update(start=new['start'],duration=new['duration'])
    result['musicxmlFiles']=[str(Path(target).resolve())]
    return result,report
