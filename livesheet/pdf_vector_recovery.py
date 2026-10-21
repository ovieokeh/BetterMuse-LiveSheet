"""Experimental local recovery for one tightly specified, printed piano pattern.

Only known MScore glyphs, standard G/F clefs, 4/4, one whole bass and an
independently verified eighth-rest/three-eighth/half moving bass lane. No reference
answers, titles or measure numbers are used. Unknown evidence declines a bar.
"""
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import xml.etree.ElementTree as ET
import pymupdf
from livesheet.pdf_rhythm_evidence import PDFHeadEvidence
from livesheet.pdf_rhythm_repair import Decline, load_mxl, write_mxl, rebuild
from livesheet.score_recognition import read_mxl

VERSION='printed-whole-bass-v2'
HEADS={'\ue12b':'whole','\ue12c':'half','\ue12d':'filled'}


def key_alter(step,fifths):
    order='FCGDAEB' if fifths>=0 else 'BEADGCF'
    return (1 if fifths>=0 else -1) if step in order[:abs(fifths)] else 0


def pitch(n):
    p=n.find('pitch')
    return p.findtext('step'),int(p.findtext('octave')),int(p.findtext('alter','0'))


def verticals(drawings,gap):
    result=[]
    for d in drawings:
        if not d.get('stroke') or not 0<float(d.get('width') or 0)<gap*.3:continue
        for item in d['items']:
            if item[0]=='l':
                a,b=item[1:]
                if abs(a[0]-b[0])<gap*.05 and 2*gap<abs(a[1]-b[1])<10*gap:
                    result.append((a[0],min(a[1],b[1]),max(a[1],b[1])))
    return result


def printed_tie(drawings,a,b,gap):
    """Short, level, closed two-cubic lens attached to equal-pitch heads."""
    matches=[]
    for d in drawings:
        items=d['items']
        if not d.get('fill') or len(items)!=2 or any(i[0]!='c' for i in items):continue
        q=list(items[0][1:]);other=list(items[1][1:])
        if q[0][0]>q[-1][0]:q.reverse()
        x0,y0=q[0];x1,y1=q[-1]
        if not gap*.5<x1-x0<gap*5 or abs(y1-y0)>gap*.12:continue
        # Engravers attach ties either above head centres or just outside the
        # head edges. Both endpoints must remain in their respective head zones.
        if not a['x']+a['w']*.45<=x0<=a['x']+a['w']+gap*.45:continue
        if not b['x']-gap*.45<=x1<=b['x']+b['w']*.55:continue
        if abs(a['y']-b['y'])>gap*.2 or not gap*.2<abs(y0-a['y'])<gap*.8:continue
        if not all(min(x0,x1)-gap*.1<=p[0]<=max(x0,x1)+gap*.1 for p in q+other):continue
        if any(abs(a[k]-b[k])>gap*.05 for a,b in [(other[0],items[0][-1]),(other[-1],items[0][1])] for k in (0,1)):continue
        arches=[p[1]-y0 for p in q[1:3]+other[1:3]]
        side=1 if y0>a['y'] else -1
        if not all(gap*.08<side*v<gap*1.4 for v in arches):continue
        matches.append(d)
    return len(matches)==1


