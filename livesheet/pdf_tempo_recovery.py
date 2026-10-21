"""Recover explicit quarter-note tempos from aligned vector PDF markings.

Opening and later changes require complete head coverage and unique printed
attack alignment. Unsupported units, offsets and conflicting evidence decline.
"""
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import re
import pymupdf
import xml.etree.ElementTree as ET
from livesheet.pdf_music_fonts import TEMPO_FONTS, SMUFL_FONTS
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence

VERSION = 'printed-opening-tempo-v1'
QUARTER = '\ueca5'


def repair_opening(measure, lines, *, left, first_x, top, gap):
    tempos = [d for d in measure if d.find('.//metronome') is not None or
              d.find('.//sound[@tempo]') is not None or d.tag == 'sound' and 'tempo' in d.attrib]
    if len(tempos) != 1: raise Decline('opening-tempo-not-unique')
    direction = tempos[0]
    if direction.tag != 'direction' or direction.get('placement') != 'above':
        raise Decline('unsupported-tempo-direction')
    if direction.findtext('staff', '1') != '1' or any(c.tag not in ('direction-type', 'staff', 'sound') for c in direction):
        raise Decline('tempo-offset-or-extra-instructions')
    types = [c for dt in direction.findall('direction-type') for c in dt]
    if any(c.tag not in ('words', 'metronome') for c in types): raise Decline('combined-tempo-instructions')
    metros = direction.findall('direction-type/metronome'); sounds = direction.findall('sound')
    if len(metros) != 1 or len(sounds) != 1 or set(sounds[0].attrib) != {'tempo'} or len(sounds[0]):
        raise Decline('unsupported-tempo-sound')
    metro = metros[0]
    if [c.tag for c in metro] != ['beat-unit', 'per-minute'] or metro.findtext('beat-unit') != 'quarter':
        raise Decline('unsupported-metronome-unit')
    value = metro.findtext('per-minute', '')
    if not re.fullmatch(r'[1-9][0-9]{0,2}', value) or F(sounds[0].get('tempo')) != F(value):
        raise Decline('tempo-value-mismatch')
    words = ' '.join(''.join(c.itertext()).strip() for c in types if c.tag == 'words').strip()
    if not words: raise Decline('opening-tempo-words-required')
    expected = re.sub(r'\s+', '', words + '(' + QUARTER + '=' + value + ')')
    matches = []
    for line in lines:
        spans = line['spans']
        text = ''.join(s['text'] for s in spans)
        if re.sub(r'\s+', '', text) != expected: continue
        glyphs = [s for s in spans if QUARTER in s['text']]
        if len(glyphs) != 1 or glyphs[0]['font'] != 'LelandText' or glyphs[0]['text'] != QUARTER: continue
        x, y = spans[0]['origin']
        if not left <= x <= first_x + gap * .25 or not gap < top - y < gap * 6: continue
        if any(abs(s['origin'][1] - y) > gap * .2 for s in spans): continue
        matches.append((x, y))
    if len(matches) != 1: raise Decline('printed-opening-tempo-not-unique-or-aligned')
    cursor = F(0); divisions = None; first_note = None
    for child in measure:
        if child.tag == 'attributes':
            if child.find('divisions') is not None: divisions = F(child.findtext('divisions'))
        if child is direction: break
        if child.tag in ('note', 'forward', 'backup'):
            if divisions is None or divisions <= 0: raise Decline('missing-divisions')
            if child.tag == 'note' and first_note is None:
                first_note = child
                if cursor != 0 or child.find('rest') is not None or child.find('grace') is not None:
                    raise Decline('opening-attack-not-at-zero')
            if child.tag == 'note' and child.find('chord') is not None: continue
            cursor += F(child.findtext('duration', '0')) / divisions * (-1 if child.tag == 'backup' else 1)
    if cursor <= 0 or first_note is None: raise Decline('opening-tempo-already-at-start')
    new = deepcopy(measure); moved = new[list(measure).index(direction)]
    new.remove(moved); new.insert(list(new).index(new.find('note')), moved)
    return new, dict(measureIndex=0, fromBeat=str(cursor), toBeat='0', bpm=int(value),
                     printedPosition=list(matches[0]), action='anchor-printed-opening-tempo')


