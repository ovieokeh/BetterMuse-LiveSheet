"""Local experimental G/F-clef repair from vector glyphs and mapped noteheads.

The PDF supplies the clef at each head's horizontal position. The XML supplies
musical timing. Source glyph identity, staff-line alignment, key/accidentals and
old/new pitch agreement must all hold before a correction is accepted.
"""
from collections import defaultdict
from copy import deepcopy
from fractions import Fraction as F
from pathlib import Path
import xml.etree.ElementTree as ET
from livesheet.pdf_rhythm_repair import load_mxl, write_mxl, Decline
from livesheet.pdf_vector_recovery import VectorRecovery, key_alter
from livesheet.score_recognition import read_mxl

VERSION='printed-clef-timeline-v1'
from livesheet.pdf_music_fonts import CLEFS, ACCIDENTALS, SMUFL_FONTS
BASE={('G','2'):30,('F','4'):18}


def timeline(root):
    """Read clef events at musical offsets, independent of XML voice order."""
    events=defaultdict(list);keys={};divisions=F(1);key=0;unsupported=False
    for mi,m in enumerate(root.find('part').findall('measure')):
        cursor=F(0)
        for child in m:
            if child.tag=='attributes':
                if child.findtext('divisions'):divisions=F(child.findtext('divisions'))
                if child.find('transpose') is not None or len(child.findall('key'))>1:unsupported=True
                if child.findtext('key/fifths') is not None:
                    if cursor:unsupported=True
                    key=int(child.findtext('key/fifths'))
                for clef in child.findall('clef'):
                    kind=(clef.findtext('sign'),clef.findtext('line'))
                    if clef.findtext('clef-octave-change','0')!='0':kind=('unsupported','0')
                    events[int(clef.get('number','1'))].append((mi,cursor,kind))
            elif child.tag in ('backup','forward'):cursor+=F(child.findtext('duration'))/divisions*(-1 if child.tag=='backup' else 1)
            elif child.tag=='note' and child.find('chord') is None and child.find('grace') is None:cursor+=F(child.findtext('duration','0'))/divisions
        if unsupported or not -7<=key<=7:raise Decline('unsupported-clef-key-or-transposition')
        keys[mi]=key
    for e in events.values():e.sort(key=lambda x:(x[0],x[1]))
    return events,keys


def active(events,mi,onset):
    matches=[e[2] for e in events if (e[0],e[1])<=(mi,F(str(onset)))]
    if not matches or matches[-1] not in BASE:raise Decline('unsupported-or-missing-xml-clef')
    return matches[-1]


