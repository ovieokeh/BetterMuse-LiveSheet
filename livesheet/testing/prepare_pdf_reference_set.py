#!/usr/bin/env python3
"""Build and benchmark the LOCAL PDF reference set. Never publishes or edits jobs.

build: snapshot sources, render PDFs, check renderer roundtrip, retain provenance.
run: recognize only PDFs, then compare written events with separate answers.
Holdout recognition requires --allow-holdout; development is the default.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from zipfile import BadZipFile

from livesheet.pdf_reference import read_score, compare, signature
from livesheet.testing.pdf_reference_fixtures import create_fixture, FIXTURES

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT/'artifacts/test-results/pdf-reference-v1'
CONFIG = ROOT/'tests/data/pdf-quality/reference-set-v1.json'
MSCORE = Path(os.environ.get('PDF_MUSESCORE', '/usr/local/bin/mscore'))
AUDIVERIS = Path(os.environ.get('PDF_AUDIVERIS', '/opt/audiveris/bin/Audiveris'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    temp.replace(path)


def validate_splits(config):
    groups = {}
    ids = set()
    for entry in config['pieces']:
        if entry['id'] in ids: raise ValueError('Duplicate piece')
        ids.add(entry['id'])
        if entry['split'] not in ('development','holdout'): raise ValueError('Invalid split')
        previous = groups.setdefault(entry['family'],entry['split'])
        if previous != entry['split']: raise ValueError('Source family leaks across splits')
    if ids & set(config['fixtures']): raise ValueError('Duplicate fixture ID')


def checked_run(command, log, timeout=120, env=None):
    # Share the existing process-group timeout/cleanup implementation only.
    from livesheet.recognition import run
    run([str(v) for v in command],log,timeout,env)


def fully_equal(report):
    return not report['differences']