def printed_tempos(lines, *, left, right, top, gap, anchors):
    """Read explicit quarter metronomes and align their left edge to an attack."""
    found = []
    for line in lines:
        spans = line['spans']
        glyphs = [s for s in spans if QUARTER in s['text']]
        if not glyphs: continue
        # PDF text extraction can merge a nearby ottava symbol into this line.
        # Separate only that known symbol; arbitrary extra text still declines.
        spans=[s for s in spans if not (s['font'] in SMUFL_FONTS and s['text']=='\ue510'
                and abs(s['origin'][1]-glyphs[0]['origin'][1])>gap*.2)]
        x,y = spans[0]['origin']
        if not left <= x < right or not gap < top-y < gap*6: continue
        if len(glyphs)!=1 or glyphs[0]['font'] not in TEMPO_FONTS or glyphs[0]['text']!=QUARTER:
            raise Decline('unsupported-printed-tempo-glyph')
        if any(abs(s['origin'][1]-y)>gap*.2 for s in spans):raise Decline('tempo-baseline-conflict')
        text=''.join(s['text'] for s in spans).strip()
        match=re.fullmatch(r'([^0-9\ue000-\uf8ff]*?)\s*\(?'+QUARTER+r'\s*=\s*([1-9][0-9]{0,2})\)?',text)
        if not match:raise Decline('unsupported-printed-tempo-text')
        first=min(a['x'] for a in anchors)
        if x<=first+gap*.25:
            matches=[a for a in anchors if a['onset']==0 and abs(a['x']-first)<gap*.3]
        else:
            matches=[a for a in anchors if abs(a['x']-x)<gap*.5]
        onsets={a['onset'] for a in matches}
        if len(onsets)!=1:raise Decline('printed-tempo-anchor-ambiguous')
        found.append(dict(bpm=int(match[2]),onset=next(iter(onsets)),x=x,y=y))
    if len({m['onset'] for m in found})!=len(found):raise Decline('competing-printed-tempos')
    return sorted(found,key=lambda m:m['onset'])


def repair_measure_tempos(measure, printed, anchors, divisions):
    """Move supported directions or add omitted ones; leave all notes intact."""
    directions=[];cursor=F(0)
    for node in measure:
        if node.tag=='attributes' and node.findtext('divisions'):divisions=F(node.findtext('divisions'))
        if node.tag=='sound' and 'tempo' in node.attrib:raise Decline('standalone-tempo-sound')
        if node.tag=='direction' and (node.find('.//metronome') is not None or node.find('.//sound[@tempo]') is not None):
            metros=node.findall('direction-type/metronome');sounds=node.findall('sound')
            if (node.get('placement')!='above' or node.findtext('staff','1')!='1' or
                any(n.tag not in ('direction-type','staff','sound') for n in node) or
                any(n.tag not in ('words','metronome') for dt in node.findall('direction-type') for n in dt) or
                len(metros)!=1 or len(sounds)!=1 or set(sounds[0].attrib)!={'tempo'} or len(sounds[0])):
                raise Decline('unsupported-existing-tempo')
            metro=metros[0]
            if [c.tag for c in metro]!=['beat-unit','per-minute'] or metro.findtext('beat-unit')!='quarter':
                raise Decline('unsupported-metronome-unit')
            value=metro.findtext('per-minute','')
            if not re.fullmatch(r'[1-9][0-9]{0,2}',value) or F(sounds[0].get('tempo'))!=F(value):raise Decline('tempo-value-mismatch')
            directions.append((node,int(value),cursor))
        if node.tag in ('forward','backup') or node.tag=='note' and node.find('chord') is None and node.find('grace') is None:
            cursor+=F(node.findtext('duration','0'))/divisions*(-1 if node.tag=='backup' else 1)
    if len({b for n,b,t in directions})!=len(directions) or len({p['bpm'] for p in printed})!=len(printed):
        raise Decline('repeated-tempo-value-ambiguous')
    if any(b not in {p['bpm'] for p in printed} for n,b,t in directions):raise Decline('tempo-source-conflict')
    new=deepcopy(measure);copies={id(n):copy for n,copy in zip(measure,new)};changes=[]
    for mark in printed:
        matches=[(n,t) for n,b,t in directions if b==mark['bpm']]
        if matches and matches[0][1]==F(str(mark['onset'])):continue
        leaders=[a for a in anchors if a['onset']==mark['onset'] and a['node'].find('chord') is None]
        if len(leaders)!=1:raise Decline('tempo-insertion-anchor-ambiguous')
        if matches:
            original,old_onset=matches[0];direction=copies[id(original)];new.remove(direction)
        else:
            old_onset=None;direction=ET.Element('direction',placement='above')
            metro=ET.SubElement(ET.SubElement(direction,'direction-type'),'metronome')
            ET.SubElement(metro,'beat-unit').text='quarter';ET.SubElement(metro,'per-minute').text=str(mark['bpm'])
            ET.SubElement(direction,'staff').text='1';ET.SubElement(direction,'sound',tempo=str(mark['bpm']))
        new.insert(list(new).index(copies[id(leaders[0]['node'])]),direction)
        changes.append(dict(action='anchor-printed-tempo' if matches else 'add-printed-tempo',
                            fromBeat=str(old_onset) if matches else None,toBeat=str(mark['onset']),
                            bpm=mark['bpm'],printedPosition=[mark['x'],mark['y']]))
    return new,changes


