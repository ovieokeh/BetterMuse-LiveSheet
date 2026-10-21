#!/usr/bin/env python3
"""Bounded PDF inspection and first-page preview, run outside the HTTP process."""
import argparse
import json
import math
import shutil
from pathlib import Path
import resource
import sys

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 20


def inspect_pdf(source, preview=None):
    import pymupdf
    try:
        if not 0 < source.stat().st_size <= MAX_BYTES:
            raise ValueError('pdf_size_limit')
        with pymupdf.open(source) as pdf:
            if not pdf.is_pdf or pdf.needs_pass:
                raise ValueError('pdf_locked_or_invalid')
            if not 0 < len(pdf) <= MAX_PAGES:
                raise ValueError('pdf_page_limit')
            # Reject invalid/extreme geometry; ordinary oversized pages are
            # uniformly scaled in the processing copy before recognition.
            if any(not all(math.isfinite(v) and 0 < v <= 14400 for v in (p.rect.width, p.rect.height))
                   or max(p.rect.width, p.rect.height) / min(p.rect.width, p.rect.height) > 10 for p in pdf):
                raise ValueError('pdf_page_size_limit')
            if preview:
                temporary = preview.with_suffix('.tmp.webp')
                pdf[0].get_pixmap(matrix=pymupdf.Matrix(*([processing_scale(pdf[0].rect) * 4/3] * 2)), alpha=False).pil_save(temporary, format='WEBP')
                temporary.replace(preview)
            return len(pdf)
    except (pymupdf.FileDataError, RuntimeError) as error:
        raise ValueError('pdf_locked_or_invalid') from error


def processing_scale(rect):
    # Stay below Audiveris's 20 MP ceiling even on the 400 DPI retry.
    return min(1., 842 / max(rect.width, rect.height)) if rect.width * rect.height * (400 / 72)**2 > 20_000_000 else 1.


def processing_copy(source, target):
    import pymupdf
    with pymupdf.open(source) as pdf:
        if all(processing_scale(p.rect) == 1 for p in pdf):
            shutil.copyfile(source, target)
            return False
        with pymupdf.open() as output:
            for page in pdf:
                scale = processing_scale(page.rect)
                dest = output.new_page(width=page.rect.width * scale, height=page.rect.height * scale)
                # Copies vector content in displayed crop coordinates, including rotation.
                if page.get_contents(): dest.show_pdf_page(dest.rect, pdf, page.number)
            output.save(target, garbage=4, deflate=True)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--preview', type=Path)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))
    # macOS does not reliably implement an address-space limit. The local
    # service is not an OS sandbox; production workers still need one.
    if sys.platform.startswith('linux'):
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    code = 0
    try: result = {'pageCount': inspect_pdf(args.source, args.preview)}
    except ValueError as error:
        result = {'error': str(error)}; code = 1
    raw = json.dumps(result)
    if args.result: args.result.write_text(raw)
    else: print(raw)
    sys.exit(code)