class ClefEvidence:
    def __init__(self,pdf,mapping):
        self.vector=VectorRecovery(pdf,mapping);self.mapping=mapping;self.events=defaultdict(list)
        for pi,page in enumerate(mapping['pages']):
            w,h=self.vector.proof.page_sizes[pi];sx,sy=w/page['width'],h/page['height']
            for symbol in self.vector.symbols[pi]:
                kind=CLEFS.get(symbol['font'],{}).get(symbol['c'])
                if not kind:continue
                matches=[]
                for system in page['systems']:
                    for si,staff in enumerate(system['staves'],1):
                        top=staff['lines'][0][0][1]*sy;bottom=staff['lines'][-1][0][1]*sy;gap=(bottom-top)/4
                        clef_y=bottom-(int(kind[1])-1)*gap
                        if abs(symbol['y']-clef_y)<gap*.25:matches.append((system['id'],si,gap))
                if len(matches)!=1:raise Decline('printed-clef-staff-ambiguous')
                system,staff,gap=matches[0]
                self.events[(pi+1,system,staff)].append(dict(x=symbol['x'],right=symbol['x']+symbol['w'],kind=kind,gap=gap))
        for events in self.events.values():events.sort(key=lambda e:e['x'])

    def pitch_at(self,record,node,key,accidentals):
        bar=self.mapping['measures'][record['measureIndex']];page=self.mapping['pages'][bar['page']-1]
        system=next(s for s in page['systems'] if s['id']==bar['system']);staff=system['staves'][record['staff']-1]
        w,h=self.vector.proof.page_sizes[bar['page']-1];sx,sy=w/page['width'],h/page['height']
        bottom=staff['lines'][-1][0][1]*sy;gap=(bottom-staff['lines'][0][0][1]*sy)/4
        if not record.get('box'):raise Decline('unmapped-clef-note')
        x,y,bw,bh=record['box'];x*=sx;y=(y+bh/2)*sy
        events=[e for e in self.events[(bar['page'],bar['system'],record['staff'])] if e['right']<x+gap*.1]
        if not events:raise Decline('no-printed-clef-before-head')
        event=events[-1]
        if len([e for e in events if abs(e['x']-event['x'])<gap*.2])!=1:raise Decline('duplicate-printed-clef')
        glyphs=[g for g in self.vector.proof.glyphs[bar['page']-1] if abs(g['x']*w-x)<gap*.3 and abs(g['y']*h-y)<gap*.3]
        if len(glyphs)!=1:raise Decline('clef-head-not-unique')
        steps=round((bottom-y)/(gap/2))
        if abs(bottom-y-steps*gap/2)>gap*.22:raise Decline('clef-head-off-staff-step')
        diatonic=BASE[event['kind']]+steps;step='CDEFGAB'[diatonic%7];octave=diatonic//7
        candidates=[s for s in self.vector.symbols[bar['page']-1] if s['c'] in ACCIDENTALS.get(s['font'],{}) and x-3*gap<s['x']<x and abs(s['y']-y)<gap*.25]
        for symbol in self.vector.symbols[bar['page']-1]:
            accidental_range=(0xe10d,0xe118) if symbol['font']=='MScore' else (0xe260,0xe26f) if symbol['font'] in SMUFL_FONTS else None
            if accidental_range and accidental_range[0]<=ord(symbol['c'])<=accidental_range[1] and symbol['c'] not in ACCIDENTALS[symbol['font']] and x-3*gap<symbol['x']<x and abs(symbol['y']-y)<gap*.25:raise Decline('unsupported-printed-accidental')
        if len(candidates)>1:raise Decline('ambiguous-printed-accidental')
        if candidates:
            symbol=candidates[0];alter=ACCIDENTALS[symbol['font']][symbol['c']];accidentals[(record['staff'],step,octave)]=alter
        else:alter=accidentals.get((record['staff'],step,octave),key_alter(step,key))
        return dict(step=step,octave=octave,alter=alter,clef=event['kind'],event=event,steps=steps,explicitAccidental=bool(candidates))


