#!/usr/bin/env python3
"""Resumable local catalogue preparation. Never publishes or modifies sources.

Audiveris runs in bounded workers; MuseScore exports are serialized. Every score
gets a durable outcome. A bad input cannot prevent other scores from preparing.
Run again to resume; --retry-failed explicitly retries unchanged failures.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from zipfile import ZipFile, BadZipFile

from livesheet import playback as playback
from livesheet import audiveris_export_recovery as export_recovery
from livesheet.pdf_staff_evidence import inspect as inspect_staves
from livesheet.score_recognition import build_score

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path('data/recognition')
RESULTS = Path('data/reports')
STAGING = Path('data/staging')
AUDIVERIS = Path(os.environ.get('PDF_AUDIVERIS', '/opt/audiveris/bin/Audiveris'))
TESSDATA = Path(os.environ.get('PDF_TESSDATA', '/opt/tessdata'))
renderer_lock = threading.Lock()
stop = threading.Event()
processes = set()
process_lock = threading.Lock()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)



def recognition_profile(dpi):
    # A4 at 600 DPI is about 35 MP, above Audiveris's default 20 MP guard.
    # Keep an explicit ceiling rather than disabling the size check.
    return (f'{dpi}dpi-v1', 20000000) if dpi < 600 else (f'{dpi}dpi-v2-40mp', 40000000)

def recognition_timeout(page_count, override=None):
    return override if override is not None else min(3600, max(900, page_count * 120))

def longer_timeout_available(prior, timeout):
    match = re.fullmatch(r'engine timed out after (\d+)s', prior.get('error', ''))
    return bool(match and timeout > int(match[1]))

def interrupted_attempt(prior):
    return (prior.get('status') == 'interrupted'
            or re.match(r'engine exited -(?:2|15);', prior.get('error', '')) is not None)

def step_timeout_limit(folder):
    log = folder/'attempt/run.log'
    if not log.exists():
        return None
    limits = re.findall(r'Timeout (\d+) seconds for step ', log.read_text(errors='replace'))
    return int(limits[-1]) if limits else None

def retry_step_timeout(prior, folder, budget):
    limit = step_timeout_limit(folder)
    return bool(prior.get('recognitionStepTimeout', 120) < budget and limit and limit < budget)

def resumable_book(path, source, page_count, prior, fingerprint, recognition_fingerprint=None, step_timed_out=False):
    matches = (prior.get('fingerprint') == fingerprint or
               (recognition_fingerprint is not None and prior.get('recognitionFingerprint') == recognition_fingerprint))
    if (not matches or not path.exists()
            or not (interrupted_attempt(prior) or prior.get('error', '').startswith('engine timed out after ') or step_timed_out)):
        return False
    try:
        with ZipFile(path) as archive:
            if archive.testzip() is not None:
                return False
            book = ET.fromstring(archive.read('book.xml'))
            for sheet in book.findall('sheet'):
                steps = (sheet.findtext('steps') or '').split()
                prefix = f"sheet#{sheet.get('number')}"
                if steps and f'{prefix}/{prefix}.xml' not in archive.namelist():
                    return False
                if 'BINARY' in steps and f'{prefix}/BINARY.png' not in archive.namelist():
                    return False
        return (Path(book.get('path', '')).resolve() == source.resolve()
                and len(book.findall('sheet')) == page_count)
    except (OSError, BadZipFile, KeyError, ET.ParseError):
        return False

def run(command, log, timeout, env=None):
    with log.open('w') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
        with process_lock:
            processes.add(process)
        try:
            code = process.wait(timeout=timeout)
            if code:
                raise RuntimeError(f'engine exited {code}; see {log}')
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise RuntimeError(f'engine timed out after {timeout}s') from None
        finally:
            with process_lock:
                processes.discard(process)


def valid_box(box):
    return (len(box) == 4 and all(math.isfinite(v) for v in box)
            and min(box[:2]) >= 0 and min(box[2:]) > 0
            and box[0]+box[2] <= 1.01 and box[1]+box[3] <= 1.01)


def validate_package(package, page_count):
    """Check playable packages before they can enter the browser asset index."""
    if not package['ready']:
        raise ValueError(', '.join(package['issues']) or 'playback unavailable')
    if package['format'] == 'pdf' and len(package['pages']) != page_count:
        raise ValueError('playback page count differs from source PDF')
    if not package['notes'] or not package['measures'] or not math.isfinite(package['duration']):
        raise ValueError('empty or non-finite performance')
    ids = {m['id'] for m in package['measures']}
    if len({n['id'] for n in package['notes']}) != len(package['notes']):
        raise ValueError('duplicate playback note IDs')
    for collection in ('notes', 'measures', 'cursors'):
        previous = -1
        for event in package[collection]:
            start = event['start']
            if not math.isfinite(start) or start < previous or start < 0:
                raise ValueError(f'invalid {collection} ordering')
            previous = start
            if not isinstance(event['page'], int) or not 0 <= event['page'] < len(package['pages']):
                raise ValueError('event outside source pages')
            if 'box' in event and not valid_box(event['box']):
                raise ValueError('event outside source geometry')
            if collection == 'notes':
                if (event['measureId'] not in ids or not 21 <= event['midi'] <= 108
                        or int(event['midi']) != event['midi'] or not math.isfinite(event['velocity']) or not 0 < event['velocity'] <= 127
                        or not .001 <= event['duration'] <= 120
                        or start + event['duration'] > package['duration']+.002):
                    raise ValueError(f"invalid piano event: {event['id']} in {event['measureId']}, MIDI {event['midi']}, duration {event['duration']}s")
            if collection == 'measures' and not start < event['end'] <= package['duration']+.002:
                raise ValueError('invalid measure duration')



def promote(staged, target):
    """Replace a validated directory, with recovery if the rename fails."""
    # Keep the rollback directory on the destination's filesystem and within
    # this job. A repository-global staging folder can cross filesystems.
    backup = target.with_name(target.name+'.previous')
    target.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        target.rename(backup)
    try:
        staged.rename(target)
    except Exception:
        if backup.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def pages_intact(target, prior):
    hashes = prior.get('pageSHA256', {})
    return bool(hashes) and all((target.parent/name).exists() and sha(target.parent/name)==digest
                                for name,digest in hashes.items())

def default_config():
    from livesheet.pdf_pipeline.config import RecognitionConfig
    return RecognitionConfig(CACHE, RESULTS, STAGING, TESSDATA, playback.default_config())


def process_entry(entry, args, engine_fingerprint, adapter_fingerprint, progress=None, *, config=None):
    config = config or default_config()
    from livesheet.pdf_pipeline.cache import read_cache_object
    ident = entry['id']
    result_path = config.results / 'scores' / f'{ident}.json'
    source = config.playback.bundle / entry['assetPath']
    source_hash = sha(source)
    if source_hash != entry['sha256']:
        raise ValueError(f'{ident}: source hash mismatch')
    target = config.playback.public / ident / 'score.json'
    prior = read_cache_object(result_path)
    dpi = getattr(args, 'pdf_dpi', None) or ((prior.get('recognitionDPI') or 300) if prior.get('status')=='ready' and prior.get('sourceSHA256')==source_hash else 300)
    profile_key, pixel_limit = recognition_profile(dpi)
    engine_fingerprint += profile_key
    folder = config.cache / ident
    if dpi != 300:
        folder /= f'dpi-{dpi}'
    folder.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256((source_hash+engine_fingerprint+adapter_fingerprint).encode()).hexdigest()
    staff_evidence = inspect_staves(source) if entry['format']=='pdf' else {'constants': {}}
    staff_constants = staff_evidence['constants']
    from livesheet import pdf_ocr_guard as pdf_ocr_guard
    ocr_evidence = pdf_ocr_guard.inspect(source) if entry['format']=='pdf' else {'rests': []}
    fingerprint = hashlib.sha256((fingerprint+pdf_ocr_guard.fingerprint()).encode()).hexdigest()
    recognition_config = json.dumps(staff_constants, sort_keys=True) if staff_constants else ''
    recognition_key = hashlib.sha256((source_hash+engine_fingerprint+recognition_config+pdf_ocr_guard.fingerprint()).encode()).hexdigest()
    timeout = recognition_timeout(entry['pageCount'], getattr(args, 'timeout', None))
    step_timeout = getattr(args, 'step_timeout', 300)
    profile_result = folder/'preparation.json'
    if prior.get('fingerprint') != fingerprint and profile_result.exists():
        prior = read_cache_object(profile_result)
    if prior.get('fingerprint') == fingerprint and not args.force and not getattr(args,'rerun_recognition',False):
        if prior.get('status') == 'ready' and target.exists() and prior.get('packageSHA256') == sha(target) and pages_intact(target, prior):
            validate_package(json.loads(target.read_text()), entry['pageCount'])
            return {**prior, 'cached': True}
        if (prior.get('status') == 'failed' and not args.retry_failed
                and not interrupted_attempt(prior) and not longer_timeout_available(prior, timeout)
                and not retry_step_timeout(prior, folder, step_timeout)):
            return {**prior, 'cached': True}
    if stop.is_set():
        return {'id': ident, 'status': 'interrupted'}
    if shutil.disk_usage(config.cache).free < args.min_free_gb * 1024**3:
        stop.set()
        return {'id': ident, 'status': 'deferred', 'error': 'storage floor reached'}
    started = time.monotonic()
    result = {'id': ident, 'format': entry['format'], 'fingerprint': fingerprint,
              'sourceSHA256': source_hash, 'recognitionDPI':dpi if entry['format']=='pdf' else None,
              'recognitionFingerprint':recognition_key if entry['format']=='pdf' else None,
              'staffDetection':staff_evidence, 'printedRestOCR':ocr_evidence,
              'recognitionTimeout':timeout, 'recognitionStepTimeout':step_timeout,
              'updatedAt': datetime.now(timezone.utc).isoformat()}
    # Recover an interrupted directory replacement before reading the old
    # package. The second path supports jobs left by the earlier staging layout.
    backups = (target.parent.with_name(ident+'.previous'), config.staging / (ident+'.previous'))
    for backup in backups:
        if backup.exists() and not target.parent.exists():
            target.parent.parent.mkdir(parents=True, exist_ok=True)
            backup.rename(target.parent)
    previous_package = read_cache_object(target)
    same_source = previous_package.get('sourceSHA256') == source_hash
    previous_ready = same_source and previous_package.get('ready', False)
    try:
        mapping = None
        if entry['format'] == 'pdf':
            if progress: progress('recognizing', dpi)
            stamp = folder / 'recognition.json'
            mxl, omr = folder/f'{ident}.mxl', folder/f'{ident}.omr'
            existing = read_cache_object(stamp)
            exports = existing.get('musicxmlFiles', [{'file':mxl.name,'sha256':existing.get('mxlSHA256')}])
            reusable = (not getattr(args,'rerun_recognition',False) and existing.get('fingerprint') == recognition_key and omr.exists()
                        and existing.get('omrSHA256') == sha(omr) and exports
                        and all((folder/e['file']).exists() and sha(folder/e['file'])==e['sha256'] for e in exports))
            if not reusable:
                # Preserve and reuse the exact original trial inputs/outputs.
                trial_mxl = config.playback.trial/'recognition'/f'{ident}.mxl'
                trial_omr = config.playback.trial/'recognition'/f'{ident}.omr'
                trial_manifest = json.loads((config.playback.trial/'manifest.json').read_text()) if (config.playback.trial/'manifest.json').exists() else []
                seed = next((r for r in trial_manifest if r['id'] == ident and r['sha256'] == source_hash), None)
                if dpi == 300 and not staff_constants and not ocr_evidence['rests'] and not getattr(args,'rerun_recognition',False) and seed and trial_mxl.exists() and trial_omr.exists():
                    shutil.copy2(trial_mxl, mxl); shutil.copy2(trial_omr, omr)
                    shutil.copy2(config.playback.trial/'run.log', folder/'run.log')
                    export_names = [mxl.name]
                else:
                    env = {**os.environ, 'TESSDATA_PREFIX': str(config.tessdata),
                           'JAVA_TOOL_OPTIONS': '-XX:ActiveProcessorCount=2',
                           '_JAVA_OPTIONS': '-Xmx2g'}
                    attempt = folder/'attempt'
                    partial = attempt/omr.name
                    resume = (not getattr(args,'rerun_recognition',False)
                              and resumable_book(partial, source, entry['pageCount'], prior, fingerprint,
                                                 recognition_key, step_timeout_limit(folder) is not None))
                    history = (attempt/'run.log').read_text(errors='replace') if resume and (attempt/'run.log').exists() else ''
                    if attempt.exists() and not resume:
                        shutil.rmtree(attempt)
                    attempt.mkdir(exist_ok=True)
                    recognition_command, ocr_env = pdf_ocr_guard.command(args.audiveris, ocr_evidence, folder)
                    env.update(ocr_env)
                    result['resumedRecognition'] = resume
                    try:
                        run([*recognition_command, '-batch', '-transcribe', '-export', '-swap',
                             '-constant', f'org.audiveris.omr.image.ImageLoading.pdfResolution={dpi}',
                             '-constant', f'org.audiveris.omr.step.LoadStep.maxPixelCount={pixel_limit}',
                             '-constant', f'org.audiveris.omr.Main.sheetStepTimeOut={step_timeout}',
                             *[arg for key,value in sorted(staff_constants.items()) for arg in ('-constant', f'{key}={value}')],
                             '-output', str(attempt), '--', str(partial if resume else source)], attempt/'run.log',
                            timeout, env)
                    finally:
                        if history:
                            log = attempt/'run.log'
                            log.write_text(history+'\n--- Resumed recognition ---\n'+log.read_text(errors='replace'))
                    export_names = [mxl.name] if (attempt/mxl.name).exists() else [p.name for p in sorted(attempt.glob(f'{ident}.mvt*.mxl'), key=lambda p:int(re.search(r'\.mvt(\d+)\.mxl$',p.name)[1]))]
                    if not export_names or not (attempt/omr.name).exists():
                        raise ValueError('recognition did not export a complete score; see attempt outputs')
                    if export_names != [mxl.name] and export_names != [f'{ident}.mvt{i+1}.mxl' for i in range(len(export_names))]:
                        raise ValueError('recognition omitted a movement')
                    for name in export_names + [omr.name, 'run.log']:
                        (attempt/name).replace(folder/name)
                exports = [{'file':name,'sha256':sha(folder/name)} for name in export_names]
                atomic_json(stamp, {'fingerprint': recognition_key, 'sourceSHA256': source_hash,
                                    'musicxmlFiles':exports, 'omrSHA256': sha(omr)})
            musicxml_paths = [folder/e['file'] for e in exports]
            export_log = (folder/'run.log').read_text(errors='replace')
            if export_recovery.needed(export_log, musicxml_paths):
                musicxml_paths, recovery = export_recovery.recover(
                    folder, ident, args.audiveris, exports, run,
                    {**os.environ, 'TESSDATA_PREFIX':str(config.tessdata)})
                result['exportRecovery'] = recovery
            if progress: progress('mapping', dpi)
            mapping = build_score(folder, {'id': ident, 'pdf': str(source), 'sha256': source_hash,
                                          'previews': [str(config.playback.bundle/p) for p in entry['previews']],
                                          'musicxmlFiles':[str(p) for p in musicxml_paths]})
            if 'exportRecovery' in result:
                mapping['exportRecovery'] = result['exportRecovery']
            if getattr(args, 'recover_terminal_measure', False) and len(mapping['musicxmlFiles']) == 1:
                from livesheet.pdf_terminal_recovery import recover as recover_terminal
                from livesheet.pdf_rhythm_repair import Decline
                atomic_json(folder/'original-mapping.json', mapping)
                terminal_folder = folder/'terminal-recovery'
                try:
                    recovered, terminal_report = recover_terminal(Path(mapping['musicxmlFiles'][0]),
                        terminal_folder/f'{ident}.mxl', source, mapping, omr)
                    mapping = recovered
                    result['terminalRecovery'] = terminal_report
                except Decline as error:
                    terminal_report = dict(status='declined', reason=str(error))
                terminal_folder.mkdir(exist_ok=True)
                atomic_json(terminal_folder/'report.json', terminal_report)
            atomic_json(folder/'mapping.json', mapping)
            result['recognition'] = mapping['summary']
            result['recognitionArtifacts'] = {'musicxmlFiles':[
                dict(file=str(p.relative_to(folder)), sha256=sha(p)) for p in musicxml_paths], 'omrSHA256':sha(omr)}
        with renderer_lock:
            if stop.is_set():
                return {**result, 'status': 'interrupted'}
            if (config.staging/ident).exists():
                shutil.rmtree(config.staging/ident)
            if progress: progress('performance', dpi)
            playback.prepare(entry, mapping, True, folder, config.staging, config=config.playback)
            staged = config.staging / ident
            package = json.loads((staged/'score.json').read_text())
            if progress: progress('validating', dpi)
            validate_package(package, entry['pageCount'])
            if result.get('terminalRecovery'):
                from livesheet.pdf_terminal_recovery import validate_traversal
                validate_traversal(package, result['terminalRecovery'])
                package['provenance']['terminalRecovery'] = result['terminalRecovery']
                atomic_json(staged/'score.json', package)
            promote(staged, target.parent)
        result.update(status='ready', packageSHA256=sha(target), duration=package['duration'],
                      notes=len(package['notes']), issues=package['issues'], mapping=package.get('mapping'),
                      pageSHA256={Path(page['url']).name:sha(target.parent/Path(page['url']).name) for page in package['pages']})
    except Exception as error:
        (folder/'failure.log').write_text(traceback.format_exc())
        result.update(status='interrupted' if stop.is_set() else 'failed', error=str(error) or type(error).__name__, previousReady=previous_ready)
        if (not target.exists() or not same_source) and not stop.is_set():
            # A fresh catalogue still needs readable pages for a withheld score.
            try:
                if (config.staging/ident).exists():
                    shutil.rmtree(config.staging/ident)
                playback.prepare(entry, None, False, folder, config.staging, config=config.playback)
                fallback_path = config.staging/ident/'score.json'
                fallback = json.loads(fallback_path.read_text())
                fallback['issues'] = ['incomplete-playback' if 'incomplete-playback' in str(error) else 'preparation-failed']
                atomic_json(fallback_path, fallback)
                promote(config.staging/ident, target.parent)
            except Exception as fallback_error:
                result['pageFallbackError'] = str(fallback_error)

    result['seconds'] = round(time.monotonic()-started, 2)
    atomic_json(result_path, result)
    atomic_json(profile_result, result)
    return result



def prepare_with_retries(entry, args, engine_fingerprint, adapter_fingerprint, progress=None, *, config=None):
    """Try bounded raster resolutions without changing the musical content."""
    config = config or default_config()
    outcome = process_entry(entry, args, engine_fingerprint, adapter_fingerprint, progress, config=config)
    if (entry['format'] != 'pdf' or outcome['status'] != 'failed'
            or outcome.get('previousReady') or args.pdf_dpi is not None or stop.is_set()):
        return outcome
    attempts = [{k:outcome[k] for k in ['recognitionDPI','status','error','seconds','cached'] if k in outcome}]
    initial_dpi = outcome.get('recognitionDPI',300)
    for dpi in dict.fromkeys(args.retry_profiles):
        if dpi == initial_dpi or stop.is_set():
            continue
        retry_args = argparse.Namespace(**vars(args))
        retry_args.pdf_dpi = dpi
        outcome = process_entry(entry, retry_args, engine_fingerprint, adapter_fingerprint, progress, config=config)
        attempts.append({k:outcome[k] for k in ['recognitionDPI','status','error','seconds','cached'] if k in outcome})
        if outcome['status'] != 'failed':
            break
    outcome = {**outcome,'attempts':attempts}
    atomic_json(config.results/'scores'/f"{entry['id']}.json",outcome)
    return outcome
