#!/usr/bin/env python3
"""Run the upload repair chain against trusted local written-note references.

Exits nonzero on lost correct notes, new wrong notes, or failed evaluation.
Known baseline failures are counted separately; never counted as passes.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from livesheet.pdf_experimental import prepare_candidate, fingerprint, save, sha
from livesheet.pdf_reference import read_score, signature, compare

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / 'artifacts/test-results/pdf-reference-v1'
CACHE = ROOT / 'artifacts/test-results/pdf-rhythm-repair-v1'


def regressions(expected, before, after):
    if len(expected['parts']) != len(before['parts']) or len(before['parts']) != len(after['parts']):
        raise ValueError('part-count-mismatch')
    lost, introduced, improved = [], [], []
    for pi, (w, a, b) in enumerate(zip(expected['parts'], before['parts'], after['parts'])):
        if len(a['measures']) != len(b['measures']):
            raise ValueError('measure-count-mismatch')
        for mi in range(max(len(w['measures']), len(a['measures']))):
            x, y, z = [(part['measures'][mi] if mi < len(part['measures']) else {'notes': []}) for part in (w, a, b)]
            wanted, old, new = [Counter(map(signature, m['notes'])) for m in (x, y, z)]
            location = dict(part=pi, measure=mi+1)
            if (old & wanted) - (new & wanted): lost.append(location)
            if (new - wanted) - (old - wanted): introduced.append(location)
            if sum((new & wanted).values()) > sum((old & wanted).values()): improved.append(location)
    return dict(lostCorrect=lost, newIncorrect=introduced, improved=improved)

