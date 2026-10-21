"""Conservative candidate repair for overfull, incorrectly merged piano voices.

A complete single-stem staff supplies a rhythm; opposite-stem lanes must agree
with shared horizontal attacks and their unchanged durations. No interpolation
from engraving spacing. Direct PDF notehead evidence is required by the caller.
This module never selects a resolution or reads a reference answer.
"""
from fractions import Fraction as F
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

VERSION = 'anchored-stem-lanes-v2'


class Decline(ValueError):
    pass


def groups_for(measure, divisions):
    groups=[]
    cursor=F(0)
    for node in measure:
        if node.tag in ('forward','backup'):
            cursor += F(node.findtext('duration'))/divisions*(1 if node.tag=='forward' else -1)
        elif node.tag=='note':
            if node.find('grace') is not None or node.find('time-modification') is not None:
                raise Decline('grace-or-tuplet')
            if node.get('print-object')=='no' or node.find('unpitched') is not None:
                raise Decline('hidden-or-unpitched-note')
            if node.find('duration') is None or node.get('default-x') is None:
                raise Decline('missing-duration-or-position')
            duration=F(node.findtext('duration'))/divisions
            if duration<=0: raise Decline('nonpositive-duration')
            written={'whole':F(4),'half':F(2),'quarter':F(1),'eighth':F(1,2),
                     '16th':F(1,4),'32nd':F(1,8),'64th':F(1,16)}.get(node.findtext('type'))
            if written is None or written*sum((F(1,2**i) for i in range(len(node.findall('dot'))+1)),F(0))!=duration:
                raise Decline('written-duration-conflict')
            staff=node.findtext('staff','1')
            rest=node.find('rest') is not None
            stem=node.findtext('stem')
            if not rest and node.findtext('type')=='whole' and stem in (None,'none'):
                stem='whole'
            if not rest and (node.find('pitch') is None or stem not in ('up','down','whole')):
                raise Decline('missing-pitch-or-stem')
            if node.find('chord') is not None:
                if not groups: raise Decline('orphan-chord')
                g=groups[-1]
                if rest or g['rest'] or (g['staff'],g['stem'],g['duration'])!=(staff,stem,duration) or abs(g['x']-float(node.get('default-x')))>2:
                    raise Decline('ambiguous-chord')
                g['nodes'].append(node)
            else:
                groups.append(dict(nodes=[node],staff=staff,stem=stem,rest=rest,duration=duration,
                                   x=float(node.get('default-x')),oldOnset=cursor))
                cursor+=duration
    return groups


def solve(measure, divisions, budget):
    groups=groups_for(measure,divisions)
    if not groups or max(g['oldOnset']+g['duration'] for g in groups)<=budget:
        raise Decline('not-overfull')
    # Mid-bar directions/attributes could carry timing semantics. Do not relocate.
    seen_note=False
    cursor=F(0)
    for node in measure:
        if node.tag=='note':
            seen_note=True
            if node.find('chord') is None:
                cursor+=F(node.findtext('duration'))/divisions
        elif node.tag in ('backup','forward'):
            cursor+=F(node.findtext('duration'))/divisions*(1 if node.tag=='forward' else -1)
        elif seen_note and node.tag!='barline':
            # Harmony at the start of another voice is still at beat zero.
            # Keep it there; the existing playback export excludes accompaniment.
            if node.tag!='harmony' or cursor!=0 or F(node.findtext('offset','0'))!=0:
                raise Decline('mid-measure-metadata')
    staffs=sorted({g['staff'] for g in groups})
    if staffs!=['1','2']:raise Decline('requires-piano-grand-staff')
    if any(g['stem']=='whole' for g in groups):
        return solve_whole_bass(groups,budget)
    anchors=[]
    for staff in staffs:
        local=[g for g in groups if g['staff']==staff]
        stems={g['stem'] for g in local if not g['rest']}
        if len(stems)!=1 or sum(g['duration'] for g in local)!=budget or len(local)<3:continue
        local.sort(key=lambda g:g['x'])
        if any(b['x']-a['x']<4 for a,b in zip(local,local[1:])):continue
        if local[0]['oldOnset']!=0:continue
        anchors.append((staff,next(iter(stems)),local))
    if len(anchors)!=1:raise Decline('no-unique-complete-anchor-staff')
    anchor_staff,anchor_stem,anchor=anchors[0]
    cursor=F(0)
    for g in anchor:
        g['newOnset']=cursor;cursor+=g['duration']
    lanes={(anchor_staff,anchor_stem):anchor}
    for g in groups:
        if g['staff']==anchor_staff:continue
        if g['rest']:raise Decline('ambiguous-rest-in-supporting-staff')
        lanes.setdefault((g['staff'],g['stem']),[]).append(g)
    if len(lanes)!=3:raise Decline('requires-two-supporting-stem-lanes')
    for key,lane in lanes.items():
        if key[0]==anchor_staff:continue
        lane.sort(key=lambda g:g['x'])
        if any(b['x']-a['x']<4 for a,b in zip(lane,lane[1:])):raise Decline('ambiguous-supporting-chord')
        anchored=[]
        for i,g in enumerate(lane):
            matches=[a for a in anchor if abs(a['x']-g['x'])<=2]
            if len(matches)>1:raise Decline('ambiguous-alignment')
            if matches:anchored.append((i,matches[0]['newOnset']))
        if len(anchored)<2:raise Decline('insufficient-independent-alignments')
        offsets=[];total=F(0)
        for g in lane:offsets.append(total);total+=g['duration']
        origins={t-offsets[i] for i,t in anchored}
        if len(origins)!=1:raise Decline('duration-alignment-conflict')
        origin=origins.pop()
        if origin<0 or origin+total!=budget:raise Decline('supporting-lane-does-not-end-at-barline')
        for g,offset in zip(lane,offsets):g['newOnset']=origin+offset
    # Every cross-staff collision must describe the same attack, and ordering
    # must be monotone. This does not require equal spacing between beats.
    for a in groups:
        for b in groups:
            dx=b['x']-a['x']
            if abs(dx)<=2 and a['newOnset']!=b['newOnset']:
                raise Decline('simultaneous-column-conflict')
            if dx>2 and b['newOnset']<=a['newOnset']:
                raise Decline('horizontal-order-conflict')
    if all(g['newOnset']==g['oldOnset'] for g in groups):raise Decline('no-timing-change')
    return groups,lanes


