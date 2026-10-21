"""Explicit glyph encodings inspected in local vector-score PDFs.

Bravura/Petaluma exports use MuseScore's alternate noteheads (F4BC–F4BE).
Do not assume arbitrary fonts or arbitrary private-use characters are music.
"""
SMUFL_FONTS = frozenset(('Leland', 'Bravura', 'Petaluma'))
HEADS = {
    'MScore': {'\ue12b':'whole', '\ue12c':'half', '\ue12d':'filled'},
    'Leland': {'\ue0a2':'whole', '\ue0a3':'half', '\ue0a4':'filled'},
    'Bravura': {'\uf4bc':'whole', '\uf4bd':'half', '\uf4be':'filled'},
    'Petaluma': {'\uf4bc':'whole', '\uf4bd':'half', '\uf4be':'filled'},
}
CLEFS = {font: {'\ue050':('G','2'), '\ue062':('F','4')} for font in SMUFL_FONTS}
CLEFS['MScore'] = {'\ue19e':('G','2'), '\ue19c':('F','4')}
ACCIDENTALS = {font: {'\ue262':1, '\ue261':0, '\ue260':-1} for font in SMUFL_FONTS}
ACCIDENTALS['MScore'] = {'\ue10e':1, '\ue113':0, '\ue114':-1}
TEMPO_FONTS = frozenset(('LelandText', 'BravuraText', 'PetalumaText'))


def encoded_music_spans(blocks):
    """Distinguish MuseScore 3's SMuFL MScore from its legacy namesake.

The same font name is not sufficient. Require the SMuFL heads and clefs on the
page and no legacy heads before giving it the explicit MScore3 identity.
"""
    spans=[s for b in blocks for l in b.get('lines',[]) for s in l['spans']]
    codes={c['c'] for s in spans if s['font']=='MScore' for c in s['chars']}
    modern=bool(codes & {'\ue0a2','\ue0a3','\ue0a4'}) and bool(codes & {'\ue050','\ue062'}) and not codes & {'\ue12b','\ue12c','\ue12d'}
    return [dict(s,font='MScore3' if modern and s['font']=='MScore' else s['font']) for s in spans]

# This is an internal identity, assigned only by the encoding check above.
SMUFL_FONTS=SMUFL_FONTS | {'MScore3'}
HEADS['MScore3']=dict(HEADS['Leland'])
CLEFS['MScore3']=dict(CLEFS['Leland'])
ACCIDENTALS['MScore3']=dict(ACCIDENTALS['Leland'])
