"""Recover one simple 1st/2nd ending pair from explicit vector PDF brackets.

Only a single recognized forward/backward repeat is supported. Source labels,
hooks, system continuation and measure boundaries must all agree. No notes,
rhythm or existing repeat directions are edited. Ambiguity declines recovery.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET
import pymupdf
from livesheet.pdf_rhythm_repair import load_mxl, write_mxl, Decline

VERSION = 'printed-repeat-endings-v1'


def read_evidence(pdf, mapping):
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != mapping['sha256']:
        raise Decline('repeat-source-identity-mismatch')
    systems = []
    with pymupdf.open(pdf) as doc:
        if len(doc) != len(mapping['pages']): raise Decline('repeat-page-count-mismatch')
        for pi, page in enumerate(doc):
            geometry = mapping['pages'][pi]; sx = page.rect.width / geometry['width']; sy = page.rect.height / geometry['height']
            lines, chars = [], []
            for drawing in page.get_drawings():
                if drawing.get('color') is None or drawing.get('dashes') != '[] 0': continue
                for item in drawing['items']:
                    if item[0] == 'l': lines.append((tuple(item[1]), tuple(item[2])))
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        for c in span['chars']:
                            if c['c'].isdigit(): chars.append(dict(text=c['c'], box=list(c['bbox'])))
            for system in geometry['systems']:
                staff = system['staves'][0]; top = staff['lines'][0][0][1]*sy
                gap = (staff['lines'][-1][0][1]-staff['lines'][0][0][1])*sy/4
                bars = [dict(index=i, left=m['left']*sx, right=m['right']*sx) for i,m in enumerate(mapping['measures']) if m['page']==pi+1 and m['system']==system['id']]
                if not bars: raise Decline('repeat-system-without-bars')
                first_heads = [n['box'][0]*sx for n in mapping['notes'] if n['measureIndex']==bars[0]['index'] and n.get('box')]
                systems.append(dict(page=pi+1, id=system['id'], gap=gap, top=top,
                    bars=bars, firstHead=min(first_heads, default=bars[0]['left']), lines=lines, chars=chars))
    return systems


def brackets(systems, *, allow_terminal_open=False):
    segments = []
    for si, system in enumerate(systems):
        gap, top, bars = system['gap'], system['top'], system['bars']
        for p,q in system['lines']:
            left,right = sorted((p[0],q[0])); y=p[1]
            if abs(p[1]-q[1]) > .04*gap or right-left < 8*gap or not 2*gap < top-y < 10*gap: continue
            def hook(x):
                return sum(abs(a[0]-x)<.08*gap and abs(b[0]-x)<.08*gap and abs(min(a[1],b[1])-y)<.08*gap and 2*gap<abs(a[1]-b[1])<8*gap for a,b in system['lines'])
            lh,rh=hook(left),hook(right)
            if lh>1 or rh>1: raise Decline('ambiguous-repeat-hook')
            labels=[c for c in system['chars'] if -.1*gap<c['box'][0]-left<.9*gap and .1*gap<c['box'][1]-y<1.3*gap]
            if not lh and not rh: continue # no proof this is a volta bracket
            if lh:
                if len(labels)!=1 or labels[0]['text'] not in ('1','2'): raise Decline('unsupported-repeat-label')
                label=labels[0]
                # A same-size adjacent digit is a multi-number label, not proof of
                # one pass. Smaller fingering digits near the bracket stay separate.
                if any(c is not label and 0 <= c['box'][0]-label['box'][2] < 2*gap
                       and abs(c['box'][1]-label['box'][1]) < .15*gap
                       and abs((c['box'][3]-c['box'][1])-(label['box'][3]-label['box'][1])) < .15*gap
                       for c in system['chars']):
                    raise Decline('unsupported-multiple-ending-label')
                starts=[b['index'] for b in bars if abs(b['left']-left)<gap]
                if len(starts)!=1: raise Decline('repeat-start-not-on-barline')
                start=starts[0]; number=labels[0]['text']
            else:
                if labels or not bars[0]['left']<=left<=system['firstHead']+gap: raise Decline('ambiguous-repeat-continuation')
                start=bars[0]['index']; number=None
            ends=[b['index'] for b in bars if abs(b['right']-right)<1.2*gap]
            if len(ends)!=1: raise Decline('repeat-end-not-on-barline')
            if not rh and ends[0]!=bars[-1]['index']: raise Decline('open-repeat-before-system-end')
            segments.append(dict(system=si, page=system['page'], start=start, end=ends[0], number=number, closed=bool(rh), height=(top-y)/gap))
    segments.sort(key=lambda s:(s['system'],s['start']))
    spans=[]; active=None
    for s in segments:
        if s['number']:
            if active: raise Decline('unclosed-repeat-ending')
            active=dict(start=s['start'], number=s['number'], segments=[])
        else:
            if not active or active['segments'][-1]['system']+1!=s['system'] or active['segments'][-1]['end']+1!=s['start'] or abs(active['segments'][-1]['height']-s['height'])>.6:
                raise Decline('unproven-repeat-continuation')
        active['segments'].append(s)
        if s['closed']:
            active['end']=s['end'];spans.append(active);active=None
    if active and allow_terminal_open and active['number']=='2':
        last=active['segments'][-1]
        if last['system']==len(systems)-1 and last['end']==systems[-1]['bars'][-1]['index']:
            active['end']=last['end'];active['openFinal']=True;spans.append(active);active=None
    if active: raise Decline('unclosed-repeat-ending')
    if len(spans)!=2 or [s['number'] for s in spans]!=['1','2']: raise Decline('requires-one-first-second-ending-pair')
    if spans[0]['end']+1!=spans[1]['start']: raise Decline('nonadjacent-repeat-endings')
    return spans


def recover(source, target, pdf, mapping, *, allow_terminal_open=False):
    root,name=load_mxl(source); parts=root.findall('part')
    if len(parts)!=1: raise Decline('repeat-requires-one-part')
    measures=parts[0].findall('measure')
    if len(measures)!=len(mapping['measures']): raise Decline('repeat-measure-count-mismatch')
    # Other navigation needs a proper separate interpreter; do not guess.
    if root.findall('.//segno') or root.findall('.//coda') or any(any(k in n.attrib for k in ('dacapo','dalsegno','tocoda','fine','segno','coda')) for n in root.findall('.//sound')):
        raise Decline('unsupported-repeat-navigation')
    forward=[];backward=[];old=[]
    for i,m in enumerate(measures):
        for bar in m.findall('barline'):
            for r in bar.findall('repeat'):
                if r.get('times','2')!='2': raise Decline('unsupported-repeat-count')
                if r.get('direction')=='forward' and bar.get('location')=='left': forward.append(i)
                elif r.get('direction')=='backward' and bar.get('location','right')=='right': backward.append(i)
                else: raise Decline('unsupported-repeat-direction')
            old.extend(dict(index=i, location=bar.get('location','right'), **e.attrib) for e in bar.findall('ending'))
    if len(forward)!=1 or len(backward)!=1 or not old: raise Decline('requires-recognized-repeat-and-endings')
    spans=brackets(read_evidence(pdf,mapping),allow_terminal_open=allow_terminal_open);first,second=spans
    if not forward[0] < first['start'] <= first['end']==backward[0] < second['start']:
        raise Decline('printed-endings-disagree-with-repeat')
    original=deepcopy(root)
    for m in measures:
        for bar in m.findall('barline'):
            for e in bar.findall('ending'):bar.remove(e)
    for span in spans:
        for index, location, kind in [(span['start'],'left','start'),(span['end'],'right','stop')]:
            m=measures[index];bars=[b for b in m.findall('barline') if b.get('location','right')==location]
            if len(bars)>1: raise Decline('duplicate-repeat-barline')
            bar=bars[0] if bars else ET.Element('barline',location=location)
            if not bars:
                if location=='left':m.insert(0,bar)
                else:m.append(bar)
            ending=ET.Element('ending',number=span['number'],type=kind)
            repeat=bar.find('repeat')
            bar.insert(list(bar).index(repeat) if repeat is not None else len(bar),ending)
    # Prove no note, direction, repeat or measure content changed.
    def without_endings(r):
        r=deepcopy(r)
        for m in r.findall('part/measure'):
            for b in m.findall('barline'):
                for e in b.findall('ending'):b.remove(e)
                if not len(b):m.remove(b)
        for n in r.iter():n.text=(n.text or '').strip() or None;n.tail=None
        return ET.tostring(r)
    if without_endings(original)!=without_endings(root): raise ValueError('repeat-repair-changed-other-notation')
    write_mxl(source,target,root,name)
    result=deepcopy(mapping); result['musicxmlFiles']=[str(Path(target).resolve())]
    visits=list(range(backward[0]+1))+list(range(forward[0],first['start']))+list(range(second['start'],len(measures)))
    report=dict(version=VERSION, sourceSHA256=mapping['sha256'], originalMusicXMLSHA256=hashlib.sha256(source.read_bytes()).hexdigest(),
        candidateSHA256=hashlib.sha256(target.read_bytes()).hexdigest(), originalEndings=old, printedEndings=spans,
        expectedMeasureVisits=visits, algorithmSHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    result['repeatRecovery']=report
    return result,report
