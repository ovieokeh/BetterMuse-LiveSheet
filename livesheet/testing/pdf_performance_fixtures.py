"""Authored playback cases. Expected visits, tempo and pitches precede rendering.

The renderer is checked against these answers before its MIDI can become an
expression reference. All font/layout variants of a family keep the same split.
"""
from copy import deepcopy
from fractions import Fraction as F
import xml.etree.ElementTree as E
from livesheet.testing.pdf_reference_fixtures import FIXTURES, create_fixture, measure, voice, sub


def quarters(pitches):return [( [p],i,1) for i,p in enumerate(pitches)]
def eighths(pitches):return [([p],str(F(i,2)),'1/2') for i,p in enumerate(pitches)]
def bar(upper,lower=48):return measure([voice(1,upper),voice(2,[([lower],0,4)])])
def spec(title,measures,**extra):return dict(title=title,measures=measures,**extra)

CASES={
 'opening-tempo':spec('Opening tempo and flowing eighth notes',[
     bar(eighths([72,76,79,76,74,77,81,77])),bar(eighths([71,74,79,74,72,76,79,76]))],tempos=[(0,0,100)]),
 'midbar-tempo':spec('Tempo changes inside a measure',[
     bar(quarters([72,74,76,77])),bar(quarters([79,77,76,72]))],tempos=[(0,0,80),(0,2,120),(1,2,60)]),
 'tied-chords':dict(deepcopy(FIXTURES['cross-bar-ties']),tempos=[(0,0,96)]),
 'alternate-endings':dict(deepcopy(FIXTURES['repeat-endings']),tempos=[(0,0,108)],visits=[0,1,0,2]),
 'articulation-dynamics':spec('Short notes and contrasting dynamics',[
     bar(quarters([72,74,76,77])),bar(quarters([72,74,76,77]))],tempos=[(0,0,100)],
     dynamics=[(0,0,'p',40),(1,0,'f',90)],staccato=[(0,0),(0,2)]),
 'pedal-release':spec('Pedal across changing harmonies',[
     bar(quarters([72,76,79,76])),bar(quarters([74,77,81,77]))],tempos=[(0,0,90)],pedal=[(0,0,'start'),(0,3,'stop')]),
 'fermata-release':spec('Hold then return to tempo',[
     bar(quarters([72,74,76,77])),bar(quarters([79,77,76,72]))],tempos=[(0,0,100)],fermata=[(0,1)]),
 'midbar-ottava':spec('Octave shift inside the measure',[
     bar(quarters([72,74,76,77])),bar(quarters([79,77,76,72]))],tempos=[(0,0,100)],ottava=[(0,2,'down'),(1,2,'stop')]),
 'compound-rests':spec('Compound meter with rests and dotted rhythm',[
     measure([voice(1,[([72],0,'3/2'),([], '3/2','1/2'),([76],2,1)]),voice(2,[([48,55],0,3)])],time=[6,8]),
     measure([voice(1,[([74],0,'1/2'),([76],'1/2',1),([79],'3/2','3/2')]),voice(2,[([43,50],0,3)])],time=[6,8])],tempos=[(0,0,108)],split='holdout'),
 'meter-ties':spec('Meter changes with tied chords',[
     measure([voice(1,[([72,76],0,2,['start'])]),voice(2,[([48],0,2)])],time=[2,4]),
     measure([voice(1,[([72,76],0,1,['stop']),([74,77],1,2)]),voice(2,[([50],0,3)])],time=[3,4]),
     bar(quarters([76,77,79,72]))],tempos=[(0,0,84)],split='holdout'),
}

VARIANTS={
 'leland':dict(font='Leland',text='Leland Text',width=8.27,height=11.69,spatium=1.75),
 'bravura-compact':dict(font='Bravura',text='Bravura Text',width=6.2,height=8.8,spatium=1.65),
 'petaluma-wide':dict(font='Petaluma',text='Petaluma Text',width=11.69,height=8.27,spatium=1.9),
}


