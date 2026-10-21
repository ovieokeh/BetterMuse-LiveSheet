"""Small authored examples with declared answers, independent of XML parsing.

Beats are quarter-note units. Each event: pitches (empty means rest), onset,
duration, optional tie types. Different voices may overlap.
"""
from fractions import Fraction as F
import xml.etree.ElementTree as ET


def voice(staff, events):
    return dict(staff=staff, events=events)


def measure(voices, **kw):
    return dict(voices=voices, **kw)


FIXTURES = {
    'dotted-overlap': dict(title='Dotted rhythm and overlapping voices', measures=[
        measure([voice(1, [([76,79],0,1), ([],1,'1/2'), ([76,79],'3/2','1/2',['start']), ([76,79],2,2,['stop'])]),
                 voice(2, [([52],0,'3/2'), ([52],'3/2','1/2',['start']), ([52],2,2,['stop'])]),
                 voice(2, [([] ,0,'1/2'), ([59],'1/2','1/2'), ([55],1,'1/2'), ([62],'3/2','1/2',['start']), ([62],2,2,['stop'])])]),
        measure([voice(1, [([72,76],0,1), ([],1,'1/2'), ([72,76],'3/2','1/2',['start']), ([72,76],2,2,['stop'])]),
                 voice(2, [([48],0,'3/2'), ([48],'3/2','1/2',['start']), ([48],2,2,['stop'])]),
                 voice(2, [([],0,'1/2'), ([59],'1/2','1/2'), ([55],1,'1/2'), ([59],'3/2','1/2',['start']), ([59],2,2,['stop'])])])]),
    'cross-bar-ties': dict(title='Ties across the barline', measures=[
        measure([voice(1,[([72,76],0,3),([74,77],3,1,['start'])]),voice(2,[([48],0,4,['start'])])]),
        measure([voice(1,[([74,77],0,1,['stop']),([72,76],1,3)]),voice(2,[([48],0,4,['stop'])])])]),
    'accidentals': dict(title='Accidentals and repeated pitches', measures=[
        measure([voice(1,[([66],0,1),([66],1,1),([65],2,1),([66],3,1)]),voice(2,[([46,53],0,2),([47,54],2,2)])]),
        measure([voice(1,[([65],0,1),([63],1,1),([64],2,1),([63],3,1)]),voice(2,[([48,55],0,4)])])]),
    'tuplets-and-rests': dict(title='Triplets and rests', measures=[
        measure([voice(1,[([72],0,'1/3'),([74],'1/3','1/3'),([76],'2/3','1/3'),([],1,1),([79],2,2)]),voice(2,[([48],0,2),([55],2,2)])]),
        measure([voice(1,[([],0,1),([76],1,'1/3'),([74],'4/3','1/3'),([72],'5/3','1/3'),([71],2,2)]),voice(2,[([43],0,4)])])]),
    'pickup-meter-change': dict(title='Pickup and changing meter', measures=[
        measure([voice(1,[([72],0,1)]),voice(2,[([],0,1)])],implicit=True,label='0',time=[3,4]),
        measure([voice(1,[([74],0,1),([76],1,1),([77],2,1)]),voice(2,[([48],0,3)])],time=[3,4]),
        measure([voice(1,[([79],0,2)]),voice(2,[([55],0,2)])],time=[2,4])]),
    'repeat-endings': dict(title='Repeat and alternate endings', measures=[
        measure([voice(1,[([72],0,2),([74],2,2)]),voice(2,[([48],0,4)])],repeatStart=True),
        measure([voice(1,[([76],0,4)]),voice(2,[([55],0,4)])],ending='1',repeatEnd=True),
        measure([voice(1,[([72],0,4)]),voice(2,[([48],0,4)])],ending='2')]),
}


def sub(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, attrs)
    if text is not None: e.text = str(text)
    return e


