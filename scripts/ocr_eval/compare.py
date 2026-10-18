#!/usr/bin/env python3
"""Local, read-only evidence replay. See docs/OCR_ENGINE_COMPARISON.md."""
import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import statistics
import threading
import time
import unicodedata

PROFILES = ("profile_002", "profile_003")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def normalize(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def edit_distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        row = [i]
        for j, b in enumerate(right, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j-1] + (a != b)))
        previous = row
    return previous[-1]


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def inside(point, box):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def normalize_items(boxes, texts, scores, scale=1., offset=(0, 0)):
    if not (len(boxes) == len(texts) == len(scores)):
        raise ValueError("engine returned inconsistent boxes/texts/scores")
    return [{"box": [[float(x)/scale+offset[0], float(y)/scale+offset[1]] for x, y in box],
             "text": str(text), "confidence": float(score)}
            for box, text, score in zip(boxes, texts, scores)]


def prepare(image, region=None, max_dimension=1280):
    import cv2
    height, width = image.shape[:2]
    x1, y1, x2, y2 = region or (0, 0, width, height)
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("invalid crop bounds")
    crop = image[y1:y2, x1:x2]
    scale = min(1., max_dimension / max(crop.shape[:2]))
    if scale < 1:
        crop = cv2.resize(crop, (round(crop.shape[1]*scale), round(crop.shape[0]*scale)),
                          interpolation=cv2.INTER_AREA)
    return crop, scale, (x1, y1)


