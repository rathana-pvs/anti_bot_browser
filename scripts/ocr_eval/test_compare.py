import importlib.util
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

SPEC = importlib.util.spec_from_file_location('compare', Path(__file__).with_name('compare.py'))
compare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare)


class ComparisonTests(unittest.TestCase):
    def test_coordinate_mapping_restores_scale_and_crop_offset(self):
        result = compare.normalize_items([[[1, 2], [4, 2], [4, 6], [1, 6]]],
                                         ['Next'], [.9], .5, (100, 200))
        self.assertEqual(result[0]['box'], [[102., 204.], [108., 204.], [108., 212.], [102., 212.]])

    def test_engine_output_must_have_matching_lengths(self):
        with self.assertRaises(ValueError):
            compare.normalize_items([], ['Next'], [.9])

    def test_wrong_geometry_cannot_satisfy_target_label(self):
        label = dict(expected=['Post'], target_box=[0, 0, 30, 30], region=[0, 0, 200, 200])
        items = compare.normalize_items([[[100, 100], [120, 100], [120, 120], [100, 120]]], ['Post'], [.99])
        self.assertFalse(compare.score_label(label, items, 0)['exact'])

    def test_negative_crop_rejects_only_expected_forbidden_label(self):
        label = dict(expected=['Upload'], region=[0, 0, 30, 30], absent=True)
        boxes = [[[1, 1], [20, 1], [20, 20], [1, 20]]]
        for text, expected in [('Preview', True), ('Upload', False)]:
            items = compare.normalize_items(boxes, [text], [.9])
            self.assertEqual(compare.score_label(label, items, 0)['exact'], expected)

    def test_missing_recognition_counts_as_deletion(self):
        score = compare.score_label(dict(expected=['Next'], region=[0, 0, 30, 30]), [], 0)
        self.assertEqual(score['char_errors'], 4)
        self.assertEqual(score['word_errors'], 1)
        self.assertFalse(score['exact'])

    def test_threshold_and_normalization(self):
        label = dict(expected=['Next'], region=[0, 0, 30, 30])
        items = compare.normalize_items([[[1, 1], [20, 1], [20, 20], [1, 20]]], [' NEXT '], [.4])
        self.assertTrue(compare.score_label(label, items, .3)['exact'])
        self.assertFalse(compare.score_label(label, items, .5)['exact'])

    def test_comment_anchor_ignores_dynamic_account_name(self):
        label = dict(expected=['Comment as'], category='input', region=[0,0,30,30])
        boxes = [[[1,1],[20,1],[20,20],[1,20]]]
        self.assertTrue(compare.score_label(label, compare.normalize_items(boxes,['Comment as Name'],[.9]),0)['exact'])
        self.assertFalse(compare.score_label(label, compare.normalize_items(boxes,['Comment by Name'],[.9]),0)['exact'])

    def test_percentile_and_edit_distance(self):
        self.assertEqual(compare.percentile([1, 2, 3, 4], .95), 4)
        self.assertIsNone(compare.percentile([], .95))
        self.assertEqual(compare.edit_distance('1m', '1h'), 1)
        self.assertEqual(compare.edit_distance(['a','b'], ['a']), 1)

    def test_snapshot_validation_detects_modified_inputs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root/'image.png'
            image.write_bytes(b'original')
            compare.write_json(root/'manifest.json', dict(records=[dict(id='one',image='image.png',sha256=compare.digest(image))]))
            compare.write_json(root/'annotations.json', dict(manifest_sha256=compare.digest(root/'manifest.json'), labels=[]))
            compare.load_dataset(root)
            image.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'snapshot modified'):
                compare.load_dataset(root)

    def test_inventory_excludes_profile_four_and_preserves_source(self):
        import cv2
        import numpy as np
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for profile in ('profile_002','profile_003','profile_004'):
                run = root/'source'/'profiles'/profile/'automation_evidence'/'run'
                run.mkdir(parents=True)
                cv2.imwrite(str(run/'01_next.png'), np.zeros((32,64,3), dtype=np.uint8))
            source = root/'source'
            before = {str(p): compare.digest(p) for p in source.rglob('*.png')}
            compare.inventory(SimpleNamespace(source=[source],output=root/'snapshot',seed=42,limit=5))
            manifest = json.loads((root/'snapshot'/'manifest.json').read_text())
            self.assertEqual(manifest['original_count'], 2)
            self.assertEqual(manifest['unique_count'], 1)
            self.assertFalse(any('profile_004' in p for r in manifest['records'] for p in r['sources']))
            self.assertEqual(before, {str(p): compare.digest(p) for p in source.rglob('*.png')})
            with self.assertRaises(ValueError):
                compare.inventory(SimpleNamespace(source=[source],output=source/'bad',seed=42,limit=5))

    def test_engine_error_cannot_pass_negative_case(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'image.png').write_bytes(b'fixture')
            compare.write_json(root/'manifest.json', dict(original_count=1,unique_count=1,selected=['one'],
                records=[dict(id='one',image='image.png',sha256=compare.digest(root/'image.png'),
                              width=100,height=100,split='held_out')]))
            compare.write_json(root/'annotations.json',dict(manifest_sha256=compare.digest(root/'manifest.json'),
                labels=[dict(label_id='label',id='one',category='negative',expected=['Upload'],
                             absent=True,region=[0,0,30,30])]))
            compare.write_json(root/'broken.json',dict(manifest_sha256=compare.digest(root/'manifest.json'),
                annotations_sha256=compare.digest(root/'annotations.json'),initialization_ms=1,first_warmup_ms=1,
                peak_rss_mib=1,versions={},threads=4,device='cpu',queue=dict(clients=4,samples=[],throughput_per_second=0),
                samples=[dict(id='one',label_id='label',kind='crop',repeat=0,error='engine failed'),
                         dict(id='one',kind='screen',repeat=0,error='engine failed')]))
            with contextlib.redirect_stdout(io.StringIO()):
                compare.summarize(SimpleNamespace(dataset=root,runs=['broken'],threshold=0))
            summary=json.loads((root/'summary.json').read_text())
            self.assertEqual(summary['engines']['broken']['accuracy']['held_out']['exact'],0)
            self.assertEqual(summary['engines']['broken']['accuracy']['held_out']['screen_exact'],0)
            self.assertEqual(summary['engines']['broken']['errors'],2)

    def test_consumer_diagnosis_preserves_aliases_and_rejects_bad_timestamp_suffix(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'image.png').write_bytes(b'fixture')
            compare.write_json(root/'manifest.json',dict(selected=['one'],records=[dict(id='one',image='image.png',
                sha256=compare.digest(root/'image.png'),width=100,height=100,split='held_out')]))
            labels=[]
            for i,(text,category) in enumerate([('Photo/video','button'),('Describe your reel...','input'),('a few seconds ago','timestamp')]):
                labels.append(dict(label_id=str(i),id='one',expected=[text],category=category,region=[0,0,80,80],target_box=[0,0,80,80]))
            compare.write_json(root/'annotations.json',dict(manifest_sha256=compare.digest(root/'manifest.json'),labels=labels))
            samples=[]
            for i,text in enumerate(['photolvideo','pescribe your reel:','a few seconds ago g']):
                items=compare.normalize_items([[[1,1],[20,1],[20,20],[1,20]]],[text],[.99])
                samples.append(dict(label_id=str(i),kind='crop',repeat=0,items=items))
            compare.write_json(root/'fixture.json',dict(manifest_sha256=compare.digest(root/'manifest.json'),
                annotations_sha256=compare.digest(root/'annotations.json'),samples=samples))
            with contextlib.redirect_stdout(io.StringIO()):
                compare.consumers(SimpleNamespace(dataset=root,runs=['fixture']))
            result=json.loads((root/'consumer-diagnosis.json').read_text())['engines']['fixture']
            self.assertEqual(result['accepted'],2)
            self.assertEqual(result['by_category']['timestamp']['accepted'],0)


if __name__ == '__main__':
    unittest.main()