def beam_count(drawings,stem,direction,gap):
    """Count complete printed beam polygons touching this stem, including secondary beams."""
    x,y0,y1=stem;tip=y0 if direction=='up' else y1;side=1 if direction=='up' else -1
    distances=[]
    for d in drawings:
        # A white page-background polygon is not a musical beam.
        if d.get('fill') and all(c>=.99 for c in d['fill']):continue
        if not d.get('fill') or len(d['items'])!=4 or any(i[0]!='l' for i in d['items']):continue
        edges=[i[1:] for i in d['items'] if abs(i[2][0]-i[1][0])>gap*2]
        if len(edges)!=2:continue
        if not all(min(a[0],b[0])-gap*.25<=x<=max(a[0],b[0])+gap*.25 for a,b in edges):continue
        ys=[a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]) for a,b in edges]
        distance=side*(sum(ys)/2-tip)
        if -gap*.4<=distance<=gap*2:
            if not gap*.35<abs(ys[1]-ys[0])<gap*.65:raise Decline('ambiguous-upper-beam-thickness')
            distances.append(distance)
    distances.sort()
    if distances and not -gap*.1<distances[0]<gap*.6:raise Decline('missing-primary-upper-beam')
    if len(distances)>2 or any(not gap*.5<b-a<gap for a,b in zip(distances,distances[1:])):raise Decline('ambiguous-upper-beams')
    return len(distances)


