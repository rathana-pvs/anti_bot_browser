"""Durable local rendering. No account sessions or publishing ownership."""
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

import psutil
from PIL import Image, ImageOps, UnidentifiedImageError, features

from .composition import FONTS, make_layers, save_layers, poster, encode_command, output_size, fit_news_text
from .models import Composition
from .store import Store, Conflict
from .audio_presets import preset_path
from .story import photos_for, story_poster, save_story, story_command, photo_spec

logger = logging.getLogger(__name__)
MAX_UPLOAD = 25 * 1024 * 1024
MAX_PIXELS = 20_000_000
PHOTO_FORMATS = ('JPEG', 'PNG', 'WEBP', 'AVIF', 'GIF', 'BMP', 'TIFF')
PHOTO_FORMAT_LABELS = 'JPEG, PNG, WebP, AVIF, GIF, BMP, or TIFF'
ACTIVE = {'rendering', 'verifying'}


class Interrupted(RuntimeError):
    pass


def tool(name):
    found = shutil.which(name)
    local = Path(sys.executable).parent / name
    return found or (str(local) if local.is_file() else None)


def stop_owned_process(record, marker):
    """Reject PID reuse and require the private attempt path in the command."""
    if not record:
        return False
    try:
        process = psutil.Process(record['pid'])
        if abs(process.create_time() - record['created']) > 0.01:
            return False
        if process.cwd() != marker and not any(arg.startswith(marker + os.sep) for arg in process.cmdline()):
            return False
        children = process.children(recursive=True)
        if os.name == 'posix' and os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            for child in children:
                child.terminate()
            process.terminate()
        _, alive = psutil.wait_procs([process, *children], timeout=2)
        for child in alive:
            child.kill()
        psutil.wait_procs(alive, timeout=2)
        return True
    except (psutil.NoSuchProcess, ProcessLookupError):
        return False


