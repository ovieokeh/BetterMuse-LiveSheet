"""Opt-in sounding-pitch normalization from explicit vector-PDF 8va segments.

Known BravuraText ottava glyph, dashed horizontal line and end hook/system edge.
Only standard upper treble staff. Unknown or incomplete evidence rejects the
whole operation. The written candidate is preserved; output is for performance.
"""
from copy import deepcopy
from pathlib import Path
import pymupdf
from livesheet.pdf_music_fonts import SMUFL_FONTS, TEMPO_FONTS
from livesheet.pdf_rhythm_repair import load_mxl, write_mxl, Decline
from livesheet.pdf_vector_recovery import VectorRecovery

VERSION='printed-ottava-v1'


def segments(pdf,mapping):
    result=[]
    with pymupdf.open(pdf) as doc:
        for pi,page in enumerate(doc):
            geometry=mapping['pages'][pi];sx=page.rect.width/geometry['width'];sy=page.rect.height/geometry['height']
            lines=[];labels=[]
            for d in page.get_drawings():
                if not d.get('color') or not d['dashes'].startswith('[ ') or not 0<d['width']<1:continue
                for item in d['items']:
                    if item[0]=='l':lines.append((tuple(item[1]),tuple(item[2])))
            for b in page.get_text('rawdict')['blocks']:
                for line in b.get('lines',[]):
                    for span in line['spans']:
                        for c in span['chars']:
                            if span['font'] in SMUFL_FONTS | TEMPO_FONTS and c['c']=='\ue510':labels.append(dict(x=c['origin'][0],y=(c['bbox'][1]+c['bbox'][3])/2,right=c['bbox'][2],font=span['font']))
            for label in labels:
                systems=[]
                for system in geometry['systems']:
                    staff=system['staves'][0];top=staff['lines'][0][0][1]*sy;gap=(staff['lines'][-1][0][1]-staff['lines'][0][0][1])*sy/4
                    # Handwritten font text boxes include large unused vertical
                    # space. Associate the explicit adjacent dashed line first,
                    # then use its actual height to identify the staff.
                    vertical_limit=2 if label['font']=='Petaluma' else .8
                    found=[(a,b) for a,b in lines if abs(a[1]-b[1])<gap*.05 and b[0]>a[0]
                           and 0<a[0]-label['right']<gap and abs(a[1]-label['y'])<gap*vertical_limit
                           and gap<top-a[1]<12*gap]
                    if found:systems.append((system,staff,gap,found))
                if len(systems)!=1:raise Decline('ambiguous-octave-staff')
                system,staff,gap,found=systems[0]
                if len(found)!=1:raise Decline('missing-or-ambiguous-octave-line')
                a,b=found[0];end=staff['lines'][0][-1][0]*sx
                hooks=[(c,d) for c,d in lines if abs(c[0]-b[0])<gap*.1 and abs(d[0]-b[0])<gap*.1
                       and abs(min(c[1],d[1])-b[1])<gap*.1 and gap<abs(d[1]-c[1])<3*gap]
                if len(hooks)>1 or (not hooks and abs(b[0]-end)>gap*.5):raise Decline('octave-line-has-no-end-or-continuation')
                result.append(dict(page=pi+1,system=system['id'],left=label['x'],right=b[0],y=a[1],ends=bool(hooks),gap=gap))
    # Continuing spans must be explicit on every successive system and end in a hook.
    order=[(pi+1,s['id']) for pi,p in enumerate(mapping['pages']) for s in p['systems']]
    result.sort(key=lambda s:order.index((s['page'],s['system'])))
    for i,s in enumerate(result):
        if not s['ends']:
            if i+1==len(result) or order.index((result[i+1]['page'],result[i+1]['system']))!=order.index((s['page'],s['system']))+1:raise Decline('unclosed-octave-span')
    return result