def inventory(args):
    import cv2
    destination = args.output.resolve()
    sources = [p.resolve() for p in args.source]
    for source in sources:
        if destination == source or source in destination.parents:
            raise ValueError("output must be outside the source runtime")
    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        raise ValueError("snapshot already exists; choose a fresh output directory")
    records, errors, seen = [], [], {}
    for source in sources:
        for profile in PROFILES:
            evidence = source / "profiles" / profile / "automation_evidence"
            for path in sorted(evidence.glob("*/*.png")):
                image = cv2.imread(str(path))
                if image is None:
                    errors.append({"source": str(path), "error": "image_unreadable"})
                    continue
                sha = digest(path)
                if sha in seen:
                    seen[sha]["sources"].append(str(path))
                    continue
                # A conservative visual fingerprint is used only for split grouping,
                # never to delete images or assume their timestamps are equivalent.
                gray = cv2.cvtColor(cv2.resize(image, (33, 32)), cv2.COLOR_BGR2GRAY)
                bits = (gray[:, 1:] > gray[:, :-1]).flatten()
                dhash = sum(int(bit) << i for i, bit in enumerate(bits))
                metadata = {}
                if path.with_suffix('.json').exists():
                    try:
                        metadata = json.loads(path.with_suffix('.json').read_text())
                    except (ValueError, OSError) as exc:
                        errors.append({"source": str(path), "error": str(exc)})
                item = {"id": sha[:16], "sha256": sha, "sources": [str(path)],
                        "profile": profile, "run": path.parent.name,
                        "stage": metadata.get("label", path.stem.split('_', 1)[-1]),
                        "width": image.shape[1], "height": image.shape[0],
                        "bytes": path.stat().st_size, "dhash": hex(dhash),
                        "target_hint": metadata.get("target"),
                        "image": f"images/{sha}.png"}
                seen[sha] = item
                records.append(item)
                target = destination / item['image']
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                if digest(target) != sha:
                    raise RuntimeError("snapshot hash mismatch")
    # Union whole runs, exact-duplicate source runs, and near-duplicate frames.
    parent = list(range(len(records)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def union(i, j):
        parent[find(j)] = find(i)
    runs = {}
    for i, item in enumerate(records):
        for source in item['sources']:
            run = str(Path(source).parent)
            if run in runs:
                union(i, runs[run])
            runs[run] = i
        for j in range(i):
            if (int(item['dhash'], 16) ^ int(records[j]['dhash'], 16)).bit_count() <= 2:
                union(i, j)
    groups = defaultdict(list)
    for i, item in enumerate(records):
        groups[find(i)].append(item)
    for members in groups.values():
        key = min(item['id'] for item in members)
        split = 'tuning' if int(key[:8], 16) % 5 == 0 else 'held_out'
        for item in members:
            item.update(group=key, split=split)
    buckets = defaultdict(list)
    rng = random.Random(args.seed)
    for item in records:
        buckets[(item['profile'], item['stage'])].append(item)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    queue = deque(sorted(buckets))
    selected = []
    while queue and len(selected) < args.limit:
        key = queue.popleft()
        selected.append(buckets[key].pop()['id'])
        if buckets[key]:
            queue.append(key)
    result = {"schema_version": 1, "seed": args.seed,
              "profiles": list(PROFILES), "source_roots": [str(p) for p in sources],
              "original_count": sum(len(r['sources']) for r in records),
              "unique_count": len(records), "groups": len(groups),
              "records": records, "selected": selected, "errors": errors}
    write_json(manifest_path, result)
    write_json(destination / 'annotations.json', {"schema_version": 1,
               "manifest_sha256": digest(manifest_path), "labels": []})
    print(json.dumps({k: v for k, v in result.items() if k not in ('records', 'selected')}, indent=2))
    print('Selected:', len(selected), 'splits:', dict(Counter(r['split'] for r in records if r['id'] in selected)))


class Reader:
    def __init__(self, args):
        self.engine = args.engine
        self.lock = threading.Lock()
        if args.engine == 'easyocr':
            import torch
            torch.set_num_threads(args.threads)
            import easyocr
            self.reader = easyocr.Reader(['en'], gpu=False, verbose=False,
                model_storage_directory=str(args.models.resolve()), download_enabled=False)
            self.model_paths = list(args.models.glob('*.pth'))
        else:
            for directory in (args.detection_dir, args.recognition_dir):
                for name in ('inference.json', 'inference.pdiparams', 'inference.yml'):
                    if not (directory/name).is_file():
                        raise ValueError(f'missing local model artifact: {directory/name}')
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
            from paddleocr import PaddleOCR
            self.reader = PaddleOCR(device='cpu', cpu_threads=args.threads,
                text_detection_model_name=args.detection_model,
                text_recognition_model_name=args.recognition_model,
                text_detection_model_dir=str(args.detection_dir.resolve()),
                text_recognition_model_dir=str(args.recognition_dir.resolve()),
                use_doc_orientation_classify=False, use_doc_unwarping=False,
                use_textline_orientation=False,
                text_rec_score_thresh=0.0, enable_mkldnn=args.mkldnn)
            self.model_paths = [p for root in (args.detection_dir, args.recognition_dir)
                                for p in root.rglob('*') if p.is_file()]

    def read(self, image):
        if self.engine == 'easyocr':
            result = self.reader.readtext(image, detail=1, paragraph=False)
            return normalize_items([r[0] for r in result], [r[1] for r in result], [r[2] for r in result])
        result = next(iter(self.reader.predict(image)))
        return normalize_items(result['rec_polys'], result['rec_texts'], result['rec_scores'])


def load_dataset(root):
    manifest = json.loads((root/'manifest.json').read_text())
    annotations = json.loads((root/'annotations.json').read_text())
    if annotations['manifest_sha256'] != digest(root/'manifest.json'):
        raise ValueError('annotations belong to a different manifest')
    records = {r['id']: r for r in manifest['records']}
    label_ids = set()
    for label in annotations['labels']:
        if label['id'] not in records or label['id'] not in manifest['selected']:
            raise ValueError('annotation references an unselected image')
        if label['label_id'] in label_ids or not label.get('expected'):
            raise ValueError('duplicate label ID or empty expected text')
        label_ids.add(label['label_id'])
        record = records[label['id']]
        for box in (label['region'], label.get('target_box', label['region'])):
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= record['width'] and 0 <= y1 < y2 <= record['height']):
                raise ValueError('annotation bounds outside image')
    for record in records.values():
        if digest(root/record['image']) != record['sha256']:
            raise ValueError(f"snapshot modified: {record['id']}")
    return manifest, annotations, records


def run(args):
    import cv2
    import numpy as np
    root = args.dataset.resolve()
    manifest, annotations, records = load_dataset(root)
    if not manifest['selected'] or (args.crops_only and not annotations['labels']):
        raise ValueError('benchmark requires selected images and, for crops-only, annotations')
    manifest_sha = digest(root/'manifest.json')
    annotation_sha = digest(root/'annotations.json')
    name = args.name or args.engine
    if Path(name).name != name or name in ('.', '..'):
        raise ValueError('run name must be a filename stem')
    output = root / f'{name}.json'
    if output.exists():
        raise ValueError('result exists; move it aside explicitly before repeating')
    started = time.perf_counter()
    reader = Reader(args)
    init_ms = (time.perf_counter()-started)*1000
    first = cv2.imread(str(root/records[manifest['selected'][0]]['image']))
    warm = time.perf_counter()
    reader.read(prepare(first, annotations['labels'][0]['region'] if args.crops_only else None)[0])
    warmup_ms = (time.perf_counter()-warm)*1000
    tasks = [] if args.crops_only else [{"id": r, "kind": 'screen', "region": None} for r in manifest['selected']]
    tasks += [{"id": a['id'], "kind": 'crop', "region": a['region'], "label_id": a['label_id']}
              for a in annotations['labels']]
    # Fixed shuffle reduces bias from stage ordering. Same order for both engines.
    random.Random(manifest['seed']).shuffle(tasks)
    samples = []
    for index, task in enumerate(tasks):
        record = records[task['id']]
        image = cv2.imread(str(root/record['image']))
        prepared, scale, offset = prepare(image, task['region'])
        for repeat in range(args.repeats):
            started = time.perf_counter()
            try:
                items = reader.read(prepared)
                elapsed = (time.perf_counter()-started)*1000
                mapped = normalize_items([i['box'] for i in items], [i['text'] for i in items],
                                         [i['confidence'] for i in items], scale, offset)
                samples.append(dict(task, repeat=repeat, duration_ms=elapsed, items=mapped,
                                    split=record['split'], stage=record['stage']))
            except Exception as exc:
                samples.append(dict(task, repeat=repeat, error=f'{type(exc).__name__}: {exc}',
                                    split=record['split'], stage=record['stage']))
        if index % 10 == 0:
            print(f"{args.engine}: {index+1}/{len(tasks)} inputs", flush=True)
        write_json(root/f'{name}.partial.json', {'samples': samples})
    # Offline service simulation: bounded concurrent clients, one inference lock,
    # uncached prepared inputs. This is not a live multi-profile throughput test.
    queued = []
    prepared_inputs = [prepare(cv2.imread(str(root/records[t['id']]['image'])), t['region'])[0]
                       for t in tasks if t['kind'] == 'crop'][:12]
    def request(image):
        arrival = time.perf_counter()
        with reader.lock:
            admitted = time.perf_counter()
            reader.read(image)
            finished = time.perf_counter()
        return {'wait_ms': (admitted-arrival)*1000, 'inference_ms': (finished-admitted)*1000,
                'response_ms': (finished-arrival)*1000}
    queue_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        queued = list(pool.map(request, prepared_inputs*2))
    queue_seconds = time.perf_counter()-queue_start
    versions = {}
    for name in ('easyocr', 'torch', 'paddleocr', 'paddlepaddle', 'paddlex', 'numpy', 'opencv-python', 'opencv-python-headless'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    if digest(root/'manifest.json') != manifest_sha or digest(root/'annotations.json') != annotation_sha:
        raise ValueError('dataset or annotations changed during benchmark')
    payload = {'engine': args.engine, 'manifest_sha256': manifest_sha,
               'annotations_sha256': annotation_sha,
               'initialization_ms': init_ms, 'first_warmup_ms': warmup_ms,
               'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
               'platform': platform.platform(), 'cpu': platform.processor(),
               'cpu_count': os.cpu_count(), 'threads': args.threads, 'device': 'cpu',
               'repeats': args.repeats, 'max_dimension': 1280,
               'crops_only': args.crops_only,
               'versions': versions, 'model_hashes': {str(p): digest(p) for p in reader.model_paths},
               'paddle_models': [args.detection_model, args.recognition_model] if args.engine == 'paddleocr' else None,
               'mkldnn': args.mkldnn if args.engine == 'paddleocr' else None,
               'queue': {'clients': args.clients, 'samples': queued,
                         'throughput_per_second': len(queued)/queue_seconds}, 'samples': samples}
    write_json(output, payload)
    print('Saved:', output)


def score_label(label, items, threshold):
    expected = [normalize(t) for t in label['expected']]
    def transcription(item):
        value = normalize(item['text'])
        # The production comment-input matcher deliberately uses the English
        # anchor before the variable account name. Preserve that label meaning.
        if label.get('category') == 'input' and expected == ['comment as'] and value.startswith('comment as '):
            return 'comment as'
        return value
    target = label.get('target_box', label['region'])
    # Geometry restricts evaluation to the labeled target, rather than counting
    # every unrelated text line in a screenshot as an OCR false positive.
    candidates = []
    for item in items:
        center = [sum(p[d] for p in item['box'])/len(item['box']) for d in (0, 1)]
        if item['confidence'] >= threshold and inside(center, target):
            candidates.append(item)
    correct = any(transcription(i) in expected for i in candidates)
    ordered = sorted(candidates, key=lambda i: (min(p[1] for p in i['box'])//12, min(p[0] for p in i['box'])))
    joined = normalize(' '.join(i['text'] for i in ordered))
    exact = correct or joined in expected
    if label.get('absent'):
        return {'exact': not exact, 'char_errors': 0, 'chars': 0,
                'word_errors': 0, 'words': 0, 'false_match': exact,
                'predicted': joined}
    best = min([transcription(i) for i in candidates]+[joined],
               key=lambda t: min(edit_distance(t, e) for e in expected))
    char_errors = min(edit_distance(best, e) for e in expected)
    word_errors = min(edit_distance(best.split(), e.split()) for e in expected)
    return {'exact': exact, 'char_errors': char_errors, 'chars': len(expected[0]),
            'word_errors': word_errors, 'words': len(expected[0].split()),
            'false_match': bool(candidates) if label.get('absent') else False,
            'predicted': best}


def summarize(args):
    root = args.dataset.resolve()
    manifest, annotations, records = load_dataset(root)
    result = {'dataset': {'original': manifest['original_count'], 'unique': manifest['unique_count'],
                         'selected': len(manifest['selected']), 'labels': len(annotations['labels']),
                         'split_counts': dict(Counter(records[r]['split'] for r in manifest['selected']))},
              'engines': {}}
    for engine in args.runs:
        run = json.loads((root/f'{engine}.json').read_text())
        if run['manifest_sha256'] != digest(root/'manifest.json') or run['annotations_sha256'] != digest(root/'annotations.json'):
            raise ValueError('results use different dataset/labels')
        entry = {k: run[k] for k in ('initialization_ms','first_warmup_ms','peak_rss_mib','versions','threads','device')}
        entry['errors'] = sum('error' in s for s in run['samples'])
        entry['latency'] = {}
        for kind in ('screen', 'crop'):
            times = [s['duration_ms'] for s in run['samples'] if s['kind']==kind and 'duration_ms' in s]
            entry['latency'][kind] = {'n': len(times), 'median_ms': statistics.median(times) if times else None,
                                     'p95_ms': percentile(times, .95)}
        entry['accuracy'] = {}
        by_key = {(s.get('label_id'), s['kind']): s for s in run['samples'] if s['repeat']==0}
        screens = {s['id']: s for s in run['samples'] if s['kind']=='screen' and s['repeat']==0}
        for split in ('tuning', 'held_out'):
            scores = []
            for label in annotations['labels']:
                if records[label['id']]['split'] != split:
                    continue
                sample = by_key.get((label['label_id'], 'crop'), {})
                scored = score_label(label, sample.get('items', []), args.threshold)
                screen_score = score_label(label, screens.get(label['id'], {}).get('items', []), args.threshold)
                if not sample or 'error' in sample:
                    scored['exact'] = False
                if not screens.get(label['id']) or 'error' in screens[label['id']]:
                    screen_score['exact'] = False
                scores.append(dict(scored, label_id=label['label_id'], category=label['category'],
                                   screen_exact=screen_score['exact']))
            entry['accuracy'][split] = {'n': len(scores), 'exact': sum(s['exact'] for s in scores),
                'positive_n': sum(s['category']!='negative' for s in scores),
                'positive_exact': sum(s['category']!='negative' and s['exact'] for s in scores),
                'positive_screen_exact': sum(s['category']!='negative' and s['screen_exact'] for s in scores),
                'cer': sum(s['char_errors'] for s in scores)/max(1,sum(s['chars'] for s in scores)),
                'wer': sum(s['word_errors'] for s in scores)/max(1,sum(s['words'] for s in scores)),
                'screen_exact': sum(s['screen_exact'] for s in scores),
                'negative_n': sum(s['category']=='negative' for s in scores),
                'false_matches': sum(s['false_match'] for s in scores),
                'by_category': {category: {'n':sum(s['category']==category for s in scores),
                    'exact':sum(s['category']==category and s['exact'] for s in scores)}
                    for category in sorted({s['category'] for s in scores})},
                'failures': [s for s in scores if not s['exact']]}
        q = run['queue']
        entry['queue'] = {'clients': q['clients'], 'throughput_per_second': q['throughput_per_second'],
            **{k: percentile([s[k] for s in q['samples']], .95) for k in ('wait_ms','inference_ms','response_ms')}}
        result['engines'][engine] = entry
    result['threshold'] = args.threshold
    write_json(root/'summary.json', result)
    lines = ['# Local OCR comparison results', '',
             f"Inputs: {len(manifest['selected'])} screens; {len(annotations['labels'])} reviewed labels. CPU only, four threads by default.", '',
             '| Run | Held-out positive crop hits | Held-out positive screen hits | Screen median / p95 | Crop median / p95 | Peak RSS |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    def latency(value):
        return '-' if value is None else f'{value:.0f}'
    for name, entry in result['engines'].items():
        acc = entry['accuracy']['held_out']
        screen, crop = entry['latency']['screen'], entry['latency']['crop']
        lines.append(f"| {name} | {acc['positive_exact']}/{acc['positive_n']} | {acc['positive_screen_exact']}/{acc['positive_n']} | "
                     f"{latency(screen['median_ms'])} / {latency(screen['p95_ms'])} ms | "
                     f"{latency(crop['median_ms'])} / {latency(crop['p95_ms'])} ms | {entry['peak_rss_mib']:.0f} MiB |")
    lines += ['', 'These are target-region transcription hits on positive labels; negative cases are reported separately in summary.json. They do not measure complete action safety.',
              'Labels on the same screen are correlated. No confidence tuning was performed. See summary.json for categories and failures.',
              'No light-theme, GPU, current-UI, old/conflicting-timestamp, or platform packaging conclusions follow from this dataset.',
              'Production OCR remains unchanged.', '']
    (root/'REPORT.md').write_text('\n'.join(lines))
    print(json.dumps(result, indent=2))


def fallback(args):
    """Replay the existing bounded consensus implementation on saved crop reads."""
    import cv2
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'automation'))
    from engine.vision import VisionEngine
    root = args.dataset.resolve()
    manifest, annotations, records = load_dataset(root)
    primary = json.loads((root/f'{args.primary_run}.json').read_text())
    if primary['annotations_sha256'] != digest(root/'annotations.json') or primary['manifest_sha256'] != digest(root/'manifest.json'):
        raise ValueError('primary results do not match dataset')
    reads = {s['label_id']: s for s in primary['samples'] if s['kind']=='crop' and s['repeat']==0}
    vision = VisionEngine.__new__(VisionEngine)
    vision.telemetry = None
    vision._secondary_ocr_cache = {}
    observations = []
    for label in annotations['labels']:
        # Full timestamp gates have additional old/conflict checks and cannot
        # be inferred from a labeled transcription crop.
        if label['category'] in ('negative', 'timestamp'):
            continue
        sample = reads[label['label_id']]
        if 'error' in sample:
            observations.append({'label_id':label['label_id'], 'error':sample['error']})
            continue
        items = sample['items']
        before = score_label(label, items, args.threshold)['exact']
        def adapt(item):
            if 'box' in item:
                return item
            l,t,r,b = item['bounds']
            return dict(item,box=[[l,t],[r,t],[r,b],[l,b]])
        def accept(item):
            return score_label(label, [adapt(item)], args.threshold)['exact']
        image = cv2.imread(str(root/records[label['id']]['image']))
        x1,y1,x2,y2 = label['region']
        # Reset cache for each case so recovery timing is uncached.
        vision._secondary_ocr_cache = {}
        started = time.perf_counter()
        recovered = vision.read_text_with_fallback(image, region=(x1,y1,x2-x1,y2-y1),
            accept=accept, primary=[i for i in items if i['confidence']>=args.threshold],
            min_confidence=.60, psm=11)
        elapsed = (time.perf_counter()-started)*1000
        observations.append({'label_id': label['label_id'],
            'split': records[label['id']]['split'], 'primary_exact': before,
            'combined_exact': score_label(label,[adapt(i) for i in recovered],args.threshold)['exact'],
            'secondary_used': any(i.get('engine')=='tesseract' for i in recovered),
            'fallback_duration_ms': elapsed})
    result = {'primary_run':args.primary_run, 'threshold':args.threshold,
              'scope':'label crops only; existing consensus, exact labeled acceptance; not full action gates',
              'tesseract_available':shutil.which(os.environ.get('AUTOMATION_TESSERACT_BIN','tesseract')) is not None,
              'observations':observations}
    write_json(root/'fallback.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='observations'},indent=2))
    print('eligible labels',len(observations),'primary',sum(o.get('primary_exact',False) for o in observations),
          'combined',sum(o.get('combined_exact',False) for o in observations))


def timestamps(args):
    """Evaluate timestamp parsing on independently reviewed timestamp crops."""
    import importlib.util
    import sys
    path = Path(__file__).resolve().parents[2]/'automation/modules/post_timestamp.py'
    spec = importlib.util.spec_from_file_location('ocr_eval_timestamp_parser', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    root = args.dataset.resolve()
    manifest, annotations, records = load_dataset(root)
    output = {'scope':'timestamp crop parsing only; not first-post selection or a publication gate',
              'threshold':args.threshold, 'engines':{}}
    for name in args.runs:
        run = json.loads((root/f'{name}.json').read_text())
        if run['annotations_sha256'] != digest(root/'annotations.json') or run['manifest_sha256'] != digest(root/'manifest.json'):
            raise ValueError('timestamp annotations changed')
        reads = {s['label_id']:s for s in run['samples'] if s['kind']=='crop' and s['repeat']==0}
        scores = []
        for label in annotations['labels']:
            if label['category'] != 'timestamp' or 'expected_recent' not in label:
                continue
            sample = reads[label['label_id']]
            ages = []
            for item in sample.get('items',[]):
                center = [sum(p[d] for p in item['box'])/len(item['box']) for d in (0,1)]
                if item['confidence'] < args.threshold or not inside(center,label['target_box']):
                    continue
                stamp = module.parse_header_timestamp(item['text'])
                if stamp:
                    ages.append((stamp.age_seconds,stamp.unit,stamp.recent))
            distinct = set(ages)
            decision = 'unreadable' if not distinct else 'conflicting' if len(distinct)>1 else 'recent' if ages[0][2] else 'old'
            scores.append({'label_id':label['label_id'], 'decision':decision,
                           'expected_recent':label['expected_recent'],
                           'correct':decision==('recent' if label['expected_recent'] else 'old'),
                           'unsafe_recent':decision=='recent' and not label['expected_recent']})
        output['engines'][name] = {'n':len(scores),'correct':sum(s['correct'] for s in scores),
            'unsafe_recent':sum(s['unsafe_recent'] for s in scores),'decisions':dict(Counter(s['decision'] for s in scores)),
            'cases':scores}
    write_json(root/'timestamp-decisions.json',output)
    print(json.dumps(output,indent=2))


def inputs(args):
    """Verify identical prepared pixels across isolated OpenCV environments."""
    import cv2
    root = args.dataset.resolve()
    manifest, annotations, records = load_dataset(root)
    tasks = [(r,None,None) for r in manifest['selected']]
    tasks += [(a['id'],a['label_id'],a['region']) for a in annotations['labels']]
    hashes = {}
    for image_id, label_id, region in tasks:
        image = cv2.imread(str(root/records[image_id]['image']))
        prepared,scale,offset = prepare(image,region)
        key = label_id or image_id
        hashes[key] = {'sha256':hashlib.sha256(prepared.tobytes()).hexdigest(),
                       'shape':list(prepared.shape),'scale':scale,'offset':list(offset)}
    output = root/f'{args.name}-inputs.json'
    if Path(args.name).name != args.name:
        raise ValueError('input fingerprint name must be a filename stem')
    write_json(output,hashes)
    if args.compare:
        other = json.loads(args.compare.read_text())
        if hashes != other:
            raise ValueError('preprocessed inputs differ between environments')
        print('Identical prepared inputs:',len(hashes))
    else:
        print('Fingerprinted prepared inputs:',len(hashes))


def consumers(args):
    """Post-hoc diagnosis using current text rules, without executing actions."""
    import ast
    import importlib.util
    import sys
    code_root = Path(__file__).resolve().parents[2]
    def module(name, path):
        spec = importlib.util.spec_from_file_location(name,path)
        value = importlib.util.module_from_spec(spec)
        sys.modules[name] = value
        spec.loader.exec_module(value)
        return value
    parser = module('ocr_consumer_timestamp',code_root/'automation/modules/post_timestamp.py')
    matcher = module('ocr_consumer_matcher',code_root/'automation/engine/text_matcher.py').OcrTextMatcher(default_threshold=.78)
    tree = ast.parse((code_root/'automation/engine/screen_state.py').read_text())
    aliases = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                   if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PHOTO_VIDEO_PHRASES' for t in node.targets))
    root = args.dataset.resolve()
    manifest,annotations,records = load_dataset(root)
    result = {'scope':'post-hoc text acceptance diagnosis only; no first-post selection, action geometry, or publication gate',
              'rules':{'description':'OcrTextMatcher .78, required your/reel, confidence .18',
                       'photo':'current ScreenState PHOTO_VIDEO_PHRASES, confidence .45',
                       'timestamp':'parse_header_timestamp, confidence .55; conflicts reject',
                       'other':'expected label substring, confidence .45'},'engines':{}}
    for name in args.runs:
        run = json.loads((root/f'{name}.json').read_text())
        if run['manifest_sha256']!=digest(root/'manifest.json') or run['annotations_sha256']!=digest(root/'annotations.json'):
            raise ValueError('consumer diagnosis dataset mismatch')
        samples = {s['label_id']:s for s in run['samples'] if s['kind']=='crop' and s['repeat']==0}
        cases = []
        for label in annotations['labels']:
            expected = normalize(label['expected'][0])
            threshold = .55 if label['category']=='timestamp' else .18 if expected.startswith('describe your reel') else .45
            items = []
            for item in samples[label['label_id']].get('items',[]):
                center = [sum(p[d] for p in item['box'])/len(item['box']) for d in (0,1)]
                if item['confidence']>=threshold and inside(center,label['target_box']):
                    items.append(item)
            if label['category']=='timestamp':
                wanted = parser.parse_header_timestamp(expected)
                stamps = [parser.parse_header_timestamp(i['text']) for i in items]
                ages = {(s.age_seconds,s.unit) for s in stamps if s}
                accepted = wanted is not None and ages=={(wanted.age_seconds,wanted.unit)}
            elif expected.startswith('describe your reel'):
                accepted = any(matcher.evaluate(expected='Describe your reel…',received=i['text'],required_tokens=('your','reel')).matched for i in items)
            elif expected=='photo/video':
                accepted = any(any(alias in i['text'].casefold() for alias in aliases) for i in items)
            else:
                accepted = any(any(normalize(e) in normalize(i['text']) for e in label['expected']) for i in items)
            if label.get('absent'):
                accepted = not accepted and 'error' not in samples[label['label_id']]
            cases.append({'label_id':label['label_id'],'category':label['category'],
                          'split':records[label['id']]['split'],'accepted':accepted})
        held = [c for c in cases if c['split']=='held_out' and c['category']!='negative']
        result['engines'][name]={'held_out_positive_n':len(held),'accepted':sum(c['accepted'] for c in held),
            'by_category':{category:{'n':sum(c['category']==category for c in held),
                'accepted':sum(c['category']==category and c['accepted'] for c in held)}
                for category in sorted({c['category'] for c in held})},'cases':cases}
    write_json(root/'consumer-diagnosis.json',result)
    print(json.dumps(result,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('--source', type=Path, action='append', required=True)
    inv.add_argument('--output', type=Path, required=True)
    inv.add_argument('--limit', type=int, default=120)
    inv.add_argument('--seed', type=int, default=20261018)
    inv.set_defaults(func=inventory)
    run_parser = sub.add_parser('run')
    run_parser.add_argument('--dataset', type=Path, required=True)
    run_parser.add_argument('--engine', choices=('easyocr','paddleocr'), required=True)
    run_parser.add_argument('--name', help='result filename stem for model variants')
    run_parser.add_argument('--models', type=Path, default=Path('automation/models/easyocr'))
    run_parser.add_argument('--detection-model', default='PP-OCRv5_mobile_det')
    run_parser.add_argument('--recognition-model', default='en_PP-OCRv5_mobile_rec')
    run_parser.add_argument('--detection-dir', type=Path)
    run_parser.add_argument('--recognition-dir', type=Path)
    run_parser.add_argument('--threads', type=int, default=4)
    run_parser.add_argument('--mkldnn', action=argparse.BooleanOptionalAction, default=True,
                            help='Paddle CPU acceleration; recorded in run metadata')
    run_parser.add_argument('--clients', type=int, default=4)
    run_parser.add_argument('--repeats', type=int, default=2)
    run_parser.add_argument('--crops-only', action='store_true', help='supplemental labeled crops without full-screen reads')
    run_parser.set_defaults(func=run)
    summary = sub.add_parser('summarize')
    summary.add_argument('--dataset', type=Path, required=True)
    summary.add_argument('--threshold', type=float, default=0.0)
    summary.add_argument('--runs', nargs='+', default=['easyocr', 'paddleocr'])
    summary.set_defaults(func=summarize)
    recovery = sub.add_parser('fallback')
    recovery.add_argument('--dataset', type=Path, required=True)
    recovery.add_argument('--primary-run', default='easyocr')
    recovery.add_argument('--threshold', type=float, default=.45)
    recovery.set_defaults(func=fallback)
    stamps = sub.add_parser('timestamps')
    stamps.add_argument('--dataset', type=Path, required=True)
    stamps.add_argument('--runs', nargs='+', default=['easyocr','paddleocr-v5','paddleocr-v6'])
    stamps.add_argument('--threshold', type=float, default=.55)
    stamps.set_defaults(func=timestamps)
    fingerprints = sub.add_parser('inputs')
    fingerprints.add_argument('--dataset',type=Path,required=True)
    fingerprints.add_argument('--name',required=True)
    fingerprints.add_argument('--compare',type=Path)
    fingerprints.set_defaults(func=inputs)
    consumer = sub.add_parser('consumers')
    consumer.add_argument('--dataset',type=Path,required=True)
    consumer.add_argument('--runs',nargs='+',default=['easyocr','paddleocr-v5','paddleocr-v6'])
    consumer.set_defaults(func=consumers)
    args = parser.parse_args()
    if args.command == 'run' and args.engine == 'paddleocr' and not (args.detection_dir and args.recognition_dir):
        parser.error('PaddleOCR requires explicit local model directories')
    if getattr(args, 'repeats', 1) < 1 or getattr(args, 'limit', 1) < 1:
        parser.error('repeats and limit must be positive')
    if getattr(args, 'threads', 1) < 1 or getattr(args, 'clients', 1) < 1:
        parser.error('threads and clients must be positive')
    if not 0 <= getattr(args, 'threshold', 0) <= 1:
        parser.error('threshold must be between zero and one')
    args.func(args)


if __name__ == '__main__':
    main()