def recover(source,target,pdf,mapping):
    root,name=load_mxl(source)
    if len(root.findall('part'))!=1:raise Decline('clef-requires-one-piano-part')
    try:events,keys=timeline(root);evidence=ClefEvidence(pdf,mapping)
    except Decline as exc:
        write_mxl(source,target,root,name)
        result=deepcopy(mapping);result['musicxmlFiles']=[str(Path(target).resolve())]
        report=dict(version=VERSION,changes=[],insertions=[],declined=str(exc),scan=[],printedClefs=None)
        result['clefRecovery']=report
        return result,report
    measures=root.find('part').findall('measure');proposals=[];scan=[];seen_changes={}
    original=deepcopy(root)
    try:
        for mi,m in enumerate(measures):
            nodes=[n for n in m.findall('note') if n.find('pitch') is not None]
            records=[n for n in mapping['notes'] if n['measureIndex']==mi]
            if len(nodes)!=len(records):raise Decline('clef-mapping-count-mismatch')
            pairs=sorted(zip(nodes,records),key=lambda pair:(pair[1]['box'][0] if pair[1].get('box') else -1,pair[1]['staff']))
            accidental_state={};bar_proposals=[]
            for node,record in pairs:
                old_clef=active(events[record['staff']],mi,record['onset'])
                try:new=evidence.pitch_at(record,node,keys[mi],accidental_state)
                except Decline as exc:
                    scan.append(dict(measureIndex=mi,noteId=record['id'],reason=str(exc)))
                    # Refuse partially repaired scores if uncertainty is in a staff
                    # where an earlier missed clef still has consequences.
                    if record['staff'] in seen_changes:raise
                    continue
                if new['clef']==old_clef:continue
                if node.find('grace') is not None or node.find('time-modification') is not None:raise Decline('clef-grace-or-tuplet')
                pitch=node.find('pitch');actual=(pitch.findtext('step'),int(pitch.findtext('octave')))
                old_step=BASE[old_clef]+new['steps'];old_expected=('CDEFGAB'[old_step%7],old_step//7)
                if actual not in (old_expected,(new['step'],new['octave'])):raise Decline('clef-pitch-not-explained-by-clef-change')
                # A source accidental must agree with its recognized identity if
                # present. Inherited key alteration is recomputed for the new step.
                if node.findtext('accidental') is not None:
                    alterations={'flat':-1,'natural':0,'sharp':1}
                    if not new['explicitAccidental'] or alterations.get(node.findtext('accidental'))!=new['alter']:raise Decline('clef-accidental-conflict')
                bar_proposals.append((node,record,new));seen_changes[record['staff']]=True
            if bar_proposals:
                proof=evidence.vector.proof(mi,nodes)
                if not proof.get('verified'):raise Decline(proof['reason'])
                proposals.extend(bar_proposals)
        # When any source uncertainty could hide a relevant accidental/clef,
        # decline the operation instead of emitting a partly corrected region.
        affected={r['measureIndex'] for _,r,_ in proposals}
        if any(s['measureIndex'] in affected for s in scan):raise Decline('uncertain-head-in-clef-repair-measure')
        changes=[];insertions=[]
        for node,record,new in sorted(proposals,key=lambda p:(p[1]['measureIndex'],p[1]['onset'],p[1]['staff'])):
            mi=record['measureIndex'];staff=record['staff'];pitch=node.find('pitch');before=deepcopy(pitch)
            pitch.find('step').text=new['step'];pitch.find('octave').text=str(new['octave'])
            alter=pitch.find('alter')
            if new['alter']:
                if alter is None:alter=ET.Element('alter');pitch.insert(1,alter)
                alter.text=str(new['alter'])
            elif alter is not None:pitch.remove(alter)
            # Insert a timed attribute only at the start of each newly discovered
            # clef region. An earlier voice may contain the held pre-change note.
            if active(timeline(root)[0][staff],mi,record['onset'])!=new['clef']:
                if node.find('chord') is not None:raise Decline('clef-boundary-inside-chord')
                attributes=ET.Element('attributes');clef=ET.SubElement(attributes,'clef',number=str(staff));ET.SubElement(clef,'sign').text=new['clef'][0];ET.SubElement(clef,'line').text=new['clef'][1]
                measures[mi].insert(list(measures[mi]).index(node),attributes)
                insertions.append(dict(measureIndex=mi,staff=staff,onset=record['onset'],clef=new['clef']))
            old_midi=record['midi'];new_midi=12*(new['octave']+1)+{'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}[new['step']]+new['alter']
            changes.append(dict(noteId=record['id'],measureIndex=mi,staff=staff,onset=record['onset'],before=old_midi,after=new_midi,clef=new['clef']))
        # Verify the inserted attributes have the intended musical offset even
        # when XML serializes a different voice first.
        actual_events,_=timeline(root)
        for c in insertions:
            if (c['measureIndex'],F(str(c['onset'])),c['clef']) not in actual_events[c['staff']]:raise Decline('clef-insertion-offset-mismatch')
        corrected={c['noteId']:c['after'] for c in changes}
        for note in mapping['notes']:
            if note['id'] not in corrected:continue
            for tie in note.get('ties',[]):
                opposite='stop' if tie=='start' else 'start'
                partners=[n for n in mapping['notes'] if opposite in n.get('ties',[]) and n['staff']==note['staff'] and n['voice']==note['voice'] and n['midi']==note['midi'] and
                          abs((n['start'] if tie=='start' else n['start']+n['duration'])-(note['start']+note['duration'] if tie=='start' else note['start']))<.0001]
                if len(partners)!=1 or corrected.get(partners[0]['id'],partners[0]['midi'])!=corrected[note['id']]:raise Decline('clef-repair-would-break-tie')
    except Decline as exc:
        root=original;changes=[];insertions=[];reason=str(exc)
    else:reason=None
    write_mxl(source,target,root,name)
    parsed_measures,parsed=read_mxl(target)
    if len(parsed)!=len(mapping['notes']):raise ValueError('Clef repair changed note count')
    result=deepcopy(mapping)
    for old,new in zip(result['notes'],parsed):
        if any(old[k]!=new[k] for k in ['id','measureIndex','staff','voice','ties']) or any(abs(float(old[k])-float(new[k]))>1e-9 for k in ['onset','duration']):raise ValueError('Clef repair changed event timing/identity')
        for key in ('midi','pitch','diatonic'):old[key]=new[key]
    result['musicxmlFiles']=[str(Path(target).resolve())]
    report=dict(version=VERSION,changes=changes,insertions=insertions,declined=reason,scan=scan,printedClefs=sum(len(v) for v in evidence.events.values()))
    result['clefRecovery']=report
    return result,report