def recover(source,target,pdf,mapping):
    evidence=VectorRecovery(pdf,mapping) # verifies PDF identity
    spans=segments(pdf,mapping)
    if not spans:raise Decline('no-supported-printed-octaves')
    root,name=load_mxl(source);parts=root.findall('part')
    if len(parts)!=1:raise Decline('octave-requires-one-piano-part')
    changes=[];clefs={};removed=0
    for mi,m in enumerate(parts[0].findall('measure')):
        attrs=m.find('attributes')
        if attrs is not None:
            if attrs.find('transpose') is not None:raise Decline('octave-transposing-part')
            for c in attrs.findall('clef'):clefs[c.get('number','1')]=(c.findtext('sign'),c.findtext('line'),c.findtext('clef-octave-change','0'))
        bar=mapping['measures'][mi]
        active=[s for s in spans if (s['page'],s['system'])==(bar['page'],bar['system'])]
        nodes=[n for n in m.findall('note') if n.find('pitch') is not None]
        records=[n for n in mapping['notes'] if n['measureIndex']==mi]
        if len(nodes)!=len(records):raise Decline('octave-note-mapping-count')
        page=mapping['pages'][bar['page']-1];w,h=evidence.proof.page_sizes[bar['page']-1];sx=w/page['width'];sy=h/page['height']
        system=next(s for s in page['systems'] if s['id']==bar['system']);staff=system['staves'][0];bottom=staff['lines'][-1][0][1]*sy
        for node,rec in zip(nodes,records):
            if node.findtext('staff','1')!='1':continue
            if not rec.get('box'):
                if active:raise Decline('unmapped-octave-note')
                continue
            x,y,bw,bh=rec['box'];x*=sx;y=(y+bh/2)*sy
            matched=[s for s in active if s['left']-s['gap']*.3<=x<=s['right']]
            if not matched:continue
            if len(matched)!=1 or clefs.get('1')!=('G','2','0'):raise Decline('unsupported-octave-clef-or-overlap')
            span=matched[0];gap=span['gap']
            glyphs=[g for g in evidence.proof.glyphs[bar['page']-1] if abs(g['x']*w-x)<gap*.3 and abs(g['y']*h-y)<gap*.3]
            if len(glyphs)!=1:raise Decline('octave-note-has-no-unique-printed-head')
            steps=round((bottom-y)/(gap/2));written=30+steps
            if abs(bottom-y-steps*gap/2)>gap*.22:raise Decline('octave-note-off-staff-step')
            pitch=node.find('pitch')
            if pitch.findtext('step')!='CDEFGAB'[written%7] or int(pitch.findtext('octave'))!=written//7:raise Decline('octave-pitch-already-shifted-or-conflicting')
            pitch.find('octave').text=str(written//7+1)
            changes.append(dict(noteId=rec['id'],measureIndex=mi,staff=1,semitones=12))
        for direction in m.findall('direction'):
            shifts=direction.findall('direction-type/octave-shift')
            if not shifts:continue
            if not active or direction.findtext('staff','1')!='1' or any(s.get('type')!='down' or s.get('size','8')!='8' for s in shifts):raise Decline('recognized-octave-conflicts-with-printed-evidence')
            for dtype in direction.findall('direction-type'):
                for shift in dtype.findall('octave-shift'):dtype.remove(shift);removed+=1
                if len(dtype)==0:direction.remove(dtype)
            if direction.find('direction-type') is None:m.remove(direction)
    if not changes:raise Decline('empty-octave-span')
    changed={c['noteId'] for c in changes}
    for note in mapping['notes']:
        if note['id'] not in changed:continue
        for tie in note.get('ties',[]):
            opposite='stop' if tie=='start' else 'start'
            partners=[n for n in mapping['notes'] if opposite in n.get('ties',[]) and n['staff']==note['staff'] and n['voice']==note['voice'] and n['midi']==note['midi'] and
                      abs((n['start'] if tie=='start' else n['start']+n['duration'])-(note['start']+note['duration'] if tie=='start' else note['start']))<.0001]
            if len(partners)!=1 or partners[0]['id'] not in changed:raise Decline('tie-crosses-uncertain-octave-boundary')
    # The normalized performance XML has sounding pitches and no incomplete octave
    # directions. The untouched PDF supplies the visual notation to both clients.
    write_mxl(source,target,root,name)
    result=deepcopy(mapping)
    for n in result['notes']:
        if n['id'] in changed:n['midi']+=12
    result['musicxmlFiles']=[str(Path(target).resolve())]
    report=dict(version=VERSION,segments=spans,changes=changes,removedIncompleteDirections=removed,scope='Sounding pitch only; original written candidate and PDF retained')
    result['octaveRecovery']=report
    return result,report
