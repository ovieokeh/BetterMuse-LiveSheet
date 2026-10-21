"""Recover missing opening meter/clefs from explicit, repeated printed symbols.

Only supported numeric single-digit meters, agreed on both piano staffs. Never
replace conflicting attributes, infer a key, or reinterpret existing notes on a
staff whose clef is absent. No later/courtesy/mid-bar signatures are inferred.
"""
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as E
from livesheet.pdf_clef_recovery import ClefEvidence
from livesheet.pdf_context_recovery import geometry
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl


def opening_attributes(engine, measure):
    g=geometry(engine,0);gap=g['gap'];left=g['bar']['left']*g['sx']
    if measure.get('implicit')=='yes':raise Decline('opening-pickup')
    attrs=measure.findall('attributes')
    if len(attrs)!=1 or list(measure).index(attrs[0])>next((i for i,n in enumerate(measure) if n.tag=='note'),0):
        raise Decline('opening-attributes-not-unique')
    a=attrs[0]
    if a.find('transpose') is not None or len(a.findall('time'))>1:raise Decline('unsupported-opening-attributes')
    meters=[];clefs={}
    for staff,(top,bottom) in enumerate(g['bounds'],1):
        heads=[h for h in g['local'] if h['staff']==staff]
        if not heads:raise Decline('opening-staff-has-no-printed-notes')
        first=min(h['x'] for h in heads)
        events=[e for e in engine.events[(g['bar']['page'],g['bar']['system'],staff)] if left<=e['x'] and e['right']<first]
        if len(events)!=1:raise Decline('opening-clef-not-unique')
        clefs[staff]=events[0]['kind']
        digits=[s for s in g['symbols'] if s['font'] in ('Leland','Bravura','Petaluma') and
                0xe080<=ord(s['c'])<=0xe089 and events[0]['right']<s['x']<first and top-gap<s['y']<bottom+gap]
        if len(digits)!=2:raise Decline('opening-meter-not-two-digits')
        digits.sort(key=lambda s:s['y']);upper,lower=digits
        if upper['font']!=lower['font'] or abs(upper['x']-lower['x'])>gap*.15:raise Decline('opening-meter-not-aligned')
        # Petaluma's tall handwritten digits have different baseline metrics.
        expected=(0,4) if upper['font']=='Petaluma' else (1,3)
        if any(abs(s['y']-(top+k*gap))>gap*.25 for s,k in zip(digits,expected)):
            raise Decline('opening-meter-off-staff')
        meter=tuple(str(ord(s['c'])-0xe080) for s in digits)
        if meter[0]=='0' or meter[1] not in ('2','4','8'):raise Decline('opening-meter-unsupported-value')
        meters.append(meter)
    if len(set(meters))!=1:raise Decline('opening-staff-meters-disagree')
    existing=a.find('time')
    if existing is not None and (existing.attrib or [n.tag for n in existing]!=['beats','beat-type'] or
                                (existing.findtext('beats'),existing.findtext('beat-type'))!=meters[0]):
        raise Decline('opening-meter-conflict')
    additions=[]
    for staff,kind in clefs.items():
        old=[c for c in a.findall('clef') if c.get('number','1')==str(staff)]
        if old:
            if len(old)!=1 or (old[0].findtext('sign'),old[0].findtext('line'))!=kind or old[0].findtext('clef-octave-change','0')!='0':
                raise Decline('opening-clef-conflict')
        else:
            if any(n.find('pitch') is not None and n.findtext('staff','1')==str(staff) for n in measure.findall('note')):
                raise Decline('missing-clef-with-existing-pitches')
            additions.append((staff,kind))
    new=deepcopy(measure);attr=new.find('attributes');changes=[]
    if existing is None:
        t=E.Element('time');E.SubElement(t,'beats').text=meters[0][0];E.SubElement(t,'beat-type').text=meters[0][1]
        attr.insert(next((i for i,n in enumerate(attr) if n.tag not in ('footnote','level','divisions','key')),len(attr)),t)
        changes.append(dict(action='add-printed-meter',beats=meters[0][0],beatType=meters[0][1]))
    for staff,(sign,line) in additions:
        c=E.Element('clef',number=str(staff));E.SubElement(c,'sign').text=sign;E.SubElement(c,'line').text=line
        at=next((i for i,n in enumerate(attr) if n.tag in ('staff-details','transpose','directive','measure-style')),len(attr))
        attr.insert(at,c);changes.append(dict(action='add-printed-clef',staff=staff,sign=sign,line=line))
    return new,changes


