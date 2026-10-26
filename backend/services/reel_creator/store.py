"""Small transactional journal; no scheduler or publishing state is stored here."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def db(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.root / 'creator.sqlite3', timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS reels (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA user_version').fetchone()[0] < 4:
                # The template-based studio is gone: its drafts and render records no longer open.
                db.execute('DROP TABLE IF EXISTS drafts')
                db.execute("DELETE FROM jobs WHERE payload NOT LIKE '%\"reel\":%'")
                db.execute('PRAGMA user_version=4')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _draft(row):
        payload = json.loads(row['payload'])
        return dict(id=row['id'], revision=row['revision'], updated_at=row['updated'], **payload)

    def drafts(self):
        with self.db() as db:
            return [self._draft(r) for r in db.execute('SELECT * FROM reels ORDER BY updated DESC LIMIT 50')]

    def draft(self, draft_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM reels WHERE id=?', (draft_id,)).fetchone()
        if not row:
            raise FileNotFoundError('Draft not found')
        return self._draft(row)

    def save_draft(self, draft_id, revision, payload):
        """payload: reel, caption, source_text, source_url."""
        with self.db() as db:
            old = db.execute('SELECT revision FROM reels WHERE id=?', (draft_id,)).fetchone()
            if revision != (old['revision'] if old else 0):
                raise Conflict('This reel changed in another window. Reload it before saving.')
            value = dict(id=draft_id, revision=revision + 1, updated_at=time.time(), **payload)
            db.execute('INSERT OR REPLACE INTO reels(id,revision,payload,updated) VALUES(?,?,?,?)',
                       (draft_id, value['revision'], json.dumps(payload), value['updated_at']))
        return value

    def delete_draft(self, draft_id):
        with self.db() as db:
            db.execute('DELETE FROM reels WHERE id=?', (draft_id,))

    def put_asset(self, value):
        with self.db() as db:
            db.execute('INSERT INTO assets VALUES(?,?)', (value['id'], json.dumps(value)))
        return value

    def asset(self, asset_id, kind=None):
        with self.db() as db:
            row = db.execute('SELECT payload FROM assets WHERE id=?', (asset_id,)).fetchone()
        if not row:
            raise ValueError('An input asset is missing. Import it again.')
        value = json.loads(row['payload'])
        if kind and value['kind'] != kind:
            raise ValueError('Wrong asset type')
        return value

    def create_job(self, request_id, fingerprint, reel, kind, post_caption=''):
        with self.db() as db:
            old = db.execute('SELECT * FROM jobs WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('The render request ID was already used for different settings.')
                return json.loads(old['payload'])
            value = dict(id=uuid.uuid4().hex, reel=reel, kind=kind,
                         state='queued', progress=0, reason=None, error=None,
                         created_at=time.time(), updated_at=time.time(), attempt=0,
                         cancel_requested=False, output=None, process=None, post_caption=post_caption)
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)',
                       (value['id'], request_id, fingerprint, json.dumps(value)))
        return value

    def jobs(self):
        with self.db() as db:
            rows = db.execute('SELECT payload FROM jobs ORDER BY rowid DESC').fetchall()
        return [json.loads(r['payload']) for r in rows]

    def job(self, job_id):
        with self.db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise FileNotFoundError('Render job not found')
        return json.loads(row['payload'])

    def update(self, job_id, **changes):
        with self.db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError('Render job not found')
            value = json.loads(row['payload'])
            value.update(changes, updated_at=time.time())
            db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(value), job_id))
        return value

    def cancel(self, job_id):
        with self.db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError('Render job not found')
            value = json.loads(row['payload'])
            if value['state'] == 'queued':
                value.update(state='cancelled', cancel_requested=True)
            elif value['state'] in ('rendering', 'verifying'):
                value['cancel_requested'] = True
            db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(value), job_id))
        return value

    def claim(self):
        with self.db() as db:
            rows = db.execute('SELECT id,payload FROM jobs ORDER BY rowid').fetchall()
            # Preserve creation order within each kind; exports precede previews.
            rows.sort(key=lambda row: json.loads(row['payload'])['kind'] != 'final')
            for row in rows:
                job = json.loads(row['payload'])
                if job['state'] == 'queued':
                    job.update(state='rendering', attempt=job['attempt'] + 1,
                               reason=None, updated_at=time.time())
                    db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(job), row['id']))
                    return job
        return None

    def house_styles(self):
        with self.db() as db:
            row = db.execute('SELECT payload FROM settings WHERE id=?', ('house_styles',)).fetchone()
        return json.loads(row['payload']) if row else []

    def save_house_style(self, value, limit):
        """Add or replace one house style by id; names are unique so the picker can tell them apart."""
        with self.db() as db:
            row = db.execute('SELECT payload FROM settings WHERE id=?', ('house_styles',)).fetchone()
            styles = [s for s in (json.loads(row['payload']) if row else []) if s['id'] != value['id']]
            if any(s['name'].casefold() == value['name'].casefold() for s in styles):
                raise ValueError(f'A house style is already called {value["name"]}. Choose another name.')
            if len(styles) >= limit:
                raise ValueError(f'Keep up to {limit} house styles. Delete one first.')
            styles.append(value)
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('house_styles', json.dumps(styles)))
        return styles

    def delete_house_style(self, style_id):
        with self.db() as db:
            row = db.execute('SELECT payload FROM settings WHERE id=?', ('house_styles',)).fetchone()
            styles = [s for s in (json.loads(row['payload']) if row else []) if s['id'] != style_id]
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('house_styles', json.dumps(styles)))
        return styles

    def brand(self, value=None):
        with self.db() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('brand', json.dumps(value)))
            row = db.execute('SELECT payload FROM settings WHERE id=?', ('brand',)).fetchone()
        return json.loads(row['payload']) if row else None