def recover(source, target, pdf, mapping):
    root,name=load_mxl(source)
    if len(root.findall('part'))!=1:raise Decline('tempo-multiple-parts')
    part=root.find('part');proof=PDFHeadEvidence(pdf,mapping);divisions=F(1)
    report=dict(version='printed-quarter-tempos-v2',accepted=[],declined=[])
    with pymupdf.open(pdf) as doc:
        for i,measure in enumerate(list(part)):
            if measure.findtext('attributes/divisions'):divisions=F(measure.findtext('attributes/divisions'))
            try:
                nodes=[n for n in measure.findall('note') if n.find('pitch') is not None]
                checked=proof(i,nodes)
                if not checked['verified']:raise Decline(checked['reason'])
                records=[n for n in mapping['notes'] if n['measureIndex']==i]
                if len(records)!=len(nodes):raise Decline('tempo-mapping-count')
                bar=mapping['measures'][i];page=mapping['pages'][bar['page']-1]
                system=next(s for s in page['systems'] if s['id']==bar['system'])
                w,h=proof.page_sizes[bar['page']-1];sx,sy=w/page['width'],h/page['height']
                staff=system['staves'][0];top=staff['lines'][0][0][1]*sy
                gap=(staff['lines'][-1][0][1]*sy-top)/4
                anchors=[dict(node=n,onset=r['onset'],x=r['box'][0]*sx) for n,r in zip(nodes,records)
                         if r['staff']==1 and r.get('box') and n.find('grace') is None]
                if not anchors:raise Decline('upper-tempo-anchors-missing')
                lines=[l for b in doc[bar['page']-1].get_text('dict')['blocks'] for l in b.get('lines',[])]
                printed=printed_tempos(lines,left=bar['left']*sx,right=bar['right']*sx,top=top,gap=gap,anchors=anchors)
                new,changes=repair_measure_tempos(measure,printed,anchors,divisions)
                if changes:
                    part.remove(measure);part.insert(i,new)
                    report['accepted'].append(dict(measureIndex=i,changes=changes))
            except Decline as error:report['declined'].append(dict(measureIndex=i,reason=str(error)))
    write_mxl(source,target,root,name)
    result=deepcopy(mapping);result['musicxmlFiles']=[str(Path(target).resolve())]
    return result,report