def solve_whole_bass(groups,budget):
    """A separate narrow pattern; original-page rest/stem/beam proof is mandatory."""
    if budget!=4:raise Decline('whole-bass-requires-four-quarter-beats')
    anchor=sorted((g for g in groups if g['staff']=='1'),key=lambda g:g['x'])
    bass=[g for g in groups if g['staff']=='2' and g['stem']=='whole']
    moving=sorted((g for g in groups if g['staff']=='2' and g['stem']!='whole'),key=lambda g:g['x'])
    if len(bass)!=1 or bass[0]['duration']!=4 or bass[0]['oldOnset']!=0:
        raise Decline('requires-one-full-bar-bass')
    if len(anchor)<2 or any(g['rest'] or g['stem'] not in ('up','down') for g in anchor) or len({g['stem'] for g in anchor})!=1:
        raise Decline('whole-bass-incomplete-upper-anchor')
    if sum(g['duration'] for g in anchor)!=4 or anchor[0]['oldOnset']!=0:
        raise Decline('whole-bass-incomplete-upper-anchor')
    # Do not infer arbitrary gaps from horizontal spacing. This specific lane
    # has an explicit printed eighth rest, three beamed eighths and a half note.
    if [g['duration'] for g in moving]!=[F(1,2),F(1,2),F(1,2),F(2)] or any(g['rest'] or g['stem']!='up' for g in moving):
        raise Decline('whole-bass-unsupported-moving-rhythm')
    if any(len(g['nodes'])!=1 for g in moving):raise Decline('whole-bass-moving-chord')
    cursor=F(0)
    for g in anchor:g['newOnset']=cursor;cursor+=g['duration']
    bass[0]['newOnset']=F(0)
    if abs(bass[0]['x']-anchor[0]['x'])>4.5:raise Decline('whole-bass-start-column-conflict')
    cursor=F(1,2)
    for g in moving:g['newOnset']=cursor;cursor+=g['duration']
    if not any(abs(a['x']-moving[-1]['x'])<=2 and a['newOnset']==2 for a in anchor):
        raise Decline('whole-bass-missing-half-note-alignment')
    pitched=anchor+moving
    for a in pitched:
        for b in pitched:
            dx=b['x']-a['x']
            if abs(dx)<=2 and a['newOnset']!=b['newOnset']:raise Decline('simultaneous-column-conflict')
            if dx>2 and b['newOnset']<=a['newOnset']:raise Decline('horizontal-order-conflict')
    # Changing voice labels must not detach ties entering or leaving this bar.
    tied={}
    for g in groups:
        for n in g['nodes']:
            pitch=ET.tostring(n.find('pitch'))
            for t in n.findall('tie'):
                tied.setdefault((g['staff'],pitch,t.get('type')),[]).append((g['newOnset'],g['duration']))
    for (staff,pitch,kind),events in tied.items():
        if kind not in ('start','stop'):raise Decline('unknown-tie-type')
        partners=tied.get((staff,pitch,'stop' if kind=='start' else 'start'),[])
        for onset,duration in events:
            if sum((other==onset+duration if kind=='start' else other+length==onset) for other,length in partners)!=1:
                raise Decline('whole-bass-tie-crosses-boundary')
    return groups,{('1',anchor[0]['stem']):anchor,('2','whole'):bass,('2','up'):moving}


