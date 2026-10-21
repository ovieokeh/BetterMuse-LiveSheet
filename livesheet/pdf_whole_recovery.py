"""Recover omitted, isolated whole-note chords from supported vector PDFs.

Requires a complete 4/4 bar, one printed column, independently read clef and
accidentals, no stems/dots/nearby curves, and every retained head accounted for.
A source glyph supplies geometry even when OMR never detected that head.
Mixed-duration staffs and possible ties deliberately remain unresolved.
"""
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import xml.etree.ElementTree as ET
from livesheet.pdf_rhythm_repair import load_mxl, write_mxl, Decline
from livesheet.pdf_rhythm_evidence import HEADS
from livesheet.pdf_music_fonts import SMUFL_FONTS
from livesheet.pdf_clef_recovery import ClefEvidence, timeline, active
from livesheet.pdf_vector_recovery import verticals
from livesheet.score_recognition import read_mxl

VERSION = 'printed-isolated-whole-chords-v1'


def cursor_at_end(measure, divisions):
    cursor = F(0)
    for n in measure:
        if n.tag in ('backup', 'forward'):
            cursor += F(n.findtext('duration')) / divisions * (-1 if n.tag == 'backup' else 1)
        elif n.tag == 'note' and n.find('chord') is None and n.find('grace') is None:
            cursor += F(n.findtext('duration', '0')) / divisions
    return cursor