class VectorRecovery:
    def __init__(self,pdf,mapping):
        self.proof=PDFHeadEvidence(pdf,mapping);self.mapping=mapping
        self.symbols=[]
        with pymupdf.open(pdf) as doc:
            for page in doc:
                chars=[]
                from livesheet.pdf_music_fonts import encoded_music_spans
                for span in encoded_music_spans(page.get_text('rawdict')['blocks']):
                    for c in span['chars']:
                        chars.append(dict(c=c['c'],font=span['font'],x=c['origin'][0],y=c['origin'][1],w=c['bbox'][2]-c['bbox'][0]))
                self.symbols.append(chars)

    def recover_ties(self,index,measure):
        """Recover only within-voice, within-bar ties; leave all event timing intact."""
        from livesheet.pdf_rhythm_evidence import HEADS as FONT_HEADS
        replacement=deepcopy(measure)
        nodes=[n for n in replacement.findall('note') if n.find('pitch') is not None]
        records=[n for n in self.mapping['notes'] if n['measureIndex']==index]
        proof=self.proof(index,nodes)
        if not proof.get('verified'):raise Decline(proof['reason'])
        m=self.mapping['measures'][index];page=self.mapping['pages'][m['page']-1]
        system=next(s for s in page['systems'] if s['id']==m['system'])
        w,h=self.proof.page_sizes[m['page']-1];sx,sy=w/page['width'],h/page['height']
        pairs=[]
        for n,rec in zip(nodes,records):
            if n.find('grace') is not None or n.find('time-modification') is not None:raise Decline('tie-grace-or-tuplet')
            x,y,bw,bh=rec['box'];staff=int(n.findtext('staff','1'))-1
            lines=system['staves'][staff]['lines'];gap=(lines[-1][0][1]-lines[0][0][1])*sy/4
            glyphs=[g for g in self.symbols[m['page']-1] if g['c'] in FONT_HEADS.get(g['font'],{}) and abs(g['x']-x*sx)<gap*.35 and abs(g['y']-(y+bh/2)*sy)<gap*.3]
            if len(glyphs)!=1:raise Decline('tie-head-ambiguous')
            pairs.append(dict(node=n,record=rec,glyph=glyphs[0],gap=gap))
        changes=[]
        for a in pairs:
            n=a['node'];rec=a['record']
            if n.findall('tie') or n.find('notations/tied') is not None:continue
            matches=[b for b in pairs if b is not a and b['record']['voice']==rec['voice'] and b['record']['staff']==rec['staff'] and pitch(b['node'])==pitch(n) and F(str(b['record']['onset']))==F(str(rec['onset']))+F(str(rec['duration']))]
            if len(matches)!=1:continue
            b=matches[0]
            if b['node'].findall('tie') or b['node'].find('notations/tied') is not None:continue
            # A slur already interpreted on either endpoint needs separate review.
            if any(v['node'].find('notations/slur') is not None for v in (a,b)):continue
            if not printed_tie(self.proof.drawings[m['page']-1],a['glyph'],b['glyph'],a['gap']):continue
            for v,kind in [(a,'start'),(b,'stop')]:
                node=v['node'];duration=node.find('duration')
                node.insert(list(node).index(duration)+1,ET.Element('tie',type=kind))
                nt=node.find('notations')
                if nt is None:
                    nt=ET.Element('notations');position=next((i for i,c in enumerate(node) if c.tag in ('lyric','play','listen')),len(node));node.insert(position,nt)
                ET.SubElement(nt,'tied',type=kind)
            changes.append(dict(pitch=pitch(n),staff=n.findtext('staff','1'),onset=str(rec['onset'])))
        if not changes:raise Decline('no-source-proven-missing-ties')
        return replacement,records,dict(addedNotes=0,tieChanges=changes,heads=proof,scope='Tie-only: unchanged pitches, durations, voices and onsets')

    def recover(self,index,measure,divisions,fifths,clefs):
        if clefs!={'1':('G','2'),'2':('F','4')} or not -7<=fifths<=7:raise Decline('unsupported-clefs-or-key')
        if any(n.find('grace') is not None or n.find('time-modification') is not None for n in measure.findall('note')):
            raise Decline('grace-or-tuplet')
        seen=False
        for n in measure:
            if n.tag=='note':seen=True
            elif seen and n.tag not in ('backup','forward','barline'):raise Decline('mid-bar-metadata')
        m=self.mapping['measures'][index];page=self.mapping['pages'][m['page']-1]
        system=next(s for s in page['systems'] if s['id']==m['system']);staves=system['staves']
        if len(staves)!=2:raise Decline('not-two-staves')
        w,h=self.proof.page_sizes[m['page']-1];sx,sy=w/page['width'],h/page['height']
        bounds=[(s['lines'][0][0][1]*sy,s['lines'][-1][0][1]*sy) for s in staves]
        gap=(bounds[1][1]-bounds[1][0])/4;middle=(bounds[0][1]+bounds[1][0])/2
        symbols=[s for s in self.symbols[m['page']-1] if m['left']*sx<s['x']<m['right']*sx and bounds[0][0]-6*gap<s['y']<bounds[1][1]+5*gap]
        glyphs=[dict(s,kind=HEADS[s['c']],staff='1' if s['y']<middle else '2') for s in symbols if s['font']=='MScore' and s['c'] in HEADS]
        if not glyphs:raise Decline('unsupported-font-or-no-heads')
        originals=[n for n in measure.findall('note') if n.find('pitch') is not None]
        mapped=[n for n in self.mapping['notes'] if n['measureIndex']==index]
        if len(originals)!=len(mapped):raise Decline('mapping-count-mismatch')
        used=set()
        for node,record in zip(originals,mapped):
            if not record.get('box'):raise Decline('unmapped-original')
            x,y,bw,bh=record['box'];x*=sx;y=(y+bh/2)*sy
            found=[i for i,g in enumerate(glyphs) if abs(g['x']-x)<gap*.35 and abs(g['y']-y)<gap*.3]
            if len(found)!=1 or found[0] in used:raise Decline('ambiguous-original-head')
            g=glyphs[found[0]];used.add(found[0])
            if g['staff']!=node.findtext('staff','1'):raise Decline('cross-staff-or-assignment-conflict')
            g.update(node=deepcopy(node),record=deepcopy(record),new=False)
        # A recovery is permitted only where OMR already detected each head.
        # The PDF supplies its identity; existing OMR boxes retain exact mapping.
        for i,g in enumerate(glyphs):
            if i in used:continue
            found=[v for v in page['heads'] if not v.get('mapped') and abs(v['box'][0]*sx-g['x'])<gap*.35 and abs((v['box'][1]+v['box'][3]/2)*sy-g['y'])<gap*.3]
            if len(found)!=1:raise Decline('new-head-has-no-unique-unused-geometry')
            g.update(new=True,record=dict(page=m['page'],system=m['system'],omrHeadID=found[0]['id'],box=found[0]['box'],normalizedBox=[found[0]['box'][0]/page['width'],found[0]['box'][1]/page['height'],found[0]['box'][2]/page['width'],found[0]['box'][3]/page['height']],mapping='printed-head+unused-OMR-geometry'))
        bass=[g for g in glyphs if g['staff']=='2' and g['kind']=='whole']
        moving=sorted((g for g in glyphs if g['staff']=='2' and g['kind']!='whole'),key=lambda g:g['x'])
        upper=[g for g in glyphs if g['staff']=='1']
        if len(bass)!=1 or bass[0]['new'] or [g['kind'] for g in moving]!=['filled','filled','filled','half'] or not upper:
            raise Decline('not-whole-bass-moving-pattern')
        drawings=self.proof.drawings[m['page']-1];lines=verticals(drawings,gap)
        for g in glyphs:
            bottom=bounds[int(g['staff'])-1][1];steps=round((bottom-g['y'])/(gap/2))
            if abs(bottom-g['y']-steps*gap/2)>gap*.22:raise Decline('head-not-on-staff-step')
            diatonic=(30 if g['staff']=='1' else 18)+steps;step='CDEFGAB'[diatonic%7];octave=diatonic//7
            if not g['new']:
                if pitch(g['node'])[:2]!=(step,octave):raise Decline('printed-pitch-conflicts-with-recognition')
            else:
                # Any local accidental or unclassified music glyph near a new
                # head needs a broader accidental reader. Do not guess through it.
                allowed=set(HEADS)|{'\ue127'}
                if any(s['font']=='MScore' and s['c'] not in allowed and g['x']-3*gap<s['x']<g['x'] and abs(s['y']-g['y'])<gap for s in symbols):
                    raise Decline('new-note-near-unresolved-accidental')
                alter=key_alter(step,fifths)
                prior=[v for v in glyphs if not v['new'] and v['staff']==g['staff'] and v['x']<g['x'] and pitch(v['node'])[:2]==(step,octave)]
                if prior:alter=pitch(max(prior,key=lambda v:v['x'])['node'])[2]
                n=ET.Element('note',{'default-x':str(round(g['x']/gap*10,3))});p=ET.SubElement(n,'pitch');ET.SubElement(p,'step').text=step
                if alter:ET.SubElement(p,'alter').text=str(alter)
                ET.SubElement(p,'octave').text=str(octave);g['node']=n
            if g['kind']=='whole':g.update(stem='whole',stemIndex=None);continue
            possible=[]
            for li,(x,y0,y1) in enumerate(lines):
                # Displaced chord seconds can sit on either side of the stem.
                if y0-gap*.3<=g['y']<=y1+gap*.3:
                    if min(abs(x-(g['x']+g['w'])),abs(x-g['x']))<gap*.3:
                        if y1-g['y']<gap*.35 and g['y']-y0>gap*2:possible.append((li,'up'))
                        if g['y']-y0<gap*.35 and y1-g['y']>gap*2:possible.append((li,'down'))
                        # Interior chord heads use the recognized stem only.
                        if not g['new']:
                            direction=g['node'].findtext('stem')
                            if direction=='up' and y1>=g['y'] and y0<g['y']-2*gap:possible.append((li,'up'))
                            if direction=='down' and y0<=g['y'] and y1>g['y']+2*gap:possible.append((li,'down'))
            possible=set(possible)
            if len(possible)!=1:raise Decline('ambiguous-source-stem')
            g['stemIndex'],g['stem']=possible.pop()
        # Eighths in the moving lane are verified by the common beam below.
        for g,dur in zip(moving,[F(1,2),F(1,2),F(1,2),F(2)]):g['duration']=dur
        bass[0]['duration']=F(4)
        for g in upper:
            if g['kind'] not in ('filled','half'):raise Decline('unsupported-upper-head')
            bottom=bounds[0][1];staff_step=round((bottom-g['y'])/(gap/2))
            dot_y=g['y']-gap/2 if staff_step%2==0 else g['y']
            dots=[s for s in symbols if s['font']=='MScore' and s['c']=='\ue127' and g['x']+g['w']-.1*gap<s['x']<g['x']+g['w']+gap and abs(s['y']-dot_y)<gap*.22]
            if len(dots)>1:raise Decline('ambiguous-dots')
            g['duration']=F(2 if g['kind']=='half' else 1)*(F(3,2) if dots else 1)
            if g['kind']=='filled':
                x,y0,y1=lines[g['stemIndex']];tip=y0 if g['stem']=='up' else y1
                if any(s['font']=='MScore' and s['c'] not in HEADS and abs(s['x']-x)<gap*2 and abs(s['y']-tip)<gap for s in symbols):raise Decline('upper-flag-or-unclassified-symbol')
                g['duration']/=2**beam_count(drawings,lines[g['stemIndex']],g['stem'],gap)
        groups=[]
        buckets=defaultdict(list)
        for g in upper:buckets[g['stemIndex']].append(g)
        for values in buckets.values():
            if len({g['duration'] for g in values})!=1:raise Decline('mixed-chord-durations')
            groups.append(dict(staff='1',stem=values[0]['stem'],duration=values[0]['duration'],x=sum(g['x']+g['w']/2 for g in values)/len(values),glyphs=values))
        for g in bass+moving:groups.append(dict(staff='2',stem=g['stem'],duration=g['duration'],x=g['x']+g['w']/2,glyphs=[g]))
        lanes=defaultdict(list)
        upper_groups=sorted((g for g in groups if g['staff']=='1'),key=lambda g:g['x'])
        single_upper=sum(g['duration'] for g in upper_groups)==4 and all(b['x']-a['x']>gap for a,b in zip(upper_groups,upper_groups[1:]))
        for g in groups:lanes[(g['staff'],'up' if g['staff']=='1' and single_upper else g['stem'])].append(g)
        if set(k for k in lanes if k[0]=='2')!={('2','whole'),('2','up')}:raise Decline('wrong-lower-stems')
        startx=bass[0]['x']+bass[0]['w']/2
        upper_rests=[s for s in symbols if s['font']=='MScore' and s['c'] in ('\ue101','\ue107') and bounds[0][0]-gap<s['y']<bounds[0][1]+2*gap]
        for (staff,stem),lane in lanes.items():
            lane.sort(key=lambda g:g['x']);cursor=F(1,2) if staff=='2' and stem=='up' else F(0)
            if staff=='1' and abs(lane[0]['x']-startx)>gap*.5:
                rests=[s for s in upper_rests if s['c'] in ('\ue101','\ue107') and abs(s['x']-startx)<gap]
                if len(rests)!=1:raise Decline('missing-upper-start-anchor')
                cursor=F(2 if rests[0]['c']=='\ue101' else 1)
            for g in lane:g['newOnset']=cursor;cursor+=g['duration']
            if cursor>4:raise Decline('printed-lane-overfull')
            if cursor<4:
                if cursor!=3 or not any(s['c']=='\ue107' and s['x']>lane[-1]['x']+gap for s in upper_rests):raise Decline('unsupported-lane-gap')
        for a in groups:
            for b in groups:
                dx=b['x']-a['x']
                if abs(dx)<gap*.4 and a['newOnset']!=b['newOnset']:raise Decline('printed-column-conflict')
                if dx>gap*.7 and b['newOnset']<=a['newOnset']:raise Decline('printed-order-conflict')
        for g in groups:
            for j,v in enumerate(g['glyphs']):
                n=v['node'];dur=g['duration']
                if not v['new'] and F(n.findtext('duration'))/divisions!=dur:raise Decline('printed-duration-conflicts-with-existing-note')
                for tag in ['chord','duration','voice','type','dot','stem','staff']:
                    for old in n.findall(tag):n.remove(old)
                if j:ET.SubElement(n,'chord')
                if (dur*divisions).denominator!=1:raise Decline('unrepresentable-duration')
                ET.SubElement(n,'duration').text=str(int(dur*divisions));ET.SubElement(n,'voice').text='1'
                kind={F(1,4):'16th',F(1,2):'eighth',F(1):'quarter',F(2):'half',F(3):'half',F(4):'whole'}[dur]
                ET.SubElement(n,'type').text=kind
                if dur==3:ET.SubElement(n,'dot')
                if g['stem']!='whole':ET.SubElement(n,'stem').text=g['stem']
                ET.SubElement(n,'staff').text=g['staff']
            g['nodes']=[v['node'] for v in g['glyphs']];g['rest']=False
        # Feed original-page proof with the new note/box pairing, not a guessed
        # ordinal association to the old incomplete recognition list.
        pairs=[(v['node'],v['record']) for g in groups for v in g['glyphs']]
        proof=deepcopy(self.proof)
        proof.mapping=deepcopy(self.mapping)
        proof.mapping['notes']=[dict(rec,measureIndex=index) for n,rec in pairs]
        heads=proof(index,[n for n,rec in pairs])
        if not heads.get('verified'):raise Decline(heads['reason'])
        rhythm=proof.verify_whole_lanes(index,groups)
        if not rhythm.get('verified'):raise Decline(rhythm['reason'])
        # Preserve every existing tie. New/recovered ties need an unambiguous
        # short printed curve, equal pitch and adjacent positions in one lane.
        tie_changes=[]
        for lane in lanes.values():
            for a,b in zip(lane,lane[1:]):
                if a['newOnset']+a['duration']!=b['newOnset']:continue
                for va in a['glyphs']:
                    for vb in b['glyphs']:
                        if pitch(va['node'])!=pitch(vb['node']) or not printed_tie(drawings,va,vb,gap):continue
                        changed=False
                        for v,kind in [(va,'start'),(vb,'stop')]:
                            if not any(t.get('type')==kind for t in v['node'].findall('tie')):
                                ET.SubElement(v['node'],'tie',type=kind)
                                nt=v['node'].find('notations')
                                if nt is None:nt=ET.SubElement(v['node'],'notations')
                                ET.SubElement(nt,'tied',type=kind);changed=True
                        if changed:tie_changes.append(dict(pitch=pitch(va['node']),staff=a['staff'],onset=str(a['newOnset'])))
        # Voice renumbering must never break ties crossing the measure boundary.
        for g in groups:
            for v in g['glyphs']:
                for t in v['node'].findall('tie'):
                    kind=t.get('type');opposite='stop' if kind=='start' else 'start'
                    matches=[h for h in groups if h['staff']==g['staff'] and
                        (h['newOnset']==g['newOnset']+g['duration'] if kind=='start' else h['newOnset']+h['duration']==g['newOnset']) and
                        any(pitch(u['node'])==pitch(v['node']) and any(z.get('type')==opposite for z in u['node'].findall('tie')) for u in h['glyphs'])]
                    if len(matches)!=1:raise Decline('unresolved-or-cross-bar-tie')
        # Keep the MusicXML child order valid after adding notation fields.
        order={tag:i for i,tag in enumerate(['grace','cue','chord','pitch','unpitched','rest','duration','tie','instrument','voice','type','dot','accidental','time-modification','stem','notehead','notehead-text','staff','beam','notations','lyric','play','listen'])}
        for g in groups:
            for v in g['glyphs']:
                n=v['node']
                if any(c.tag not in order for c in n):raise Decline('unsupported-note-metadata')
                n[:]=sorted(n,key=lambda c:order[c.tag])
        before=[(v['record'].get('onset'),str(g['newOnset'])) for g in groups for v in g['glyphs'] if not v['new']]
        if not any(v['new'] for g in groups for v in g['glyphs']) and not tie_changes and all(F(str(a))==F(b) for a,b in before):raise Decline('no-change')
        rebuilt=deepcopy(measure)
        rebuild(rebuilt,lanes,divisions)
        # The nodes appended by rebuild are the recovered nodes; link geometry by
        # identity before the XML serializer removes Python object identities.
        records={id(v['node']):v['record'] for g in groups for v in g['glyphs']}
        ordered=[records[id(n)] for n in rebuilt.findall('note') if n.find('pitch') is not None]
        return rebuilt,ordered,dict(addedNotes=sum(v['new'] for v in glyphs),tieChanges=tie_changes,heads=heads,rhythm=rhythm)


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source);parts=root.findall('part')
    if len(parts)!=1:raise ValueError('One piano part required')
    engine=VectorRecovery(pdf,mapping);report=dict(version=VERSION,accepted=[],declined=[]);origins=[]
    divisions=F(1);fifths=0;clefs={};meter=None;unsupported_state=False
    for i,m in enumerate(list(parts[0])):
        if m.tag!='measure':raise ValueError('Unexpected part content')
        a=m.find('attributes')
        if a is not None:
            if a.find('transpose') is not None or len(a.findall('key'))>1 or len(a.findall('time'))>1 or any(c.findtext('clef-octave-change','0')!='0' for c in a.findall('clef')):unsupported_state=True
            if a.findtext('divisions'):divisions=F(a.findtext('divisions'))
            if a.findtext('key/fifths'):fifths=int(a.findtext('key/fifths'))
            if a.find('time') is not None:meter=(a.findtext('time/beats'),a.findtext('time/beat-type'))
            for c in a.findall('clef'):clefs[c.get('number','1')]=(c.findtext('sign'),c.findtext('line'))
        try:
            if unsupported_state:raise Decline('unsupported-transposition-or-staff-signatures')
            if meter!=('4','4') or m.get('implicit')=='yes':raise Decline('unsupported-meter-or-pickup')
            try:
                replacement,records,evidence=engine.recover(i,m,divisions,fifths,clefs)
            except Decline as pattern_error:
                try:replacement,records,evidence=engine.recover_ties(i,m)
                except Decline:raise pattern_error
            parts[0].remove(m);parts[0].insert(i,replacement);origins.extend(records)
            report['accepted'].append(dict(measureIndex=i,label=m.get('number'),**evidence))
        except Decline as e:
            origins.extend(n for n in mapping['notes'] if n['measureIndex']==i)
            report['declined'].append(dict(measureIndex=i,reason=str(e)))
    write_mxl(source,target,root,name)
    measures,notes=read_mxl(target)
    if len(notes)!=len(origins) or len(measures)!=len(mapping['measures']):raise ValueError('Recovery mapping count mismatch')
    result=deepcopy(mapping)
    for old,new in zip(origins,notes):
        geometry={k:deepcopy(v) for k,v in old.items() if k in ('page','system','omrHeadID','box','normalizedBox','mapping','recognitionGrade','recognitionOriginalID','pdfGlyphID')}
        new.update(geometry)
        new['onset']=float(new['onset'])
    for old,new in zip(result['measures'],measures):old.update(start=new['start'],duration=new['duration'])
    for page in result['pages']:
        assigned={n['omrHeadID'] for n in notes if n.get('page')==page['number'] and n.get('omrHeadID')}
        page['mappedNotes']=sum(n.get('page')==page['number'] for n in notes)
        for head in page['heads']:head['mapped']=head['id'] in assigned
    result['summary'].update(xmlPitchedNotes=len(notes),mappedNotes=sum(bool(n.get('box')) for n in notes),unmatchedXmlNotes=sum(not bool(n.get('box')) for n in notes),unusedOmrHeads=sum(not h['mapped'] for p in result['pages'] for h in p['heads']))
    result.update(notes=notes,musicxmlFiles=[str(Path(target).resolve())],vectorRecovery=report)
    return result,report
