"""Use printed p/f consistently at a uniquely aligned upper-staff attack.

Numeric loudness imported by OMR is not printed evidence. Once the source proves
an isolated p/f, remove that derived override and use the pinned playback
engine's interpretation of the letter. Never apply this to authored MusicXML.
Compound dynamics, offsets, wedges and ambiguous placement decline.
"""
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import xml.etree.ElementTree as E
from livesheet.pdf_clef_recovery import ClefEvidence
from livesheet.pdf_context_recovery import geometry
from livesheet.pdf_music_fonts import SMUFL_FONTS
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl


def repair_bar(measure,geo,anchors,divisions):
    gap=geo['gap'];top=geo['bounds'][0][0];left=geo['bar']['left']*geo['sx'];right=geo['bar']['right']*geo['sx']
    marks=[s for s in geo['symbols'] if s['font'] in SMUFL_FONTS and 0xe520<=ord(s['c'])<=0xe54f and left<s['x']<right and top-4*gap<s['y']<top]
    cursor=F(0);changes=[];new=deepcopy(measure);copies={id(n):c for n,c in zip(measure,new)}
    for node in measure:
        if node.tag=='direction' and node.find('.//dynamics') is not None:
            if (node.get('placement')!='above' or node.findtext('staff','1')!='1' or
                any(c.tag not in ('direction-type','staff','sound') for c in node) or
                any(c.tag!='dynamics' for dt in node.findall('direction-type') for c in dt)):
                raise Decline('dynamic-combined-or-offset')
            ds=node.findall('direction-type/dynamics');sounds=node.findall('sound')
            if len(ds)!=1 or len(ds[0])!=1 or ds[0][0].tag not in ('p','f') or len(sounds)>1 or any(set(s.attrib)!={'dynamics'} or len(s) for s in sounds):
                raise Decline('dynamic-unsupported-existing')
            at=[a for a in anchors if F(str(a['onset']))==cursor]
            if len(at)!=1:raise Decline('dynamic-attack-not-unique')
            matches=[s for s in marks if abs(s['x']-at[0]['x'])<gap*.7]
            if len(matches)!=1 or matches[0]['c'] not in ('\ue520','\ue522'):raise Decline('dynamic-source-not-unique')
            mark=matches[0]
            if any(s is not mark and abs(s['x']-mark['x'])<gap*2 and abs(s['y']-mark['y'])<gap for s in marks):
                raise Decline('dynamic-compound-source')
            kind='p' if mark['c']=='\ue520' else 'f'
            if ds[0][0].tag!=kind or sounds:
                changed=copies[id(node)];d=changed.find('direction-type/dynamics');old=d[0].tag;d[0].tag=kind
                for sound in changed.findall('sound'):changed.remove(sound)
                changes.append(dict(fromMark=old,toMark=kind,onset=float(cursor),sourcePosition=[mark['x'],mark['y']],
                    removedNumericOverrides=[s.get('dynamics') for s in sounds],policy='printed-letter-renderer-default'))
        if node.tag in ('forward','backup') or node.tag=='note' and node.find('chord') is None and node.find('grace') is None:
            cursor+=F(node.findtext('duration','0'))/divisions*(-1 if node.tag=='backup' else 1)
    return new,changes


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);report=dict(version='printed-dynamic-letters-v2',accepted=[],declined=[])
    engine=ClefEvidence(pdf,mapping);part=root.find('part');divisions=F(1)
    for i,m in enumerate(list(part)):
        if m.findtext('attributes/divisions'):divisions=F(m.findtext('attributes/divisions'))
        if m.find('direction/direction-type/dynamics') is None:continue
        try:
            geo=geometry(engine,i);nodes=[n for n in m.findall('note') if n.find('pitch') is not None]
            proof=engine.vector.proof(i,nodes)
            if not proof['verified']:raise Decline(proof['reason'])
            records=[r for r in mapping['notes'] if r['measureIndex']==i]
            anchors=[dict(onset=r['onset'],x=r['box'][0]*geo['sx']) for n,r in zip(nodes,records)
                     if r['staff']==1 and r.get('box') and n.find('chord') is None and n.find('grace') is None]
            new,changes=repair_bar(m,geo,anchors,divisions)
            if changes:
                part.remove(m);part.insert(i,new);report['accepted'].append(dict(measureIndex=i,changes=changes))
        except Decline as error:report['declined'].append(dict(measureIndex=i,reason=str(error)))
    write_mxl(source,target,root,name);result=deepcopy(mapping);result['musicxmlFiles']=[str(Path(target).resolve())]
    return result,report