class ReelCreator:
    def __init__(self, root, media_dir, admission=None):
        self.root, self.media_dir = Path(root), Path(media_dir)
        self.store = Store(self.root)
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.render_lock = threading.Lock()
        self.thread = None
        self.admission = admission
        self.memory_paused = False
        self._capabilities = None
        self._capability_time = 0
        self._posters = {}
        self._last_cleanup = 0

    def capabilities(self, refresh=False):
        if not refresh and self._capabilities and time.monotonic() - self._capability_time < 30:
            return self._capabilities
        missing = []
        ffmpeg, ffprobe = tool('ffmpeg'), tool('ffprobe')
        version = None
        for name, executable in (('FFmpeg', ffmpeg), ('FFprobe', ffprobe)):
            if not executable:
                missing.append(name)
                continue
            try:
                result = subprocess.run([executable, '-version'], capture_output=True, text=True, timeout=5, check=True)
                if name == 'FFmpeg':
                    version = result.stdout.splitlines()[0].split(' Copyright')[0]
                    encoders = subprocess.run([executable, '-hide_banner', '-encoders'], capture_output=True,
                                              text=True, timeout=5, check=True).stdout
                    if 'libx264' not in encoders or ' aac ' not in encoders:
                        missing.append('H.264/AAC encoders')
                    filters = subprocess.run([executable, '-hide_banner', '-filters'], capture_output=True,
                                             text=True, timeout=5, check=True).stdout
                    names = {line.split()[1] for line in filters.splitlines() if len(line.split()) > 1}
                    if not {'loop', 'xfade', 'zoompan', 'overlay', 'fade', 'concat', 'split'} <= names:
                        missing.append('FFmpeg story motion filters')
            except (OSError, subprocess.SubprocessError):
                missing.append(name + ' executable/runtime libraries')
        if not features.check_feature('raqm'):
            missing.append('Pillow RAQM text layout')
        if not all((FONTS / name).is_file() for name in (
                'NotoSans-Bold.ttf', 'NotoSans-Regular.ttf', 'NotoSansKhmer-Bold.ttf', 'NotoSansKhmer-Regular.ttf')):
            missing.append('bundled fonts')
        from .daily_brief import runtime_status
        daily_ready, daily_reason = runtime_status()
        from .lens_report import runtime_status as lens_runtime_status
        lens_ready, lens_reason = lens_runtime_status()
        try:
            from .daily_brief import require_flexible_runtime
            require_flexible_runtime();flexible_ready=True
        except ValueError:flexible_ready=False
        self._capabilities = dict(flexible_scenes=flexible_ready,lens_report_revision=2, lens_report=lens_ready, lens_report_reason=lens_reason, daily_brief=daily_ready, daily_brief_reason=daily_reason, ready=not missing, missing=missing, version=version,
                                 repair='Open Settings → Setup and repair → Install / update runtime.',
                                 max_upload_bytes=MAX_UPLOAD, max_pixels=MAX_PIXELS, story_motion=True, max_photos=5,
                                 slideshow_controls=True, photo_framing=True, aspect_ratios=['9:16', '4:5', '1:1'])
        self._capability_time = time.monotonic()
        return self._capabilities

    def asset_path(self, asset_id, kind):
        value = self.store.asset(asset_id, kind)
        path = self.root / 'assets' / value['filename']
        if path.parent.resolve() != (self.root / 'assets').resolve() or not path.is_file():
            raise ValueError('An input file is missing. Import it again.')
        return path

    def import_asset(self, data, name, kind, *, narration=None):
        if kind not in ('image', 'logo', 'audio') or not data or len(data) > MAX_UPLOAD:
            raise ValueError('Choose an image, logo, or audio file no larger than 25 MB.')
        asset_id = uuid.uuid4().hex
        folder = self.root / 'assets'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = dict(id=asset_id, kind=kind, name=Path(name.replace('\\', '/')).name[:160],
                     created_at=time.time())
        path = folder / (asset_id + ('.audio' if kind == 'audio' else '.png'))
        try:
            if kind == 'audio':
                ffprobe = tool('ffprobe')
                if not ffprobe:
                    raise ValueError('Audio inspection requires FFprobe. Run Setup and repair.')
                if not (data.startswith((b'ID3', b'fLaC', b'OggS'))
                        or (data[:4] == b'RIFF' and data[8:12] == b'WAVE')
                        or data[4:8] == b'ftyp'
                        or (len(data) > 1 and data[0] == 255 and data[1] & 0xe0 == 0xe0)):
                    raise ValueError('Choose MP3, WAV, M4A, Ogg, or FLAC audio. Playlists are not supported.')
                path.write_bytes(data)
                result = subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                         '-show_streams', '-show_format', '-of', 'json', str(path)],
                                        capture_output=True, text=True, timeout=10, check=True)
                info = json.loads(result.stdout)
                if not any(s['codec_type'] == 'audio' for s in info.get('streams', [])):
                    raise ValueError('This file has no audio stream.')
                duration = float(info.get('format', {}).get('duration', 0))
                if not 0 < duration <= 600:
                    raise ValueError('Choose an audio clip between 0 and 600 seconds.')
                value['duration'] = duration
            else:
                with Image.open(io.BytesIO(data)) as raw:
                    if raw.format not in PHOTO_FORMATS:
                        raise ValueError(f'Choose a {PHOTO_FORMAT_LABELS} image.')
                    if raw.width * raw.height > MAX_PIXELS:
                        raise ValueError('This image exceeds 20 million pixels. Resize it before importing.')
                    if kind == 'logo' and raw.format != 'PNG':
                        raise ValueError('Choose a PNG logo; transparency is supported.')
                    if getattr(raw, 'n_frames', 1) > 1:
                        raise ValueError('Choose a still image. Animated or multi-page images are not supported.')
                    image = ImageOps.exif_transpose(raw).convert('RGBA' if kind == 'logo' else 'RGB')
                    value.update(width=image.width, height=image.height)
                    image.save(path, 'PNG')
                    if kind == 'image':
                        from .auto_framing import suggest_framing
                        value['suggested_framing'] = suggest_framing(image)
                        ImageOps.contain(image, (256, 160)).convert('RGB').save(folder / f'{asset_id}-thumb.jpg', 'JPEG', quality=80)
            if narration:
                value['narration'] = narration
            value.update(filename=path.name, size_bytes=path.stat().st_size)
            return self.store.put_asset(value)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            path.unlink(missing_ok=True)
            raise ValueError(f'The file could not be decoded. Choose a valid {PHOTO_FORMAT_LABELS} image, or a supported audio clip.') from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def validate_assets(self, spec):
        if not spec.image_id:
            raise ValueError('Choose an image first.')
        if spec.template in ('daily_brief', 'lens_report'):
            if spec.template == 'lens_report':
                from .lens_report import validate, layers
            else:
                from .daily_brief import validate, layers
            validate(spec)
            for photo in photos_for(spec):
                for layer in layers(spec, photo, self.asset_path(photo.image_id, 'image')):
                    layer.close()
        elif not spec.photos:
            fit_news_text(spec)
        image = self.asset_path(spec.image_id, 'image')
        for photo in spec.photos:
            if spec.template not in ('daily_brief', 'lens_report'):
                fit_news_text(photo_spec(spec, photo))
            self.asset_path(photo.image_id, 'image')
        logo = self.asset_path(spec.logo_id, 'logo') if spec.logo_id else None
        audio = self.asset_path(spec.audio_id, 'audio') if spec.audio_id else preset_path(spec.audio_preset) if spec.audio_preset != 'none' else None
        return image, logo, audio

    def validate_narration(self, spec):
        if not spec.narration_enabled:
            return None
        from .narration import voice_fingerprint
        if not spec.narration_id or not spec.narration_text.strip():
            raise ValueError('Preview voice to prepare your narration before rendering, or turn narration off.')
        asset = self.store.asset(spec.narration_id, 'audio')
        expected = voice_fingerprint(spec.narration_text, spec.narration_voice, spec.narration_rate)
        if asset.get('narration', {}).get('fingerprint') != expected:
            raise ValueError('The narration script or voice changed. Preview voice again before rendering.')
        if asset.get('duration', 0) > spec.duration - .25:
            raise ValueError(f'The voice lasts {asset["duration"]:.1f} seconds. Shorten the script or choose a longer video.')
        return self.asset_path(spec.narration_id, 'audio')

    def thumbnail_path(self, asset_id):
        source = self.asset_path(asset_id, 'image')
        path = source.parent / f'{asset_id}-thumb.jpg'
        if path.is_file():
            return path
        if not self.render_lock.acquire(blocking=False):
            raise Conflict('Thumbnail will be available after rendering.')
        try:
            with Image.open(source) as image:
                ImageOps.contain(image, (256, 160)).convert('RGB').save(path, 'JPEG', quality=80)
            return path
        finally:
            self.render_lock.release()

    def render_poster(self, spec, photo_index=0):
        if not self.render_lock.acquire(blocking=False):
            raise Conflict('A video is rendering. Your preview will update when it finishes.')
        try:
            image, logo, _ = self.validate_assets(spec)
            key = hashlib.sha256((spec.model_dump_json() + ':' + str(photo_index)).encode()).hexdigest()
            if key in self._posters:
                return self._posters[key]
            if spec.template in ('daily_brief', 'lens_report'):
                if spec.template == 'lens_report':
                    from .lens_report import poster as daily_poster
                else:
                    from .daily_brief import poster as daily_poster
                picture, warnings = daily_poster(spec, [self.asset_path(p.image_id, 'image') for p in photos_for(spec)], logo, photo_index)
            elif spec.version == 2:
                picture, warnings = story_poster(spec, [self.asset_path(p.image_id, 'image') for p in photos_for(spec)], logo, photo_index)
            else:
                if photo_index:
                    raise ValueError('This draft has one photo.')
                layers = make_layers(spec, image, logo)
                picture, warnings = poster(layers, spec), layers[3]
            output = io.BytesIO()
            picture.save(output, format='JPEG', quality=88)
            result = (output.getvalue(), warnings)
            if len(self._posters) >= 8:
                self._posters.pop(next(iter(self._posters)))
            self._posters[key] = result
            return result
        finally:
            self.render_lock.release()

    def enqueue(self, value):
        if self.thread and not self.thread.is_alive():
            raise Conflict('The local renderer stopped. Restart the app or backend before rendering.')
        if not self.capabilities()['ready']:
            raise ValueError('Rendering dependencies are missing. Run Settings → Setup and repair.')
        if value.composition.template in ('daily_brief', 'lens_report'):
            if value.composition.template == 'lens_report':
                from .lens_report import runtime_status
                ready, reason = runtime_status(value.composition.layout_revision)
            else:
                from .daily_brief import runtime_status
                ready, reason = runtime_status()
            if not ready:
                raise ValueError(reason)
        from .flexible_scene import enabled
        if enabled(value.composition):
            from .daily_brief import require_flexible_runtime
            require_flexible_runtime()
        self.validate_assets(value.composition)
        self.validate_narration(value.composition)
        composition = value.composition.model_dump()
        fingerprint = hashlib.sha256(json.dumps([composition, value.kind, value.post_caption], sort_keys=True).encode()).hexdigest()
        job = self.store.create_job(value.request_id, fingerprint, composition, value.kind, value.post_caption)
        self.wake.set()
        return job

    def retry(self, job_id):
        job = self.store.job(job_id)
        if job['state'] == 'queued':
            return job
        if job['state'] not in ('failed', 'interrupted', 'cancelled'):
            raise Conflict('Only a stopped render can be retried.')
        self.validate_assets(Composition(**job['composition']))
        self.validate_narration(Composition(**job['composition']))
        # The backend is the sole owner; transactionally ensure concurrent retries
        # cannot turn an already running attempt back into queued work.
        with self.store.db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            current = json.loads(row['payload'])
            if current['state'] not in ('failed', 'interrupted', 'cancelled'):
                return current
            current.update(state='queued', error=None, reason=None, cancel_requested=False,
                           progress=0, process=None, updated_at=time.time())
            db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(current), job_id))
        self.wake.set()
        return current

    def pressure(self):
        if self.admission:
            return self.admission()
        from backend.services import automation_service, container_lifecycle, docker_service, live_service
        from backend.services.host_metrics_service import get_windows_host_stats
        from backend.services.resource_service import resource_admission_decision
        if container_lifecycle.UPDATE_MAINTENANCE or container_lifecycle.SHUTTING_DOWN:
            return 'App maintenance or shutdown'
        if live_service.CONTROLS or any(task.get('status') in ('running', 'preparing')
                                       for task in list(automation_service.active_automation_tasks.values())):
            return 'Waiting for automation or Live to finish'
        memory = psutil.virtual_memory().percent
        windows = get_windows_host_stats()
        if windows.get('available') and windows.get('total_memory_mb'):
            memory = max(memory, 100 * windows['used_memory_mb'] / windows['total_memory_mb'])
        cpu = max(docker_service.sustained_cpu_percent(), windows.get('cpu_percent', 0) or 0)
        decision = resource_admission_decision(memory, cpu, self.memory_paused)
        self.memory_paused = decision['memoryPaused']
        if not decision['allowed']:
            return 'Waiting for available memory' if self.memory_paused else 'Waiting for available CPU'
        return None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._worker, name='reel-creator', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=12)

    def output_path(self, job):
        if job['state'] != 'completed' or not job.get('output'):
            raise Conflict('The video is not ready yet.')
        directory = self.media_dir if job['kind'] == 'final' else self.root / 'previews'
        path = directory / job['output']['filename']
        if path.parent.resolve() != directory.resolve() or not path.is_file():
            raise FileNotFoundError('The completed video was moved or deleted.')
        return path

    def _recover(self):
        for job in self.store.jobs():
            if job['state'] not in ACTIVE:
                continue
            marker = job.get('workdir')
            if marker:
                stop_owned_process(job.get('process'), marker)
                # Also cover a crash immediately after spawn, before PID persistence.
                for process in psutil.process_iter(['pid', 'create_time', 'cmdline', 'cwd']):
                    try:
                        if process.info['cwd'] == marker or any(arg.startswith(marker + os.sep) for arg in (process.info['cmdline'] or [])):
                            stop_owned_process(dict(pid=process.pid, created=process.info['create_time']), marker)
                    except (psutil.Error, OSError):
                        continue
            final = self.media_dir / f'reel_{job["id"]}.mp4' if job['kind'] == 'final' else self.root / 'previews' / f'reel_{job["id"]}.mp4'
            if final.is_file() and not job.get('cancel_requested'):
                try:
                    self._verify(job, final, recovery=True)
                    self.store.update(job['id'], state='completed', progress=1, process=None,
                                      output=dict(filename=final.name, size_bytes=final.stat().st_size))
                    if marker:
                        shutil.rmtree(marker, ignore_errors=True)
                    continue
                except Exception:
                    # Missing repair dependencies are not permission to delete a
                    # promoted export. A retry can replace it after verification.
                    logger.warning('Could not reconcile promoted reel %s', job['id'])
            self.store.update(job['id'], state='cancelled' if job.get('cancel_requested') else 'interrupted',
                              process=None, error='The backend stopped before this render completed. Retry when ready.')
            if marker:
                shutil.rmtree(marker, ignore_errors=True)

    def _worker(self):
        try:
            self._recover()
            while not self.stop_event.is_set():
                if time.monotonic() - self._last_cleanup > 3600:
                    self._expire_previews()
                    self._last_cleanup = time.monotonic()
                queued = [j for j in self.store.jobs() if j['state'] == 'queued']
                reason = self.pressure() if queued else None
                if reason:
                    for job in queued:
                        if job.get('reason') != reason:
                            self.store.update(job['id'], reason=reason)
                elif queued:
                    with self.render_lock:
                        job = self.store.claim()
                        if job:
                            self._render(job)
                    continue
                self.wake.wait(2)
                self.wake.clear()
        except Exception:
            logger.exception('Reel Creator worker stopped unexpectedly')

    def _expire_previews(self):
        for job in self.store.jobs():
            if job['kind'] == 'preview' and job['state'] == 'completed' and time.time() - job['updated_at'] > 7 * 86400:
                try:
                    self.output_path(job).unlink()
                except FileNotFoundError:
                    pass
                self.store.update(job['id'], state='failed', output=None,
                                  error='This motion preview expired after seven days. Render it again.')

    def _run(self, job, command, stage, recovery=False):
        directory = Path(job['workdir'])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        progress_file, error_file = directory / f'{stage}.log', directory / f'{stage}.error'
        process = None
        started, pressured = time.monotonic(), None
        try:
            with progress_file.open('wb') as stdout, error_file.open('wb') as stderr:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                           start_new_session=os.name == 'posix', cwd=directory)
                owned = psutil.Process(process.pid)
                identity = dict(pid=process.pid, created=owned.create_time())
                self.store.update(job['id'], process=identity)
                try:
                    owned.nice(10 if os.name == 'posix' else psutil.BELOW_NORMAL_PRIORITY_CLASS)
                except psutil.Error:
                    pass
                while process.poll() is None:
                    current = self.store.job(job['id'])
                    if current.get('cancel_requested'):
                        raise Interrupted('Render cancelled.')
                    if self.stop_event.is_set():
                        raise Interrupted('The backend stopped. Retry this render when ready.')
                    if time.monotonic() - started > (30 if recovery else 300):
                        raise RuntimeError('Rendering timed out. Try a smaller image or shorter duration.')
                    reason = None if recovery else self.pressure()
                    if reason:
                        pressured = pressured or time.monotonic()
                        if 'automation' in reason or 'maintenance' in reason or time.monotonic() - pressured > 5:
                            raise Interrupted(reason + '. Retry when the machine is available.')
                    else:
                        pressured = None
                    try:
                        rss = sum(p.memory_info().rss for p in [owned, *owned.children(recursive=True)] if p.is_running())
                        if rss > current.get('peak_rss_bytes', 0) + 4 * 1024 * 1024:
                            self.store.update(job['id'], peak_rss_bytes=rss)
                        if rss > 768 * 1024 * 1024:
                            raise Interrupted('Render exceeded its memory budget. Try a smaller image.')
                    except psutil.NoSuchProcess:
                        pass
                    if error_file.stat().st_size > 1024 * 1024:
                        raise RuntimeError('The encoder produced excessive errors.')
                    if stage == 'encode':
                        lines = progress_file.read_text(errors='replace')[-8192:].splitlines()
                        times = [line.split('=', 1)[1] for line in lines if line.startswith('out_time_us=')]
                        if times and times[-1].isdigit():
                            fraction = min(0.98, int(times[-1]) / 1_000_000 / job['composition']['duration'])
                            if fraction > current.get('progress', 0) + 0.01:
                                self.store.update(job['id'], progress=fraction)
                    self.stop_event.wait(0.3)
                if process.returncode:
                    # Encoder logs stay private; API errors don't expose local paths.
                    raise RuntimeError('The encoder could not finish this video. Check the input files and runtime dependencies.')
            return progress_file.read_text(errors='replace')
        finally:
            if process and process.poll() is None:
                stop_owned_process(dict(pid=process.pid, created=psutil.Process(process.pid).create_time()), str(directory))
                process.wait(timeout=5)
            self.store.update(job['id'], process=None)

    def _verify(self, job, path, recovery=False):
        meta = json.loads(self._run(job, [tool('ffprobe'), '-v', 'error', '-show_streams', '-show_format',
                                         '-of', 'json', str(path)], 'probe', recovery))
        video = next((s for s in meta.get('streams', []) if s['codec_type'] == 'video'), None)
        spec = Composition(**job['composition'])
        if spec.narration_enabled and not any(s.get('codec_type') == 'audio' and s.get('codec_name') == 'aac' for s in meta.get('streams', [])):
            raise RuntimeError('Output verification failed: narration audio is missing.')
        expected = output_size(spec, job['kind'] == 'preview')
        duration = float(meta.get('format', {}).get('duration', 0))
        if not video or (video['width'], video['height']) != expected or video['codec_name'] != 'h264' or video.get('pix_fmt') != 'yuv420p':
            raise RuntimeError('Output verification failed: unexpected video format.')
        if abs(duration - job['composition']['duration']) > 0.2:
            raise RuntimeError('Output verification failed: incomplete duration.')
        from fractions import Fraction
        try:
            actual_fps = float(Fraction(video.get('avg_frame_rate', '0/1')))
        except (ValueError, ZeroDivisionError):
            actual_fps = 0
        if abs(actual_fps - spec.fps) > .01:
            raise RuntimeError('Output verification failed: unexpected frame rate.')
        self._run(job, [tool('ffmpeg'), '-v', 'error', '-xerror', '-nostdin', '-threads', '1',
                        '-i', str(path), '-f', 'null', '-'], 'decode', recovery)

    def _render(self, job):
        started = time.monotonic()
        workdir = self.root / 'staging' / f'{job["id"]}-{uuid.uuid4().hex}'
        self.store.update(job['id'], workdir=str(workdir))
        job['workdir'] = str(workdir)
        destination = None
        try:
            workdir.mkdir(parents=True, mode=0o700)
            self.media_dir.mkdir(parents=True, exist_ok=True)
            if min(shutil.disk_usage(workdir).free, shutil.disk_usage(self.media_dir).free) < 512 * 1024 * 1024:
                raise RuntimeError('At least 512 MB of free disk space is required to render.')
            spec = Composition(**job['composition'])
            image, logo, audio = self.validate_assets(spec)
            voice = self.validate_narration(spec)
            if voice:
                from .narration import mix_command
                mixed = workdir / 'narration-mix.wav'
                self._run(job, mix_command(tool('ffmpeg'), voice, audio, mixed, spec), 'audio-mix')
                audio = mixed
            output = workdir / 'output.mp4'
            if spec.template in ('daily_brief', 'lens_report'):
                if spec.template == 'lens_report':
                    from .lens_report import prepare
                else:
                    from .daily_brief import prepare
                command = prepare(spec, [self.asset_path(p.image_id, 'image') for p in photos_for(spec)], logo, workdir, job['kind'] == 'preview', tool('ffmpeg'))
                self._run(job, command, 'encode')
                from .daily_brief import finish_command
                command = finish_command(tool('ffmpeg'), workdir / 'remotion.mp4', output, spec, audio)
                warnings = []
            elif spec.version == 2:
                images, text, warnings = save_story(spec, [self.asset_path(p.image_id, 'image') for p in photos_for(spec)], logo, workdir)
                command = story_command(tool('ffmpeg'), images, text, output, spec, audio, job['kind'] == 'preview')
            else:
                layers = make_layers(spec, image, logo)
                paths = save_layers(layers, workdir)
                warnings = layers[3]
                command = encode_command(tool('ffmpeg'), paths, output, spec, audio, job['kind'] == 'preview')
            self.store.update(job['id'], warnings=warnings)
            self._run(job, command, 'encode')
            self.store.update(job['id'], state='verifying', progress=0.99)
            self._verify(job, output)
            directory = self.media_dir if job['kind'] == 'final' else self.root / 'previews'
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f'reel_{job["id"]}.mp4'
            temporary = directory / f'.reel_{job["id"]}.partial'
            try:
                with output.open('rb') as src, temporary.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                with self.store.db() as db:
                    row = db.execute('SELECT payload FROM jobs WHERE id=?', (job['id'],)).fetchone()
                    current = json.loads(row['payload'])
                    if current.get('cancel_requested') or self.stop_event.is_set():
                        raise Interrupted('Render stopped before export.')
                    os.replace(temporary, destination)
                    # Durability of the directory entry matters across host crashes.
                    if os.name == 'posix':
                        fd = os.open(directory, os.O_RDONLY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    current.update(state='completed', progress=1, error=None, process=None,
                                   updated_at=time.time(), output=dict(filename=destination.name,
                                   size_bytes=destination.stat().st_size))
                    db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(current), job['id']))
            finally:
                temporary.unlink(missing_ok=True)
        except Interrupted as exc:
            cancelled = self.store.job(job['id']).get('cancel_requested')
            self.store.update(job['id'], state='cancelled' if cancelled else 'interrupted', error=str(exc), process=None)
        except Exception as exc:
            logger.exception('Reel render failed: %s', job['id'])
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Rendering failed. Check free disk space and runtime dependencies.'
            self.store.update(job['id'], state='failed', error=message, process=None)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            self.store.update(job['id'], elapsed_seconds=round(time.monotonic() - started, 2))


_service = None


def get_service():
    global _service
    if _service is None:
        from backend.config import DATA_DIR, SHARED_MEDIA_DIR
        _service = ReelCreator(DATA_DIR / 'reel_creator', SHARED_MEDIA_DIR)
    return _service
