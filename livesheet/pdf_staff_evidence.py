"""Recognize short printed five-line staves before choosing OMR settings.

Only straight vector lines plus a known, aligned clef and notehead qualify.
This adjusts detection, never constructs musical events or copies answers.
"""
from collections import defaultdict
import pymupdf
from livesheet.pdf_music_fonts import CLEFS, HEADS

VERSION = 'printed-short-staves-v1'
CONSTANTS = {
    'org.audiveris.omr.sheet.grid.LinesRetriever.minStaffLength': '8',
    'org.audiveris.omr.sheet.grid.LinesRetriever.minTrueLengthRatio': '0.1',
    'org.audiveris.omr.sheet.grid.ClustersRetriever.minClusterLengthRatio': '0.1',
}


def staves(drawings, symbols):
    rows = defaultdict(list)
    for d in drawings:
        if d.get('color') is None or not 0 < (d.get('width') or 0) < 2: continue
        for item in d['items']:
            if item[0] != 'l': continue
            a,b = item[1:]
            if abs(a[1]-b[1]) > .05: continue
            left,right = sorted((a[0],b[0]))
            if right-left < 2: continue
            rows[round((a[1]+b[1])/2,2)].append((left,right))
    # Engravers may draw staff lines separately for each measure. Join touching
    # segments before measuring length, otherwise a short first bar looks like
    # a short system and needlessly changes recognition for an ordinary page.
    groups = defaultdict(list)
    for y,segments in rows.items():
        merged=[]
        for left,right in sorted(segments):
            if merged and left <= merged[-1][1]+.1:
                merged[-1]=(merged[-1][0],max(right,merged[-1][1]))
            else:merged.append((left,right))
        for left,right in merged:
            if right-left >= 25:groups[(round(left,1),round(right,1))].append(y)
    found = []
    for (left,right), ys in groups.items():
        ys = sorted(set(round(y,3) for y in ys))
        for i in range(len(ys)-4):
            row = ys[i:i+5]; gap = (row[-1]-row[0])/4
            if not 2 < gap < 16 or any(abs(b-a-gap) > gap*.03 for a,b in zip(row,row[1:])): continue
            clefs = [s for s in symbols if s['c'] in CLEFS.get(s['font'],{}) and
                     left <= s['x'] < left+gap*5 and
                     abs(s['y']-(row[-1]-(int(CLEFS[s['font']][s['c']][1])-1)*gap)) < gap*.2]
            if len(clefs) != 1: continue
            heads = [s for s in symbols if s['c'] in HEADS.get(s['font'],{}) and
                     clefs[0]['x']+gap < s['x'] < right and row[0]-gap*3 < s['y'] < row[-1]+gap*3 and
                     abs((row[-1]-s['y'])/(gap/2)-round((row[-1]-s['y'])/(gap/2))) < .2]
            if not heads: continue
            found.append(dict(left=left,right=right,top=row[0],bottom=row[-1],gap=gap,
                              lengthInGaps=(right-left)/gap,printedHeads=len(heads)))
    return found


def inspect(pdf):
    found = []
    with pymupdf.open(pdf) as document:
        for pi,page in enumerate(document):
            symbols = [dict(font=span['font'],c=c['c'],x=c['origin'][0],y=c['origin'][1])
                       for b in page.get_text('rawdict')['blocks'] for line in b.get('lines',[])
                       for span in line['spans'] for c in span['chars']]
            found.extend(dict(page=pi+1,**s) for s in staves(page.get_drawings(),symbols))
    short = [s for s in found if 8 <= s['lengthInGaps'] < 30]
    return dict(version=VERSION,shortStaves=short,constants=CONSTANTS.copy() if short else {})