def create_fixture(ident, path):
    spec = FIXTURES[ident]
    root = ET.Element('score-partwise', version='4.0')
    sub(sub(root,'work'),'work-title',spec['title'])
    ident_node = sub(root,'identification')
    sub(ident_node,'creator','BetterMuse recognition benchmark',type='composer')
    sub(ident_node,'rights','Locally authored test fixture')
    partlist = sub(root,'part-list')
    sub(sub(partlist,'score-part',id='P1'),'part-name','Piano')
    part = sub(root,'part',id='P1')
    expected = dict(schemaVersion=1, parts=[dict(id='P1',measures=[])], unsupported=[])
    for mi, specm in enumerate(spec['measures']):
        label = specm.get('label',str(mi if spec['measures'][0].get('implicit') else mi+1))
        m = sub(part,'measure',number=label, **({'implicit':'yes'} if specm.get('implicit') else {}))
        if mi == 0 or 'time' in specm:
            attrs = sub(m,'attributes')
            if mi == 0:
                sub(attrs,'divisions',12)
                sub(sub(attrs,'key'),'fifths',0)
            time = sub(attrs,'time'); beats, beat_type = specm.get('time',[4,4])
            sub(time,'beats',beats); sub(time,'beat-type',beat_type)
            if mi == 0:
                sub(attrs,'staves',2)
                for staff,sign,line in [(1,'G',2),(2,'F',4)]:
                    clef = sub(attrs,'clef',number=str(staff)); sub(clef,'sign',sign); sub(clef,'line',line)
        expected_notes, bars = [], []
        if specm.get('repeatStart'):
            sub(sub(m,'barline',location='left'),'repeat',direction='forward')
            bars.append(dict(kind='repeat',location='left',direction='forward'))
        if specm.get('ending'):
            sub(sub(m,'barline',location='left'),'ending',number=specm['ending'],type='start')
            bars.append(dict(kind='ending',location='left',number=specm['ending'],type='start'))
        prior_end = F(0)
        extent = F(0)
        for vi,v in enumerate(specm['voices']):
            if vi:
                sub(sub(m,'backup'),'duration',int(prior_end*12))
            cursor = F(0)
            for event in v['events']:
                pitches,onset,duration = event[:3]
                ties = event[3] if len(event)>3 else []
                onset,duration = F(onset),F(duration)
                if cursor != onset: raise ValueError('Fixture must explicitly encode rests')
                for ni,pitch in enumerate(pitches or [None]):
                    n = sub(m,'note')
                    if ni: sub(n,'chord')
                    if pitch is None: sub(n,'rest')
                    else:
                        step,alter = [('C',0),('C',1),('D',0),('D',1),('E',0),('F',0),('F',1),('G',0),('G',1),('A',0),('A',1),('B',0)][pitch%12]
                        p = sub(n,'pitch'); sub(p,'step',step)
                        if alter: sub(p,'alter',alter)
                        sub(p,'octave',pitch//12-1)
                        expected_notes.append(dict(staff=v['staff'],midi=str(pitch),onset=str(onset),duration=str(duration),grace=False,ties=sorted(ties),voice=str(vi+1)))
                    sub(n,'duration',int(duration*12))
                    for tie in ties: sub(n,'tie',type=tie)
                    sub(n,'voice',vi+1)
                    kinds = {F(4):('whole',False),F(3):('half',True),F(2):('half',False),F('3/2'):('quarter',True),F(1):('quarter',False),F('1/2'):('eighth',False),F('1/3'):('eighth',False)}
                    kind,dotted = kinds[duration]; sub(n,'type',kind)
                    if dotted: sub(n,'dot')
                    if duration == F('1/3'):
                        tm = sub(n,'time-modification'); sub(tm,'actual-notes',3);sub(tm,'normal-notes',2);sub(tm,'normal-type','eighth')
                    sub(n,'staff',v['staff'])
                    if ties or duration == F('1/3'):
                        nt = sub(n,'notations')
                        for tie in ties: sub(nt,'tied',type=tie)
                        if duration == F('1/3'):
                            if onset.denominator == 1: sub(nt,'tuplet',type='start',number='1')
                            if (onset+duration).denominator == 1: sub(nt,'tuplet',type='stop',number='1')
                cursor = onset+duration
            prior_end = cursor
            extent = max(extent,cursor)
        if specm.get('ending') or specm.get('repeatEnd'):
            bar = sub(m,'barline',location='right')
            if specm.get('ending'):
                end_type = 'stop' if specm.get('repeatEnd') else 'discontinue'
                sub(bar,'ending',number=specm['ending'],type=end_type)
                bars.append(dict(kind='ending',location='right',number=specm['ending'],type=end_type))
            if specm.get('repeatEnd'):
                sub(bar,'repeat',direction='backward')
                bars.append(dict(kind='repeat',location='right',direction='backward'))
        expected['parts'][0]['measures'].append(dict(index=mi,label=label,implicit=bool(specm.get('implicit')),extent=str(extent),notes=expected_notes,repeatMarks=bars))
    ET.indent(root)
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
    return expected