def rebuild(measure, lanes, divisions):
    # Preserve pitch, duration, ties, articulations and node identity. Only
    # cursor positioning and voice labels change. Gaps use invisible forwards.
    nodes=list(measure)
    for node in nodes:
        if node.tag in ('note','backup','forward'):measure.remove(node)
    right_bars=[n for n in measure if n.tag=='barline' and n.get('location','right')=='right']
    for n in right_bars:measure.remove(n)
    previous_end=F(0)
    for i,((staff,stem),lane) in enumerate(sorted(lanes.items())):
        if i:
            backup=ET.SubElement(measure,'backup');ET.SubElement(backup,'duration').text=str(int(previous_end*divisions))
        cursor=F(0)
        # Conventional distinct voice labels. They are not semantic hand IDs.
        voice=str((int(staff)-1)*4+(1 if stem=='up' else 2))
        for group in lane:
            start=group['newOnset']
            if start<cursor:raise ValueError('Repair produced an overlapping lane')
            if start>cursor:
                forward=ET.SubElement(measure,'forward');ET.SubElement(forward,'duration').text=str(int((start-cursor)*divisions))
                ET.SubElement(forward,'voice').text=voice;ET.SubElement(forward,'staff').text=staff
            for node in group['nodes']:
                v=node.find('voice')
                if v is None:raise ValueError('Voice missing')
                v.text=voice;measure.append(node)
            cursor=start+group['duration']
        previous_end=cursor
    measure.extend(right_bars)


def load_mxl(path):
    with ZipFile(path) as z:
        container=ET.fromstring(z.read('META-INF/container.xml'))
        name=next(n.get('full-path') for n in container.iter() if n.tag.split('}')[-1]=='rootfile')
        return ET.fromstring(z.read(name)),name


def write_mxl(source,target,root,name):
    target=Path(target)
    if Path(source).resolve()==target.resolve():raise ValueError('Never overwrite recognition original')
    target.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(source) as old,ZipFile(target,'w',ZIP_DEFLATED) as new:
        for item in old.infolist():
            new.writestr(item,ET.tostring(root,encoding='utf-8',xml_declaration=True) if item.filename==name else old.read(item.filename))


def repair(source,target,head_evidence):
    """head_evidence(index, original pitched nodes) must independently verify PDF heads."""
    root,name=load_mxl(source)
    parts=root.findall('part')
    report=dict(version=VERSION,accepted=[],declined=[],noteOrder=[])
    if len(parts)!=1:raise ValueError('Candidate repair requires one part')
    divisions=None;budget=None
    for mi,m in enumerate(parts[0].findall('measure')):
        attrs=m.find('attributes')
        if attrs is not None:
            if attrs.find('divisions') is not None:divisions=F(attrs.findtext('divisions'))
            times=attrs.findall('time')
            if times:
                t=times[0]
                budget=F(int(t.findtext('beats'))*4,int(t.findtext('beat-type'))) if len(times)==1 and (t.findtext('beats') or '').isdigit() and (t.findtext('beat-type') or '').isdigit() else None
        originals=[n for n in m.findall('note') if n.find('pitch') is not None]
        original_indices={id(n):i for i,n in enumerate(originals)}
        try:
            if not divisions or not budget or m.get('implicit')=='yes':raise Decline('unknown-meter-or-pickup')
            groups,lanes=solve(m,divisions,budget)
            evidence=head_evidence(mi,originals)
            if not evidence.get('verified'):raise Decline(evidence.get('reason','source-heads-unverified'))
            if any(g['stem']=='whole' for g in groups):
                verify=getattr(head_evidence,'verify_whole_lanes',None)
                if verify is None:raise Decline('whole-bass-needs-printed-rhythm-proof')
                rhythm=verify(mi,groups)
                if not rhythm.get('verified'):raise Decline(rhythm.get('reason','printed-rhythm-unverified'))
                evidence['rhythm']=rhythm
            changes=[dict(noteIndex=original_indices[id(n)],oldOnset=str(g['oldOnset']),newOnset=str(g['newOnset']))
                     for g in groups if g['oldOnset']!=g['newOnset'] for n in g['nodes'] if n.find('pitch') is not None]
            rebuild(m,lanes,divisions)
            report['accepted'].append(dict(measureIndex=mi,label=m.get('number'),changes=changes,evidence=evidence))
        except Decline as exc:
            report['declined'].append(dict(measureIndex=mi,label=m.get('number'),reason=str(exc)))
        report['noteOrder'].append([original_indices[id(n)] for n in m.findall('note') if n.find('pitch') is not None])
    write_mxl(source,target,root,name)
    return report