def create(ident,path):
    spec=CASES[ident];key='performance-v2-'+ident
    FIXTURES[key]=spec
    try:written=create_fixture(key,path)
    finally:del FIXTURES[key]
    root=E.parse(path).getroot();measures=root.find('part').findall('measure')
    positions={}
    for mi,m in enumerate(measures):
        cursor=F(0)
        for n in m:
            if n.tag=='backup':break
            if n.tag=='note' and n.find('chord') is None:
                positions[mi,cursor]=n;cursor+=F(n.findtext('duration'))/12
    def direction(mi,beat):
        m=measures[mi];d=E.Element('direction',placement='above')
        m.insert(list(m).index(positions[mi,F(beat)]),d)
        return d,sub(d,'direction-type')
    for mi,beat,bpm in spec.get('tempos',[]):
        d,dt=direction(mi,beat);sub(dt,'words','Moderately' if beat==0 else 'New tempo')
        metro=sub(sub(d,'direction-type'),'metronome',parentheses='yes');sub(metro,'beat-unit','quarter');sub(metro,'per-minute',bpm)
        sub(d,'staff',1);sub(d,'sound',tempo=str(bpm))
    for mi,beat,name,value in spec.get('dynamics',[]):
        d,dt=direction(mi,beat);sub(sub(dt,'dynamics'),name);sub(d,'staff',1);sub(d,'sound',dynamics=str(value))
    for mi,beat,kind in spec.get('pedal',[]):
        d,dt=direction(mi,beat);d.set('placement','below');sub(dt,'pedal',type=kind,line='yes',sign='yes');sub(d,'staff',1)
    for mi,beat,kind in spec.get('ottava',[]):
        d,dt=direction(mi,beat);sub(dt,'octave-shift',type=kind,size='8',number='1');sub(d,'staff',1)
    for mi,beat in spec.get('staccato',[]):
        n=positions[mi,F(beat)];nt=n.find('notations')
        if nt is None:nt=sub(n,'notations')
        sub(sub(nt,'articulations'),'staccato')
    for mi,beat in spec.get('fermata',[]):sub(sub(positions[mi,F(beat)],'notations'),'fermata',type='upright')
    # MusicXML pitch is the sounding pitch even under an octave-shift direction.
    # The recipe above declares the printed pitches. Encode that distinction
    # explicitly so the reference renderer prints the intended notes under 8va.
    shift=0
    for mi,m in enumerate(measures):
        changes=sorted((F(b),12 if kind=='down' else 0) for i,b,kind in spec.get('ottava',[]) if i==mi)
        pitched=[n for n in m.findall('note') if n.find('pitch') is not None]
        for n,answer in zip(pitched,written['parts'][0]['measures'][mi]['notes']):
            delta=next((v for b,v in reversed(changes) if b<=F(answer['onset'])),shift) if answer['staff']==1 else 0
            if delta:
                octave=n.find('pitch/octave');octave.text=str(int(octave.text)+delta//12)
                answer['midi']=str(int(answer['midi'])+delta)
        if changes:shift=changes[-1][1]
    E.indent(root);E.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    return written


def authored(ident):
    """Build sounding attacks from declared events, never from parsed MusicXML."""
    spec=CASES[ident];visits=spec.get('visits',list(range(len(spec['measures']))))
    notes=[];open_ties={};tempo=[];route=[];offset=F(0);bpm=120;octave=0
    for mi in visits:
        m=spec['measures'][mi];route.append(dict(id='m'+str(mi),beat=float(offset)))
        marks=sorted([(F(b),v) for i,b,v in spec['tempos'] if i==mi])
        if not marks or marks[0][0]>0:marks.insert(0,(F(0),bpm))
        for b,v in marks:tempo.append((offset+b,v))
        bpm=marks[-1][1]
        for i,b in spec.get('fermata',[]):
            if i==mi:tempo.extend([(offset+F(b),bpm/2),(offset+F(b)+1,bpm)])
        shifts=sorted((F(b),12 if kind=='down' else 0) for i,b,kind in spec.get('ottava',[]) if i==mi)
        for vi,v in enumerate(m['voices']):
            for event in v['events']:
                pitches,on,dur=event[:3];on,dur=F(on),F(dur);ties=event[3] if len(event)>3 else []
                shift=next((s for b,s in reversed(shifts) if b<=on),octave) if v['staff']==1 else 0
                for p in pitches:
                    key=(vi,p+shift);start=offset+on
                    if 'stop' in ties:
                        if key not in open_ties or open_ties[key]['endBeat']!=float(start):raise ValueError('Invalid authored tie chain')
                        n=open_ties[key];n['endBeat']=float(start+dur)
                    else:
                        n=dict(midi=p+shift,beat=float(start),endBeat=float(start+dur),staff=v['staff']);notes.append(n)
                    if 'start' in ties:open_ties[key]=n
                    else:open_ties.pop(key,None)
        if shifts:octave=shifts[-1][1]
        offset+=max(F(e[1])+F(e[2]) for v in m['voices'] for e in v['events'])
    if open_ties:raise ValueError('Unclosed authored ties')
    unique={float(b):float(t) for b,t in tempo};tempo=sorted(unique.items())
    def seconds(beat):
        return sum((min(beat,tempo[i+1][0] if i+1<len(tempo) else beat)-b)*60/t
                   for i,(b,t) in enumerate(tempo) if b<beat)
    for n in notes:n['start']=seconds(n['beat']);n['nominalDuration']=seconds(n['endBeat'])-n['start']
    for m in route:m['start']=seconds(m['beat'])
    return dict(notes=notes,measures=route,tempos=[dict(beat=b,bpm=t) for b,t in tempo])


def verify_renderer(ident,actual):
    expected=authored(ident);errors=[];available=set(range(len(actual['notes'])))
    for n in expected['notes']:
        matches=[i for i in available if actual['notes'][i]['midi']==n['midi'] and abs(actual['notes'][i]['start']-n['start'])<.035]
        if not matches:errors.append(dict(kind='authored-attack-missing',expected=n));continue
        i=min(matches,key=lambda i:abs(actual['notes'][i]['start']-n['start']));available.remove(i)
        # Tied chains must sustain across their intermediate bar/beat, not reattack.
        if n['endBeat']-n['beat']>4 and actual['notes'][i]['duration']<n['nominalDuration']*.9:
            errors.append(dict(kind='authored-tie-too-short',expected=n))
    if available:errors.append(dict(kind='unexpected-renderer-attacks',indices=sorted(available)))
    if [m['id'] for m in actual['measures']] != [m['id'] for m in expected['measures']]:errors.append(dict(kind='authored-repeat-route'))
    elif any(abs(a['start']-b['start'])>.035 for a,b in zip(actual['measures'],expected['measures'])):errors.append(dict(kind='authored-measure-timing'))
    def upper(beat):
        n=next(n for n in expected['notes'] if n['staff']==1 and n['beat']==beat)
        return next((a for a in actual['notes'] if a['midi']==n['midi'] and abs(a['start']-n['start'])<.035),None)
    if ident=='articulation-dynamics':
        a,b,c=upper(0),upper(1),upper(4)
        if not all((a,b,c)) or not (a['duration']<b['duration']*.75 and c['velocity']>a['velocity']+20):errors.append(dict(kind='authored-articulation-dynamics'))
    if ident=='pedal-release':
        a=upper(0);b=upper(3)
        if not a or not b or a['duration']<1.8 or a['start']+a['duration']>b['start']+.1:errors.append(dict(kind='authored-pedal-sustain-release'))
    return dict(passed=not errors,errors=errors,expectedAttacks=len(expected['notes']),scope='Independent authored pitches, attacks, visits, tie continuation and selected expression relationships')
