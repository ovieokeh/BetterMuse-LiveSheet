"""Recover one complete, attack-aligned printed Ped. line within a measure.

Requires complete notehead coverage, a unique supported Ped. glyph and thin
horizontal line, and unambiguous start/release attacks. Cross-system, multiple,
partial, offset or conflicting pedal instructions decline as a whole.
"""
from copy import deepcopy
from pathlib import Path
from fractions import Fraction as F
import xml.etree.ElementTree as E
from livesheet.pdf_clef_recovery import ClefEvidence
from livesheet.pdf_context_recovery import geometry
from livesheet.pdf_music_fonts import SMUFL_FONTS
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl


def printed_span(geo, anchors):
    gap=geo['gap'];left=geo['bar']['left']*geo['sx'];right=geo['bar']['right']*geo['sx']
    bottom=geo['bounds'][0][1];next_top=geo['bounds'][1][0]
    marks=[s for s in geo['symbols'] if s['font'] in SMUFL_FONTS and s['c']=='\ue650' and
           left<s['x']<right and bottom+gap<s['y']<next_top-gap]
    if len(marks)!=1:raise Decline('pedal-label-not-unique')
    mark=marks[0];lines=[]
    for d in geo['drawings']:
        if d.get('stroke') is None or d.get('fill') is not None or not 0<(d.get('width') or 0)<gap*.2 or len(d['items'])!=1:continue
        it=d['items'][0]
        if it[0]!='l':continue
        a,b=sorted(it[1:],key=lambda p:p[0])
        if abs(a[1]-b[1])>gap*.03 or abs(a[1]-mark['y'])>gap*.12:continue
        if not 0<a[0]-mark['x']-mark['w']<gap or not gap*2<b[0]-a[0] or b[0]>=right-gap:continue
        lines.append((a,b))
    if len(lines)!=1:raise Decline('pedal-line-not-unique-or-complete')
    a,b=lines[0]
    start=[h for h in anchors if abs(h['x']-mark['x'])<gap*.65]
    # Engravers stop a horizontal line just before the release attack.
    # Require one nearby attack, never interpolate time from spacing.
    stop=[h for h in anchors if 0<=h['x']-b[0]<gap]
    if len(start)!=1 or len(stop)!=1 or start[0]['onset']>=stop[0]['onset']:
        raise Decline('pedal-attack-anchor-ambiguous')
    return start[0],stop[0],dict(label=[mark['x'],mark['y']],line=[list(a),list(b)])


def replace_span(measure,start,stop,existing,divisions):
    new=deepcopy(measure);copies={id(n):c for n,c in zip(measure,new)}
    if existing is not None:
        if existing not in list(measure):raise Decline('pedal-existing-other-measure')
        pedal=existing.findall('direction-type/pedal');sounds=existing.findall('sound')
        if (existing.get('placement')!='below' or len(pedal)!=1 or pedal[0].get('type')!='start' or
            existing.findtext('staff','1')!='1' or any(n.tag not in ('direction-type','staff','sound') for n in existing) or
            any(n.tag!='pedal' for dt in existing.findall('direction-type') for n in dt) or
            len(sounds)>1 or any(set(s.attrib)!={'damper-pedal'} or s.get('damper-pedal')!='yes' or len(s) for s in sounds)):
            raise Decline('pedal-existing-conflict')
        cursor=F(0)
        for n in measure:
            if n is existing:break
            if n.tag in ('forward','backup') or n.tag=='note' and n.find('chord') is None and n.find('grace') is None:
                cursor+=F(n.findtext('duration','0'))/divisions*(-1 if n.tag=='backup' else 1)
        if cursor!=F(str(start['onset'])):raise Decline('pedal-existing-start-conflict')
        new.remove(copies[id(existing)])
    for anchor,kind in ((start,'start'),(stop,'stop')):
        d=E.Element('direction',placement='below');dt=E.SubElement(d,'direction-type')
        E.SubElement(dt,'pedal',type=kind,line='yes');E.SubElement(d,'staff').text='1'
        E.SubElement(d,'sound',**{'damper-pedal':'yes' if kind=='start' else 'no'})
        new.insert(list(new).index(copies[id(anchor['node'])]),d)
    return new


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);report=dict(version='printed-pedal-span-v1',accepted=[],declined=[])
    try:
        if len(root.findall('part'))!=1:raise Decline('pedal-multiple-parts')
        part=root.find('part');engine=ClefEvidence(pdf,mapping)
        marks=[s for page in engine.vector.symbols for s in page if s['font'] in SMUFL_FONTS and s['c']=='\ue650']
        if len(marks)!=1:raise Decline('pedal-score-needs-one-complete-span')
        existing=[n for m in part for n in m if n.find('.//pedal') is not None or n.find('.//sound[@damper-pedal]') is not None or n.tag=='sound' and 'damper-pedal' in n.attrib]
        if len(existing)>1:raise Decline('pedal-score-has-other-instructions')
        proposals=[];divisions=F(1)
        for i,m in enumerate(part):
            if m.findtext('attributes/divisions'):divisions=F(m.findtext('attributes/divisions'))
            geo=geometry(engine,i)
            nodes=[n for n in m.findall('note') if n.find('pitch') is not None]
            proof=engine.vector.proof(i,nodes)
            if not proof['verified']:continue
            records=[r for r in mapping['notes'] if r['measureIndex']==i]
            anchors=[dict(node=n,onset=r['onset'],x=r['box'][0]*geo['sx']) for n,r in zip(nodes,records)
                     if r['staff']==1 and r.get('box') and n.find('chord') is None and n.find('grace') is None]
            try:
                start,stop,evidence=printed_span(geo,anchors)
                new=replace_span(m,start,stop,existing[0] if existing else None,divisions)
                proposals.append((i,m,new,dict(startBeat=start['onset'],stopBeat=stop['onset'],evidence=evidence)))
            except Decline as error:report['declined'].append(dict(measureIndex=i,reason=str(error)))
        if len(proposals)!=1:raise Decline('pedal-span-not-uniquely-proven')
        i,m,new,evidence=proposals[0];part.remove(m);part.insert(i,new)
        report['accepted'].append(dict(measureIndex=i,**evidence))
    except Decline as error:report['declined'].append(dict(reason=str(error)))
    write_mxl(source,target,root,name);result=deepcopy(mapping);result['musicxmlFiles']=[str(Path(target).resolve())]
    return result,report
