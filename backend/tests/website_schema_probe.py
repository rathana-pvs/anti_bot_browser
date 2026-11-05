"""HTTP integration probe; only the disposable website test server is allowed."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.services import website_service as web

if len(sys.argv) != 3 or sys.argv[1] != 'http://127.0.0.1:55440':
    raise SystemExit('Use only with the disposable website Studio integration server.')
with tempfile.TemporaryDirectory(prefix='website-connector-probe-') as directory:
    root = Path(directory)
    web.ROOT = root / 'connections'
    web.SHARED_MEDIA_DIR = root / 'media'
    web.SHARED_MEDIA_DIR.mkdir()
    (web.SHARED_MEDIA_DIR / 'cover.jpg').write_bytes(Path(sys.argv[2]).read_bytes())
    value = json.loads((Path(__file__).resolve().parents[2] / 'docs/examples/asian-dot-connection.json').read_text())
    value.update(baseUrl=sys.argv[1], email='studio-test@example.com', password='test-password-only')
    saved = web.save_connection(web.Connector.model_validate(value))
    request = web.Prepare(request_id='d' * 32, connection_id=saved['id'], article={
        'title': 'Schema connector integration story', 'excerpt': 'A disposable integration story.',
        'content': 'This article checks the schema connector.\n\nIt exists only in the disposable database.',
        'cover_file': 'cover.jpg', 'seo_title': 'Custom schema SEO'}, profiles=['test-page-a', 'test-page-b'],
        post_type='reel', media_file='test.mp4', caption='Read the full story', unique_captions=False)
    record = web.prepare(request)
    assert record['ready'] and record['article']['status'] == 'published'
    assert len({item['link'] for item in record['destinations'].values()}) == 2
    assert web.prepare(request) == record
    assert any(article['title'] == request.article.title for article in web.catalog(saved['id'])['articles'])
    print('PASS: Python schema import, real HTTP login/upload/publication/verification, distinct links, replay and catalog')
