"""Resolve whole-note/fermata confusion using supported printed glyphs.

Remove a hold only when its recognized position overlaps a directly recovered
whole head. Add an omitted hold only above/below a uniquely aligned attack or chord at
the same printed x-position. Unknown positions or ambiguous source marks decline.
"""
from copy import deepcopy
from pathlib import Path
from livesheet.pdf_music_fonts import SMUFL_FONTS
import xml.etree.ElementTree as ET
from livesheet.pdf_rhythm_repair import load_mxl, write_mxl, Decline
from livesheet.pdf_clef_recovery import ClefEvidence
from livesheet.pdf_context_recovery import geometry

VERSION='printed-whole-fermatas-v1'
FERMATAS={'\ue4c0':'upright','\ue4c1':'inverted'}


def repair_bar(engine,index,measure):
    geo=geometry(engine,index);gap=geo['gap'];bar=geo['bar'];sx,sy=geo['sx'],geo['sy']
    proof=engine.vector.proof(index,[n for n in measure.findall('note') if n.find('pitch') is not None])
    if not proof['verified']:raise Decline(proof['reason'])
    if any(h['font'] not in SMUFL_FONTS for h in geo['local']):raise Decline('fermata-unsupported-font')
    symbols=[s for s in geo['symbols'] if bar['left']*sx<s['x']<bar['right']*sx]
    printed=[s for s in symbols if s['font'] in SMUFL_FONTS and s['c'] in FERMATAS]
    new=deepcopy(measure);nodes=[n for n in new.findall('note') if n.find('pitch') is not None]
    records=[r for r in engine.mapping['notes'] if r['measureIndex']==index]
    changes=[]
    for node,rec in zip(nodes,records):
        if not rec.get('box'):continue
        staff=rec['staff'];top,bottom=geo['bounds'][staff-1];x=rec['box'][0]*sx
        for nt in node.findall('notations'):
            for f in nt.findall('fermata'):
                if f.get('default-y') is None:continue
                try:mark_y=top-float(f.get('default-y'))*gap/10
                except ValueError:continue
                if f.get('type')!='inverted' or f.text and f.text.strip() not in ('normal',''):continue
                if any(abs(s['x']+s['w']/2-(x+rec['box'][2]*sx/2))<gap and abs(s['y']-mark_y)<gap*2 for s in printed):continue
                # The original head detector missed this whole and instead put
                # the fermata inside it. Both exact location and source identity
                # are required; absence of a printed hold alone is insufficient.
                heads=[r for r in records if r.get('pdfGlyphID') and r['staff']==staff and abs(r['box'][0]*sx-x)<gap*.3 and abs((r['box'][1]+r['box'][3]/2)*sy-mark_y)<gap*.5]
                if len(heads)!=1:continue
                whole=[s for s in geo['local'] if s['kind']=='whole' and s['staff']==staff and abs(s['x']-heads[0]['box'][0]*sx)<gap*.1 and abs(s['y']-(heads[0]['box'][1]+heads[0]['box'][3]/2)*sy)<gap*.1]
                if len(whole)!=1:continue
                nt.remove(f);changes.append(dict(action='remove-false-hold',staff=staff,sourceWholeGlyph=heads[0]['pdfGlyphID']))
            if not len(nt):node.remove(nt)
    groups=sorted({(r['staff'],r['onset']) for r in records})
    for staff,onset in groups:
        pairs=[(n,r) for n,r in zip(nodes,records) if r['staff']==staff and r['onset']==onset]
        if not pairs or any(n.find('grace') is not None or n.find('time-modification') is not None for n,r in pairs):continue
        top,bottom=geo['bounds'][staff-1]
        xs=[(r['box'][0]+r['box'][2]/2)*sx for n,r in pairs]
        ys=[(r['box'][1]+r['box'][3]/2)*sy for n,r in pairs]
        if max(xs)-min(xs)>gap*.25:continue
        matches=[s for s in printed if abs(s['x']+s['w']/2-xs[0])<gap*.3 and
                 (0<min(ys)-s['y']<gap*4 if FERMATAS[s['c']]=='upright' else 0<s['y']-max(ys)<gap*4)]
        if len(matches)!=1:continue
        s=matches[0];kind=FERMATAS[s['c']]
        # OMR can split the dot inside a fermata into a separate staccato.
        # Delete only that overlapping dot, not other articulation marks.
        for n,r in pairs:
            for nt in n.findall('notations'):
                for arts in nt.findall('articulations'):
                    for dot in arts.findall('staccato'):
                        if dot.get('default-y') is None:continue
                        try:
                            dot_y=top-float(dot.get('default-y'))*gap/10
                            dot_x=r['box'][0]*sx+r['box'][2]*sx/2+float(dot.get('default-x','0'))*gap/10
                        except ValueError:continue
                        if abs(dot_y-s['y'])<gap*.4 and abs(dot_x-s['x']-s['w']/2)<gap*.4:
                            arts.remove(dot);changes.append(dict(action='remove-fermata-dot-staccato',staff=staff))
                    if not len(arts):nt.remove(arts)
        if any(n.find('notations/fermata') is not None for n,r in pairs):continue
        # A fermata belongs to the chord. Put it on the chord's leading note
        # so performance exporters apply the hold to every member.
        leaders=[pair for pair in pairs if pair[0].find('chord') is None]
        if len(leaders)!=1:continue
        node,rec=leaders[0]
        nt=node.find('notations')
        if nt is None:nt=ET.SubElement(node,'notations')
        ET.SubElement(nt,'fermata',type=kind)
        changes.append(dict(action='add-printed-hold',staff=staff,glyph=s['c']))
    if not changes:raise Decline('no-proven-fermata-change')
    return new,changes


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);part=root.find('part');engine=ClefEvidence(pdf,mapping)
    report=dict(version=VERSION,accepted=[],declined=[])
    for i,m in enumerate(list(part)):
        try:
            new,changes=repair_bar(engine,i,m);part.remove(m);part.insert(i,new)
            report['accepted'].append(dict(measureIndex=i,changes=changes))
        except Decline as error:report['declined'].append(dict(measureIndex=i,reason=str(error)))
    write_mxl(source,target,root,name);result=deepcopy(mapping);result['musicxmlFiles']=[str(Path(target).resolve())]
    return result,report
