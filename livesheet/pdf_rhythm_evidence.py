"""Direct PDF glyph checks for the narrow rhythm-repair candidate.

These encodings are restricted to the named fonts inspected in the local corpus.
Unknown fonts, scans, missing or ambiguous heads make the repair decline.
"""
import hashlib
import pymupdf

from livesheet.pdf_music_fonts import HEADS, SMUFL_FONTS, encoded_music_spans



class PDFHeadEvidence:
    def __init__(self,pdf,mapping):
        if hashlib.sha256(pdf.read_bytes()).hexdigest()!=mapping['sha256']:
            raise ValueError('PDF and mapping identity differ')
        self.mapping=mapping
        self.glyphs=[]
        self.symbols=[];self.drawings=[];self.page_sizes=[]
        with pymupdf.open(pdf) as document:
            for page in document:
                heads=[];symbols=[]
                self.page_sizes.append((page.rect.width,page.rect.height))
                self.drawings.append([dict(stroke=d.get('color'),fill=d.get('fill'),width=d.get('width'),items=d['items']) for d in page.get_drawings()])
                for span in encoded_music_spans(page.get_text('rawdict')['blocks']):
                    table=HEADS.get(span['font'],{})
                    for ch in span['chars']:
                        rests={'MScore':{'\ue109':'eighth-rest','\ue101':'half-rest'},**{f:{'\ue4e6':'eighth-rest','\ue4e4':'half-rest'} for f in SMUFL_FONTS}}.get(span['font'],{})
                        if ch['c'] in rests:
                            symbols.append(dict(x=ch['origin'][0],y=ch['origin'][1],kind=rests[ch['c']]))
                        if ch['c'] in table:
                            heads.append(dict(x=ch['origin'][0]/page.rect.width,y=ch['origin'][1]/page.rect.height,
                                              kind=table[ch['c']],font=span['font']))
                self.glyphs.append(heads)
                self.symbols.append(symbols)

    def __call__(self,index,nodes):
        m=self.mapping['measures'][index]
        page=self.mapping['pages'][m['page']-1]
        systems=page['systems']
        si=next((i for i,s in enumerate(systems) if s['id']==m['system']),None)
        if si is None:return dict(verified=False,reason='missing-system')
        def bounds(s):
            ys=[p[1] for staff in s['staves'] for line in staff['lines'] for p in line]
            return min(ys),max(ys)
        top,bottom=bounds(systems[si])
        low=(bounds(systems[si-1])[1]+top)/2 if si else 0
        high=(bottom+bounds(systems[si+1])[0])/2 if si+1<len(systems) else page['height']
        glyphs=[g for g in self.glyphs[m['page']-1] if m['left']/page['width']<=g['x']<m['right']/page['width'] and low/page['height']<=g['y']<high/page['height']]
        notes=[n for n in self.mapping['notes'] if n['measureIndex']==index]
        if len(glyphs)!=len(nodes) or len(notes)!=len(nodes):
            return dict(verified=False,reason='printed-head-count-mismatch',printed=len(glyphs),recognized=len(nodes))
        used=set()
        # About one fifth of the staff-line gap, in normalized page units.
        gap=(bottom-top)/max(1,len(systems[si]['staves'])*4) # only an upper cap; notehead height tightens it
        for note,node in zip(notes,nodes):
            if not note.get('box'):return dict(verified=False,reason='unmapped-note')
            x,y,w,h=note['box']
            kind={'whole':'whole','half':'half'}.get(node.findtext('type'),'filled')
            tolerance=min(gap*.2,h*.25)
            matches=[i for i,g in enumerate(glyphs) if g['kind']==kind and abs(g['x']*page['width']-x)<=tolerance and abs(g['y']*page['height']-(y+h/2))<=tolerance]
            if len(matches)!=1 or matches[0] in used:return dict(verified=False,reason='ambiguous-or-missing-printed-head')
            used.add(matches[0])
        return dict(verified=True,printedHeads=len(glyphs),matchedHeads=len(used),fonts=sorted({g['font'] for g in glyphs}),
                    scope='Direct notehead identity and position only. Durations and stems remain recognition evidence; no tie reconstruction.')

    def verify_whole_lanes(self,index,groups):
        """Confirm the new whole-bass rule using original rest, stems and beam."""
        m=self.mapping['measures'][index];page=self.mapping['pages'][m['page']-1]
        system=next(s for s in page['systems'] if s['id']==m['system'])
        width,height=self.page_sizes[m['page']-1]
        sx,sy=width/page['width'],height/page['height']
        staves=system['staves']
        if len(staves)!=2:return dict(verified=False,reason='printed-rhythm-requires-two-staves')
        gap=abs(staves[1]['lines'][-1][0][1]-staves[1]['lines'][0][0][1])*sy/4
        nodes=[n for g in groups for n in g['nodes'] if n.find('pitch') is not None]
        notes=[n for n in self.mapping['notes'] if n['measureIndex']==index]
        heads={id(n):dict(x=v['box'][0]*sx,y=(v['box'][1]+v['box'][3]/2)*sy,w=v['box'][2]*sx)
               for n,v in zip(nodes,notes)}
        upper=sorted((g for g in groups if g['staff']=='1'),key=lambda g:g['x'])
        bass=next(g for g in groups if g['stem']=='whole')
        moving=sorted((g for g in groups if g['staff']=='2' and g['stem']=='up'),key=lambda g:g['x'])
        a=heads[id(upper[0]['nodes'][0])];b=heads[id(bass['nodes'][0])]
        if upper[0].get('newOnset')==2:
            rests=[s for s in self.symbols[m['page']-1] if s['kind']=='half-rest' and abs(s['x']-(b['x']+b['w']/2))<gap and staves[0]['lines'][0][0][1]*sy-gap<s['y']<staves[0]['lines'][-1][0][1]*sy+gap]
            if len(rests)!=1:return dict(verified=False,reason='missing-upper-printed-half-rest')
            a=b
        if abs(a['x']+a['w']/2-b['x']-b['w']/2)>gap*.4:
            return dict(verified=False,reason='printed-start-columns-disagree')
        low=staves[1]['lines'][0][0][1]*sy-3*gap
        high=staves[1]['lines'][-1][0][1]*sy+gap
        rests=[s for s in self.symbols[m['page']-1] if s['kind']=='eighth-rest' and abs(s['x']-a['x'])<gap*.6 and low<s['y']<high]
        if len(rests)!=1:return dict(verified=False,reason='missing-or-ambiguous-printed-eighth-rest')
        drawings=self.drawings[m['page']-1]
        vertical=[]
        for d in drawings:
            if not d.get('stroke') or not 0<float(d.get('width') or 0)<gap*.3:continue
            for item in d['items']:
                if item[0]=='l':
                    p,q=item[1:]
                    if abs(p[0]-q[0])<gap*.05 and 2*gap<abs(p[1]-q[1])<10*gap:
                        vertical.append((p[0],min(p[1],q[1]),max(p[1],q[1])))
        stems={}
        for g in upper+moving:
            hs=[heads[id(n)] for n in g['nodes']]
            up=g['stem']=='up'; x=hs[0]['x']+(hs[0]['w'] if up else 0)
            yy=max(h['y'] for h in hs) if up else min(h['y'] for h in hs)
            found=[v for v in vertical if abs(v[0]-x)<gap*.3 and abs((v[2] if up else v[1])-yy)<gap*.35
                   and all(v[1]-gap*.4<h['y']<v[2]+gap*.4 for h in hs)]
            if len(found)!=1:return dict(verified=False,reason='missing-or-ambiguous-printed-stem')
            stems[id(g)]=found[0]
        tips=[(stems[id(g)][0],stems[id(g)][1]) for g in moving[:3]]
        beams=[]
        for d in drawings:
            if not d.get('fill') or len(d['items'])!=4 or any(i[0]!='l' for i in d['items']):continue
            edges=[i[1:] for i in d['items'] if abs(i[2][0]-i[1][0])>gap*2]
            if len(edges)!=2:continue
            left=min(point[0] for edge in edges for point in edge)
            right=max(point[0] for edge in edges for point in edge)
            covered=[(x,y) for x,y in tips if left-gap*.25<=x<=right+gap*.25]
            if len(covered)<2:continue
            if abs(left-covered[0][0])>gap*.25 or abs(right-covered[-1][0])>gap*.25:continue
            heights=[];at_tip=True
            for x,y in covered:
                ys=[p[1]+(q[1]-p[1])*(x-p[0])/(q[0]-p[0]) for p,q in edges]
                # Count lower/partial secondary beams too, even though the stem
                # tip touches only the top beam. Two beams must not pass as eighths.
                if not y-gap*.4<=sum(ys)/2<=y+gap*2:break
                heights.append(abs(ys[1]-ys[0]))
                at_tip &= min(ys)-gap*.1<=y<=max(ys)+gap*.1
            if len(heights)==len(covered) and all(gap*.35<h<gap*.65 for h in heights):
                beams.append(dict(primary=at_tip and len(covered)==3))
        if len(beams)!=1 or not beams[0]['primary']:
            return dict(verified=False,reason='missing-or-ambiguous-single-eighth-beam')
        return dict(verified=True,printedEighthRest=1,printedStems=len(stems),printedEighthBeams=1,
                    scope='Direct glyph/rest, notehead alignment, stem and single-beam checks. No tie recovery.')