def repair_false_clef_octave(engine, root):
    """Remove a false octave suffix only if every affected pitch proves the shift.

    An ordinary known clef glyph, an otherwise empty clef-label region, and a
    consistent one-octave source/XML difference are all required. Genuine octave
    clefs, later clef changes, transposition and uncertain heads decline.
    """
    measures=root.find('part').findall('measure');proposals=[]
    if root.find('.//transpose') is not None:raise Decline('octave-clef-transposition')
    from livesheet.pdf_clef_recovery import timeline
    _,keys=timeline(root)
    for clef in measures[0].findall('attributes/clef'):
        change=clef.findtext('clef-octave-change','0')
        if change=='0':continue
        if change not in ('-1','1'):raise Decline('unsupported-clef-octave')
        staff=int(clef.get('number','1'));kind=(clef.findtext('sign'),clef.findtext('line'))
        if len([c for m in measures for c in m.findall('attributes/clef') if int(c.get('number','1'))==staff])!=1:
            raise Decline('octave-clef-later-change')
        for page in engine.mapping['pages']:
            for system in page['systems']:
                page_number=engine.mapping['pages'].index(page)+1
                events=engine.events[(page_number,system['id'],staff)]
                if len(events)!=1 or events[0]['kind']!=kind:raise Decline('octave-clef-source-conflict')
                e=events[0];gap=e['gap']
                w,h=engine.vector.proof.page_sizes[page_number-1];sy=h/page['height']
                lines=system['staves'][staff-1]['lines'];top=lines[0][0][1]*sy;bottom=lines[-1][0][1]*sy
                symbols=[s for s in engine.vector.symbols[page_number-1] if e['x']-gap*.5<s['x']<e['right']+gap*.5 and top-4*gap<s['y']<bottom+4*gap]
                if len(symbols)!=1 or symbols[0]['c'] not in ('\ue050','\ue062') or symbols[0]['font'] not in ('Leland','Bravura','Petaluma'):
                    raise Decline('octave-clef-label-not-empty')
        count=0
        for i,m in enumerate(measures):
            nodes=[n for n in m.findall('note') if n.find('pitch') is not None]
            records=[r for r in engine.mapping['notes'] if r['measureIndex']==i]
            if len(nodes)!=len(records):raise Decline('octave-clef-mapping-count')
            accidental_state={}
            for n,r in sorted(zip(nodes,records),key=lambda p:p[1]['box'][0] if p[1].get('box') else -1):
                if r['staff']!=staff:continue
                if n.find('grace') is not None:raise Decline('octave-clef-grace')
                p=engine.pitch_at(r,n,keys[i],accidental_state)
                if (p['clef']!=kind or n.findtext('pitch/step')!=p['step'] or
                    int(n.findtext('pitch/alter','0'))!=p['alter'] or int(n.findtext('pitch/octave'))!=p['octave']+int(change)):
                    raise Decline('octave-clef-pitches-not-explained')
                proposals.append((n.find('pitch/octave'),str(p['octave'])));count+=1
        if not count:raise Decline('octave-clef-no-notes')
        proposals.append((clef,None))
    for node,value in proposals:
        if value is None:node.remove(node.find('clef-octave-change'))
        else:node.text=value
    return bool(proposals)


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);report=dict(version='printed-opening-attributes-v1',accepted=[],declined=[])
    try:
        if len(root.findall('part'))!=1:raise Decline('opening-multiple-parts')
        engine=ClefEvidence(pdf,mapping);part=root.find('part')
        if repair_false_clef_octave(engine,root):
            for i in range(len(part)):
                report['accepted'].append(dict(measureIndex=i,changes=[dict(action='remove-source-disproved-clef-octave')]))
        m=part.find('measure')
        new,changes=opening_attributes(engine,m)
        if changes:
            part.remove(m);part.insert(0,new);report['accepted'].append(dict(measureIndex=0,changes=changes))
    except Decline as error:report['declined'].append(dict(measureIndex=0,reason=str(error)))
    write_mxl(source,target,root,name)
    from livesheet.pdf_whole_recovery import remap
    result=remap(target,mapping,mapping['notes'])
    return result,report