def recover_bar(engine, index, measure, divisions, key, clefs, *, context=None):
    context = context or {}
    mapping = engine.mapping
    bar = mapping['measures'][index]; page = mapping['pages'][bar['page']-1]
    system = next(s for s in page['systems'] if s['id'] == bar['system'])
    if len(system['staves']) != 2: raise Decline('whole-requires-two-staves')
    w, h = engine.vector.proof.page_sizes[bar['page']-1]
    sx, sy = w/page['width'], h/page['height']
    bounds = [(s['lines'][0][0][1]*sy, s['lines'][-1][0][1]*sy) for s in system['staves']]
    gap = (bounds[1][1]-bounds[1][0])/4
    middle = (bounds[0][1]+bounds[1][0])/2
    symbols = [g for g in engine.vector.symbols[bar['page']-1]
               if bar['left']*sx < g['x'] < bar['right']*sx
               and bounds[0][0]-5*gap < g['y'] < bounds[1][1]+5*gap]
    glyphs = [dict(g, kind=HEADS[g['font']][g['c']], staff=1 if g['y'] < middle else 2)
              for g in symbols if g['c'] in HEADS.get(g['font'], {})]
    nodes = [n for n in measure.findall('note') if n.find('pitch') is not None]
    records = [n for n in mapping['notes'] if n['measureIndex'] == index]
    if len(nodes) != len(records): raise Decline('whole-mapping-count')
    used = set(); matches = {}
    for node, rec in zip(nodes, records):
        if not rec.get('box'): raise Decline('whole-unmapped-existing-head')
        x, y, bw, bh = rec['box']
        found = [j for j, g in enumerate(glyphs) if abs(g['x']-x*sx)<gap*.35 and abs(g['y']-(y+bh/2)*sy)<gap*.3]
        if len(found) != 1 or found[0] in used: raise Decline('whole-ambiguous-existing-head')
        j = found[0]; used.add(j); matches[j] = (node, rec)
        if glyphs[j]['staff'] != rec['staff']: raise Decline('whole-cross-staff')
    missing = [g for j, g in enumerate(glyphs) if j not in used]
    if not missing: raise Decline('no-missing-printed-whole-heads')
    staffs = {g['staff'] for g in missing}
    drawings = engine.vector.proof.drawings[bar['page']-1]
    additions = []; replacement = deepcopy(measure)
    retained = {id(a):b for a,b in zip(measure,replacement)}
    for staff in sorted(staffs):
        group = [g for g in glyphs if g['staff'] == staff and (not context.get('mixed') or g['kind']=='whole')]
        if any(g['kind']!='whole' for g in missing if g['staff']==staff): raise Decline('whole-missing-nonwhole-head')
        if any(g['font'] not in SMUFL_FONTS for g in group): raise Decline('whole-unsupported-font')
        if any(not 1.4*gap < g['w'] < 2*gap for g in group): raise Decline('whole-cue-or-unsupported-size')
        if any(g['kind'] != 'whole' for g in group): raise Decline('whole-mixed-duration-staff')
        if max(g['x'] for g in group)-min(g['x'] for g in group)>gap*.25: raise Decline('whole-multiple-columns')
        if any(s['font'] in SMUFL_FONTS and 0xe4e1 <= ord(s['c']) <= 0xe4f4 and (1 if s['y']<middle else 2)==staff for s in symbols):
            raise Decline('whole-unaccounted-printed-rest')
        if len({round(g['y']/gap*2) for g in group}) != len(group): raise Decline('whole-overlapping-heads')
        if any(n.find('rest') is not None and int(n.findtext('staff', '1')) == staff for n in measure.findall('note')):
            raise Decline('whole-conflicting-rest')
        # No hidden onset inference: a whole note fills exactly one complete 4/4 bar.
        for j, (node, rec) in matches.items():
            if rec['staff'] != staff or (context.get('mixed') and node.findtext('type')!='whole'): continue
            if (node.findtext('type') != 'whole' or F(node.findtext('duration'))/divisions != 4
                or rec['onset'] != 0 or (node.findall('tie') and not context.get('tied')) or node.findall('dot')):
                raise Decline('whole-conflicting-existing-duration-or-tie')
        stems = verticals(drawings, gap)
        for g in group:
            if any(abs(x-g['x'])<gap*.25 or abs(x-g['x']-g['w'])<gap*.25
                   for x, a, b in stems if a-gap*.3<g['y']<b+gap*.3):
                raise Decline('whole-unexpected-stem')
            # A nearby curve can be a tie, including one continuing from another bar.
            for drawing_index,d in enumerate(drawings):
                if drawing_index in context.get('curves',set()): continue
                for item in d['items']:
                    if item[0] != 'c': continue
                    for point in (item[1], item[-1]):
                        if g['x']-gap < point[0] < g['x']+g['w']+gap and abs(point[1]-g['y'])<gap*1.5:
                            raise Decline('whole-possible-tie-or-slur')
            if any(s['c'] in ('\ue1e7', '\ue1e8') and g['x']+g['w']<s['x']<g['x']+g['w']+gap and abs(s['y']-g['y'])<gap for s in symbols):
                raise Decline('whole-augmentation-dot')
        existing = [(n, r) for n, r in zip(nodes, records) if r['staff'] == staff and (not context.get('mixed') or n.findtext('type')=='whole')]
        voices = {n.findtext('voice', '1') for n, _ in existing}
        if len(voices)>1: raise Decline('whole-multiple-existing-voices')
        voice = next(iter(voices), context.get('voices',{}).get(staff,str(max([int(n.findtext('voice', '1')) for n in nodes]+[0])+staff)))
        new_nodes = []; new_records = []
        for g in sorted((g for g in missing if g['staff']==staff), key=lambda g:-g['y']):
            box = [g['x']/sx, (g['y']-gap*.5)/sy, g['w']/sx, gap/sy]
            rec = dict(measureIndex=index, staff=staff, page=bar['page'], system=bar['system'], box=box,
                       normalizedBox=[box[0]/page['width'],box[1]/page['height'],box[2]/page['width'],box[3]/page['height']],
                       mapping='printed-whole-head', pdfGlyphID=f"p{bar['page']}-{g['x']:.4f}-{g['y']:.4f}")
            p = engine.pitch_at(rec, None, key, {})
            if p['clef'] != active(clefs[staff], index, 0): raise Decline('whole-clef-conflict')
            if any(e['x']>g['x'] for e in engine.events[(bar['page'],bar['system'],staff)]):
                raise Decline('whole-mid-bar-clef')
            node = ET.Element('note', {'default-x':str((g['x']/sx-bar['left'])*10/(gap/sx))})
            if existing or new_nodes: ET.SubElement(node, 'chord')
            pitch = ET.SubElement(node, 'pitch'); ET.SubElement(pitch,'step').text=p['step']
            if p['alter']: ET.SubElement(pitch,'alter').text=str(p['alter'])
            ET.SubElement(pitch,'octave').text=str(p['octave'])
            ET.SubElement(node,'duration').text=str(int(4*divisions))
            ET.SubElement(node,'voice').text=voice; ET.SubElement(node,'type').text='whole'
            ET.SubElement(node,'staff').text=str(staff)
            new_nodes.append(node); new_records.append(rec)
        if existing:
            # Extend the existing chord at its original position, without moving metadata.
            last = max(list(replacement).index(retained[id(n)]) for n, r in existing)
            for offset, node in enumerate(new_nodes, 1): replacement.insert(last+offset, node)
        else:
            end = cursor_at_end(replacement, divisions)
            if end<0 or end>4: raise Decline('whole-inconsistent-existing-cursor')
            at = next((i for i,n in enumerate(replacement) if n.tag=='barline' and n.get('location','right')=='right'), len(replacement))
            if end:
                backup = ET.Element('backup'); ET.SubElement(backup,'duration').text=str(int(end*divisions))
                replacement.insert(at,backup); at+=1
            for offset,node in enumerate(new_nodes): replacement.insert(at+offset,node)
        additions.extend(zip(new_nodes,new_records))
    old_iter = iter(records); new_by_id = {id(n):r for n,r in additions}
    ordered = [new_by_id[id(n)] if id(n) in new_by_id else next(old_iter) for n in replacement.findall('note') if n.find('pitch') is not None]
    check = deepcopy(engine.vector.proof); check.mapping = deepcopy(mapping)
    check.mapping['notes'] = [dict(r,measureIndex=index) for r in ordered]
    proof = check(index, [n for n in replacement.findall('note') if n.find('pitch') is not None])
    if not proof['verified']: raise Decline(proof['reason'])
    return replacement, ordered, dict(addedNotes=len(additions), heads=proof)


