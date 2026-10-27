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

from .audio_presets import PRESETS, preset_path
from .reel import LOOKS, MAX_SCENES, MAX_SECONDS, RATIOS, Reel
from .renderer import Plan
from .store import Store, Conflict
from .typeset import FACES, FONTS

logger = logging.getLogger(__name__)
MAX_UPLOAD = 25 * 1024 * 1024
MAX_PIXELS = 20_000_000
PHOTO_FORMATS = ('JPEG', 'PNG', 'WEBP', 'AVIF', 'GIF', 'BMP', 'TIFF')
PHOTO_FORMAT_LABELS = 'JPEG, PNG, WebP, AVIF, GIF, BMP, or TIFF'
ACTIVE = {'rendering', 'verifying'}
FPS = 30
THUMBNAIL_SECONDS = 0  # use the complete opening card as the cover
PREVIEW_WIDTH, FINAL_WIDTH = 540, 1080
MUSIC_VOLUME = .2
MAX_REQUEUES = 10  # pressure that keeps returning mid-render stops the job instead of restarting it forever


class Interrupted(RuntimeError):
    pass


class Pressured(Interrupted):
    """Automation, Live, maintenance or resource pressure needs the machine; the job waits in the queue."""


class EncodingFailed(RuntimeError):
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
        self.poster_lock = threading.Lock()  # one still at a time; independent of a running render
        self.thread = None
        self.admission = admission
        self.memory_paused = False
        self._capabilities = None
        self._capability_time = 0
        self._posters = {}
        self._last_cleanup = 0
        self._nvenc = None

    def _use_nvenc(self):
        """An encoder listing is insufficient: exercise the installed driver and GPU."""
        if self._nvenc is None:
            self._nvenc = False
            executable = tool('ffmpeg')
            if executable:
                try:
                    result = subprocess.run([
                        executable, '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '256x256', '-r', '30', '-i', 'pipe:0',
                        '-frames:v', '1', '-c:v', 'h264_nvenc', '-preset', 'p4',
                        '-pix_fmt', 'yuv420p', '-f', 'null', '-'],
                        input=bytes(256 * 256 * 3), capture_output=True, timeout=10)
                    self._nvenc = result.returncode == 0
                except (OSError, subprocess.SubprocessError):
                    pass
            logger.info('Reel video encoding uses %s', 'NVIDIA NVENC' if self._nvenc else 'CPU')
        return self._nvenc

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
            except (OSError, subprocess.SubprocessError):
                missing.append(name + ' executable/runtime libraries')
        if not features.check_feature('raqm'):
            missing.append('Pillow RAQM text layout')
        if not all((FONTS / name).is_file() for name in FACES.values()):
            missing.append('bundled fonts')
        self._capabilities = dict(
            ready=not missing, missing=missing, version=version, simple_studio=True,
            repair='Open Settings → Setup and repair → Install / update runtime.',
            looks=[dict(id=key, name=value['name'], tag=value['tag'], accent=value['accent'], music=value['music']) for key, value in LOOKS.items()],
            ratios=list(RATIOS), max_scenes=MAX_SCENES, max_seconds=MAX_SECONDS,
            music=[dict(id=key, name=value['name'], description=value['description'], duration=value['duration']) for key, value in PRESETS.items()], max_upload_bytes=MAX_UPLOAD, max_pixels=MAX_PIXELS)
        self._capability_time = time.monotonic()
        return self._capabilities

    def asset_path(self, asset_id, kind):
        value = self.store.asset(asset_id, kind)
        path = self.root / 'assets' / value['filename']
        if path.parent.resolve() != (self.root / 'assets').resolve() or not path.is_file():
            raise ValueError('An input file is missing. Import it again.')
        return path

    def import_asset(self, data, name, kind):
        if kind not in ('image', 'logo', 'audio') or not data or len(data) > MAX_UPLOAD:
            raise ValueError('Choose an image, logo or audio file no larger than 25 MB.')
        asset_id = uuid.uuid4().hex
        folder = self.root / 'assets'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = dict(id=asset_id, kind=kind, name=Path(name.replace('\\', '/')).name[:160], created_at=time.time())
        path = folder / (asset_id + ('.audio' if kind == 'audio' else '.png'))
        try:
            if kind == 'audio':
                value['duration'] = self._inspect_audio(data, path)
                value.update(filename=path.name, size_bytes=path.stat().st_size)
                return self.store.put_asset(value)
            with Image.open(io.BytesIO(data)) as raw:
                if raw.format not in PHOTO_FORMATS:
                    raise ValueError(f'Choose a {PHOTO_FORMAT_LABELS} image.')
                if raw.width * raw.height > MAX_PIXELS:
                    raise ValueError('This image exceeds 20 million pixels. Resize it before importing.')
                if kind == 'logo' and raw.format != 'PNG':
                    raise ValueError('Choose a PNG logo; transparency is supported.')
                if getattr(raw, 'n_frames', 1) > 1:
                    raise ValueError('Choose a still image. Animated or multi-page images are not supported.')
                image = ImageOps.exif_transpose(raw).convert('RGBA' if kind == 'logo' or 'A' in raw.getbands() or 'transparency' in raw.info else 'RGB')
                value.update(width=image.width, height=image.height)
                image.save(path, 'PNG')
                if kind == 'image':
                    from .auto_framing import focus_point
                    value['focus'] = focus_point(image)
                    ImageOps.contain(image, (256, 160)).convert('RGB').save(folder / f'{asset_id}-thumb.jpg', 'JPEG', quality=80)
            value.update(filename=path.name, size_bytes=path.stat().st_size)
            return self.store.put_asset(value)
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            path.unlink(missing_ok=True)
            raise ValueError(f'The file could not be decoded. Choose a valid {PHOTO_FORMAT_LABELS} image, or MP3, WAV, M4A, Ogg or FLAC audio.') from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _inspect_audio(data, path):
        """Write the file and confirm it is one audio clip of a sensible length; returns seconds."""
        ffprobe = tool('ffprobe')
        if not ffprobe:
            raise ValueError('Audio inspection requires FFprobe. Run Setup and repair.')
        if not (data.startswith((b'ID3', b'fLaC', b'OggS')) or (data[:4] == b'RIFF' and data[8:12] == b'WAVE')
                or data[4:8] == b'ftyp' or (len(data) > 1 and data[0] == 255 and data[1] & 0xe0 == 0xe0)):
            raise ValueError('Choose MP3, WAV, M4A, Ogg, or FLAC audio. Playlists are not supported.')
        path.write_bytes(data)
        info = json.loads(subprocess.run([ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams',
                                          '-show_format', '-of', 'json', str(path)],
                                         capture_output=True, text=True, timeout=10, check=True).stdout)
        if not any(s['codec_type'] == 'audio' for s in info.get('streams', [])):
            raise ValueError('This file has no audio stream.')
        duration = float(info.get('format', {}).get('duration', 0))
        if not 0 < duration <= 600:
            raise ValueError('Choose an audio clip between 0 and 600 seconds.')
        return duration

    def validate_assets(self, reel):
        """Check every file exists and the text fits; returns (images, logo, music)."""
        images = {image_id: self.asset_path(image_id, 'image') for image_id in reel.image_ids()}
        logo = self.asset_path(reel.logo_id, 'logo') if reel.logo_id else None
        if not any(scene.headline or scene.detail for scene in reel.scenes):
            raise ValueError('Add a headline to at least one scene.')
        Plan(reel, 360, images, logo)  # lays out all text; raises a readable error if it cannot fit
        if reel.music == 'custom':
            music = self.asset_path(reel.music_id, 'audio')
        else:
            music = preset_path(reel.music_preset) if reel.music_preset else None
        return images, logo, music

    def thumbnail_path(self, asset_id):
        source = self.asset_path(asset_id, 'image')
        path = source.parent / f'{asset_id}-thumb.jpg'
        if path.is_file():
            return path
        with self.poster_lock:
            with Image.open(source) as image:
                ImageOps.contain(image, (256, 160)).convert('RGB').save(path, 'JPEG', quality=80)
            return path

    def _prepare_images(self, reel, images, scene_index=None):
        if reel.look != 'pop_art':
            return images
        from .cutout import prepare_cutout
        selected = {reel.scenes[scene_index].image_id} if scene_index is not None else set(images)
        return {key: prepare_cutout(path, self.root / 'cutouts') if key in selected else path
                for key, path in images.items()}

    def render_poster(self, reel, scene_index=0):
        """The scene as it looks once its text has animated in. Runs alongside a video render."""
        if not 0 <= scene_index < len(reel.scenes):
            raise ValueError('That scene does not exist.')
        with self.poster_lock:
            key = hashlib.sha256((reel.model_dump_json() + ':' + str(scene_index)).encode()).hexdigest()
            if key in self._posters:
                return self._posters[key]
            images, logo, _ = self.validate_assets(reel)
            images = self._prepare_images(reel, images, scene_index)
            plan = Plan(reel, PREVIEW_WIDTH, images, logo, FPS)
            output = io.BytesIO()
            plan.frame(plan.settled(scene_index)).save(output, format='JPEG', quality=88)
            result = output.getvalue()
            if len(self._posters) >= 12:
                self._posters.pop(next(iter(self._posters)))
            self._posters[key] = result
            return result

    def scene_layout(self, reel, scene_index=0):
        """Element boxes for the scene preview, so the app can select what was clicked, and where its photo can be dragged."""
        if not 0 <= scene_index < len(reel.scenes):
            raise ValueError('That scene does not exist.')
        images, logo, _ = self.validate_assets(reel)  # Hot places its text by where the photo's faces land
        return Plan(reel, PREVIEW_WIDTH, images, logo, FPS).layout(scene_index)

    def create_photo(self, reel, scene_index, ratio):
        """The scene, fully drawn, as a full-size JPEG in shared media for a photo post. The same reel gives the same file."""
        if not 0 <= scene_index < len(reel.scenes):
            raise ValueError('That scene does not exist.')
        if ratio not in RATIOS:
            raise ValueError('Choose 9:16 or 4:5.')
        reel = reel.model_copy(update=dict(ratio=ratio, show_progress=False))  # a still has no progress to show
        photo_id = hashlib.sha256((reel.model_dump_json() + ':' + str(scene_index)).encode()).hexdigest()[:32]
        path = self.photo_path(photo_id)
        if not path.is_file():
            with self.poster_lock:
                images, logo, _ = self.validate_assets(reel)
                images = self._prepare_images(reel, images, scene_index)
                plan = Plan(reel, FINAL_WIDTH, images, logo, FPS)
                self.media_dir.mkdir(parents=True, exist_ok=True)
                partial = path.with_suffix('.part')
                plan.frame(plan.settled(scene_index)).save(partial, format='JPEG', quality=92)
                partial.replace(path)
        return dict(id=photo_id, filename=path.name)

    def photo_path(self, photo_id):
        return self.media_dir / f'reel_photo_{photo_id}.jpg'

    def enqueue(self, value):
        if self.thread and not self.thread.is_alive():
            raise Conflict('The local renderer stopped. Restart the app or backend before rendering.')
        if not self.capabilities()['ready']:
            raise ValueError('Rendering dependencies are missing. Run Settings → Setup and repair.')
        self.validate_assets(value.reel)
        reel = value.reel.model_dump()
        fingerprint = hashlib.sha256(json.dumps([reel, value.kind, value.caption], sort_keys=True).encode()).hexdigest()
        job = self.store.create_job(value.request_id, fingerprint, reel, value.kind, value.caption)
        self.wake.set()
        return job

    def retry(self, job_id):
        job = self.store.job(job_id)
        if job['state'] == 'queued':
            return job
        if job['state'] not in ('failed', 'interrupted', 'cancelled'):
            raise Conflict('Only a stopped render can be retried.')
        self.validate_assets(Reel(**job['reel']))
        # The backend is the sole owner; transactionally ensure concurrent retries
        # cannot turn an already running attempt back into queued work.
        with self.store.db() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (job_id,)).fetchone()
            current = json.loads(row['payload'])
            if current['state'] not in ('failed', 'interrupted', 'cancelled'):
                return current
            current.update(state='queued', error=None, reason=None, cancel_requested=False,
                           progress=0, process=None, requeues=0, updated_at=time.time())
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

    def job_thumbnail_path(self, job):
        """The cover frame saved beside a finished video."""
        name = (job.get('output') or {}).get('thumbnail')
        directory = self.media_dir if job['kind'] == 'final' else self.root / 'previews'
        path = directory / name if name else None
        if not path or path.parent.resolve() != directory.resolve() or not path.is_file():
            raise FileNotFoundError('This video has no cover image.')
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
                            raise Pressured(reason)
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
                            fraction = min(0.98, int(times[-1]) / 1_000_000 / Reel(**job['reel']).duration)
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
        reel = Reel(**job['reel'])
        if reel.music != 'off' and not any(s.get('codec_type') == 'audio' and s.get('codec_name') == 'aac' for s in meta.get('streams', [])):
            raise RuntimeError('Output verification failed: music is missing.')
        expected = reel.size(PREVIEW_WIDTH if job['kind'] == 'preview' else FINAL_WIDTH)
        duration = float(meta.get('format', {}).get('duration', 0))
        if not video or (video['width'], video['height']) != expected or video['codec_name'] != 'h264' or video.get('pix_fmt') != 'yuv420p':
            raise RuntimeError('Output verification failed: unexpected video format.')
        if abs(duration - reel.duration) > 0.2:
            raise RuntimeError('Output verification failed: incomplete duration.')
        self._run(job, [tool('ffmpeg'), '-v', 'error', '-xerror', '-nostdin', '-threads', '1',
                        '-i', str(path), '-f', 'null', '-'], 'decode', recovery)

    def _encode(self, job, reel, images, logo, music, output):
        use_nvenc = self._use_nvenc()
        try:
            self._encode_attempt(job, reel, images, logo, music, output, use_nvenc)
        except EncodingFailed:
            if not use_nvenc:
                raise
            # The device may disappear or run out of encoder sessions after probing.
            # The owned process has stopped; overwrite its partial output on CPU.
            self._nvenc = False
            logger.warning('Reel NVENC failed; restarting video encoding on CPU')
            self._guard(job, dict(started=time.monotonic()), 600)
            self._encode_attempt(job, reel, images, logo, music, output, False)

    def _encode_attempt(self, job, reel, images, logo, music, output, use_nvenc):
        """Draw frames in this process and pipe them to FFmpeg, which stays supervised."""
        directory = Path(job['workdir'])
        plan = Plan(reel, PREVIEW_WIDTH if job['kind'] == 'preview' else FINAL_WIDTH, images, logo, FPS)
        frames = round(plan.duration * FPS)
        seconds = frames / FPS
        command = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-threads', '2',
                   '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{plan.W}x{plan.H}', '-r', str(FPS), '-i', 'pipe:0']
        if music:
            command += ['-stream_loop', '-1', '-protocol_whitelist', 'file,pipe', '-i', str(music)]
        command += ['-map', '0:v']
        if music:
            command += ['-map', '1:a', '-af', f'volume={MUSIC_VOLUME},afade=t=in:d=0.5,afade=t=out:st={max(0, seconds - 1):.2f}:d=1',
                        '-c:a', 'aac', '-b:a', '128k']
        else:
            command += ['-an']
        quality = '24' if job['kind'] == 'preview' else '20'
        encoder = (['-c:v', 'h264_nvenc', '-preset', 'p4', '-rc', 'vbr', '-cq', quality, '-b:v', '0']
                   if use_nvenc else ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', quality])
        command += ['-t', f'{seconds:.3f}', *encoder, '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart', str(output)]
        state = dict(started=time.monotonic())
        process = None
        try:
            with (directory / 'encode.error').open('wb') as stderr:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr,
                                           start_new_session=os.name == 'posix', cwd=directory)
                self.store.update(job['id'], process=dict(pid=process.pid, created=psutil.Process(process.pid).create_time()))
                try:
                    psutil.Process(process.pid).nice(10 if os.name == 'posix' else psutil.BELOW_NORMAL_PRIORITY_CLASS)
                except psutil.Error:
                    pass
                last = 0
                cover = min(round(THUMBNAIL_SECONDS * FPS), frames - 1)
                for number in range(frames):
                    if number % 10 == 0:
                        self._guard(job, state, 600)
                        if (directory / 'encode.error').stat().st_size > 1024 * 1024:
                            raise RuntimeError('The encoder produced excessive errors.')
                        if number / frames > last + .01:
                            last = number / frames
                            self.store.update(job['id'], progress=min(.98, last))
                    frame = plan.frame(number / FPS)
                    if number == cover:
                        frame.save(directory / 'thumbnail.jpg', 'JPEG', quality=90)
                    try:
                        process.stdin.write(frame.tobytes())
                    except (BrokenPipeError, OSError):
                        raise EncodingFailed('The encoder could not finish this video. Check the input files and runtime dependencies.') from None
                try:
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    raise EncodingFailed('The encoder stopped before finishing this video.') from None
                try:
                    process.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    raise EncodingFailed('The encoder did not finish in time.') from None
                if process.returncode:
                    raise EncodingFailed('The encoder could not finish this video. Check the input files and runtime dependencies.')
        finally:
            if process and process.poll() is None:
                stop_owned_process(dict(pid=process.pid, created=psutil.Process(process.pid).create_time()), str(directory))
                process.wait(timeout=5)
            self.store.update(job['id'], process=None)

    def _guard(self, job, state, limit):
        """Stop on cancel, shutdown, timeout, or sustained machine pressure."""
        current = self.store.job(job['id'])
        if current.get('cancel_requested'):
            raise Interrupted('Render cancelled.')
        if self.stop_event.is_set():
            raise Interrupted('The backend stopped. Retry this render when ready.')
        if time.monotonic() - state['started'] > limit:
            raise RuntimeError('Rendering timed out. Try fewer scenes or a shorter reel.')
        reason = self.pressure()
        if reason:
            state['pressured'] = state.get('pressured') or time.monotonic()
            if 'automation' in reason or 'maintenance' in reason or time.monotonic() - state['pressured'] > 5:
                raise Pressured(reason)
        else:
            state['pressured'] = None
        return current

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
            reel = Reel(**job['reel'])
            images, logo, music = self.validate_assets(reel)
            images = self._prepare_images(reel, images)
            output = workdir / 'output.mp4'
            self._encode(job, reel, images, logo, music, output)
            self.store.update(job['id'], state='verifying', progress=0.99)
            self._verify(job, output)
            directory = self.media_dir if job['kind'] == 'final' else self.root / 'previews'
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f'reel_{job["id"]}.mp4'
            thumbnail = directory / f'reel_{job["id"]}.jpg'
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
                    if (workdir / 'thumbnail.jpg').is_file():
                        shutil.copyfile(workdir / 'thumbnail.jpg', thumbnail)
                    # Durability of the directory entry matters across host crashes.
                    if os.name == 'posix':
                        fd = os.open(directory, os.O_RDONLY)
                        try:
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    current.update(state='completed', progress=1, error=None, process=None,
                                   updated_at=time.time(), output=dict(filename=destination.name,
                                   size_bytes=destination.stat().st_size,
                                   thumbnail=thumbnail.name if thumbnail.is_file() else None))
                    db.execute('UPDATE jobs SET payload=? WHERE id=?', (json.dumps(current), job['id']))
            finally:
                temporary.unlink(missing_ok=True)
        except Pressured as exc:
            # The worker resumes queued jobs once the machine is free; nothing was exported.
            current = self.store.job(job['id'])
            if current.get('cancel_requested'):
                self.store.update(job['id'], state='cancelled', error='Render cancelled.', process=None)
            elif current.get('requeues', 0) >= MAX_REQUEUES:
                self.store.update(job['id'], state='interrupted', process=None,
                                  error=f'{exc}. Retry when the machine is available.')
            else:
                self.store.update(job['id'], state='queued', reason=str(exc), error=None, progress=0,
                                  process=None, requeues=current.get('requeues', 0) + 1)
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