def recover(source, target, pdf, mapping):
    root,name=load_mxl(source); original=deepcopy(root)
    clefs,keys=timeline(root); engine=ClefEvidence(pdf,mapping)
    report=dict(version=VERSION, accepted=[], declined=[]); origins=[]
    part=root.find('part'); divisions=F(1); meter=None; unsupported=False
    for i,m in enumerate(list(part)):
        a=m.find('attributes')
        if a is not None:
            if a.findtext('divisions'): divisions=F(a.findtext('divisions'))
            if a.find('time') is not None: meter=(a.findtext('time/beats'),a.findtext('time/beat-type'))
            if len(a.findall('time'))>1 or a.find('time/senza-misura') is not None: unsupported=True
        try:
            if unsupported or meter!=('4','4') or m.get('implicit')=='yes': raise Decline('whole-unsupported-meter')
            seen_note=False
            for child in m:
                if child.tag=='note': seen_note=True
                if child.tag=='attributes' and seen_note: raise Decline('whole-mid-bar-attributes')
            new,records,proof=recover_bar(engine,i,m,divisions,keys[i],clefs)
            part.remove(m);part.insert(i,new);origins.extend(records)
            report['accepted'].append(dict(measureIndex=i,**proof))
        except Decline as e:
            report['declined'].append(dict(measureIndex=i,reason=str(e)))
            origins.extend(n for n in mapping['notes'] if n['measureIndex']==i)
    write_mxl(source,target,root,name)
    result = remap(target, mapping, origins)
    # Count directly printed heads independently of the detector's own head list.
    report['sourceCoverage']=[dict(measureIndex=i,**engine.vector.proof(i,[n for n in m.findall('note') if n.find('pitch') is not None])) for i,m in enumerate(original.find('part'))]
    after=deepcopy(engine.vector.proof);after.mapping=result
    report['remainingSourceGaps']=[dict(measureIndex=i,**p) for i,m in enumerate(part) if not (p:=after(i,[n for n in m.findall('note') if n.find('pitch') is not None]))['verified']]
    return result,report


def remap(target, mapping, origins):
    measures,notes=read_mxl(target)
    if len(notes)!=len(origins): raise ValueError('whole-mapping-count-mismatch')
    result=deepcopy(mapping)
    for old,new in zip(origins,notes):
        for k in ('page','system','box','normalizedBox','omrHeadID','mapping','recognitionGrade','pdfGlyphID'):
            if k in old: new[k]=deepcopy(old[k])
        new['onset']=float(new['onset'])
    for old,new in zip(result['measures'],measures): old.update(start=new['start'],duration=new['duration'])
    result.update(notes=notes,musicxmlFiles=[str(Path(target).resolve())])
    result['summary'].update(xmlPitchedNotes=len(notes),mappedNotes=sum(bool(n.get('box')) for n in notes),unmatchedXmlNotes=sum(not n.get('box') for n in notes))
    return result
