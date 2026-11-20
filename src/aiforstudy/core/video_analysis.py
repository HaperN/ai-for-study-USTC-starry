"""Persistent local video analysis. All model work uses the configured production Gateway.

Public inputs identify controlled uploads; filesystem imports are a separate CLI API.
Process locks are inherited by workers so restarting a server cannot reuse a live job.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid

from .jsonio import write_json_atomic
from .video_progress import merge_deferred, evidence_gaps, stage_label
from ..errors import Cancelled
from ..pipeline.services import CancelToken

VERSION = 'video-analysis-v1.0'
STAGES = ('probe', 'audio', 'frames', 'asr', 'ocr', 'vision', 'lesson', 'reconcile', 'review', 'export')
ACTIVE = {'queued', 'running', 'cancelling'}
ID = re.compile(r'^[a-f0-9]{32}$')
VIDEO_COMPONENTS = ('core/video_analysis.py', 'core/video_analysis_models.py', 'core/video_recognition_worker.py', 'core/video_lecture_reconciliation.py', 'core/video_progress.py')


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _safe_public(value):
    if isinstance(value, dict):
        return {k: _safe_public(v) for k, v in value.items() if k not in {'worker_pid', 'config', 'owner', 'artifacts'}}
    if isinstance(value, list):
        return [_safe_public(v) for v in value]
    if isinstance(value, str) and value.startswith('/'):
        return Path(value).name
    return value


def validate_lesson(value, source_ids, duration):
    """Reject fabricated evidence and out-of-video timestamps; preserve model text."""
    if not isinstance(value, dict) or not isinstance(value.get('sections'), list) or not value['sections']:
        raise ValueError('lesson requires nonempty sections')
    for section in value['sections']:
        if not isinstance(section, dict) or not all(isinstance(section.get(k), str) and section[k].strip() for k in ('id', 'title', 'markdown')):
            raise ValueError('Invalid section text')
        start, end = section.get('start'), section.get('end')
        if not all(type(v) in (float, int) and math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= duration + .1:
            raise ValueError('Invalid section time range')
        refs = section.get('evidence_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in source_ids for r in refs):
            raise ValueError('Invalid evidence source ids')
        inline = re.findall(r'\[((?:frame|asr)-[a-zA-Z0-9-]+)\]', section['markdown'])
        if any(reference not in refs or reference not in source_ids for reference in inline):
            raise ValueError('Invalid inline evidence source ids')
    if not isinstance(value.get('formulas', []), list):
        raise ValueError('Invalid formulas list')
    for formula in value.get('formulas', []):
        if not isinstance(formula, dict):
            raise ValueError('Invalid formula object')
        refs = formula.get('source_ids')
        if not isinstance(formula.get('latex'), str) or not isinstance(refs, list) or not refs or any(not isinstance(r, str) or r not in source_ids for r in refs):
            raise ValueError('Invalid formula source ids')
        if not isinstance(formula.get('uncertainty', ''), str):
            raise ValueError('Invalid formula uncertainty')
    if not isinstance(value.get('uncertainties', []), list) or any(not isinstance(x, str) for x in value.get('uncertainties', [])):
        raise ValueError('Invalid uncertainties')
    return value


class _FileCancel(CancelToken):
    def __init__(self, path):
        super().__init__(grace_seconds=3)
        self.path = path

    def checkpoint(self):
        if self.path.exists():
            self.cancel('用户取消视频解析')
        super().checkpoint()


class VideoAnalysisService:
    def __init__(self, workspace_root):
        self.workspace_root = Path(workspace_root).resolve()
        self.root = self.workspace_root / 'data' / 'video-analysis'
        self.uploads, self.jobs = self.root / 'uploads', self.root / 'jobs'
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.jobs.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self._threads, self._tokens = {}, {}
        self._local = threading.local()
        config_path = self.workspace_root / 'config' / 'video-analysis.json'
        config = _read(config_path) if config_path.is_file() else {}
        self.config = {
            'version': VERSION,
            'asr_python': os.environ.get('AIFORSTUDY_VIDEO_ASR_PYTHON', config.get('asr_python', '')),
            'asr_model': os.environ.get('AIFORSTUDY_VIDEO_ASR_MODEL', config.get('asr_model', '')),
            'ocr_python': os.environ.get('AIFORSTUDY_VIDEO_OCR_PYTHON', config.get('ocr_python', '')),
            'sample': os.environ.get('AIFORSTUDY_VIDEO_SAMPLE', config.get('sample', '')),
            'sample_url': config.get('sample_url', ''),
            'frame_interval': float(config.get('frame_interval', 20)),
            'max_frames': int(config.get('max_frames', 96)),
            'scene_threshold': float(config.get('scene_threshold', .12)),
            'vision_batch_size': int(config.get('vision_batch_size', 6)),
            'chapter_seconds': float(config.get('chapter_seconds', 240)),
            'review': bool(config.get('review', True)),
            'max_duration_seconds': float(config.get('max_duration_seconds', 14400)),
        }
        if self.config['frame_interval'] < 5 or not 4 <= self.config['max_frames'] <= 300 or not 1 <= self.config['vision_batch_size'] <= 8 or self.config['chapter_seconds'] < 30:
            raise ValueError('Invalid video analysis bounds')
        self.max_upload_bytes = int(config.get('max_upload_bytes', 2 * 1024 ** 3))
        for path in self.jobs.glob('*/job.json'):
            self._recover(path.parent.name)

    @contextlib.contextmanager
    def _lock(self, path, blocking=True):
        with Path(path).open('a+b') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield handle
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _jobdir(self, job_id):
        if not isinstance(job_id, str) or not ID.fullmatch(job_id):
            raise KeyError('Unknown video job')
        directory = self.jobs / job_id
        if not (directory / 'job.json').is_file():
            raise KeyError('Unknown video job')
        return directory

    def _recover(self, job_id):
        directory = self._jobdir(job_id)
        with self._mutex:
            job = _read(directory / 'job.json')
            if job['state'] not in ACTIVE or job_id in self._threads:
                return
            try:
                with self._lock(directory / 'worker.lock', False):
                    state = 'cancelled' if (directory / 'cancel.request').exists() else 'interrupted'
                    values = {'state': state, 'error': '上次处理已停止，可恢复已完成阶段。'}
                    if job.get('operation') == 'reconcile' and isinstance(job.get('revision'), dict):
                        values['revision'] = {**job['revision'], 'state': state, 'error': values['error']}
                    self._update(job_id, **values)
            except BlockingIOError:
                pass  # A live worker (including inherited child lock) still owns it.

    def capabilities(self):
        dependencies = {
            'ffmpeg': bool(shutil.which('ffmpeg')),
            'ffprobe': bool(shutil.which('ffprobe')),
            'asr_python': bool(self.config['asr_python'] and Path(self.config['asr_python']).is_file()),
            'asr_model': bool(self.config['asr_model'] and Path(self.config['asr_model']).exists()),
            'ocr_python': bool((self.config['ocr_python'] or self.config['asr_python']) and Path(self.config['ocr_python'] or self.config['asr_python']).is_file()),
        }
        try:
            from ..providers.factory import configured_providers
            from .workspace import Workspace
            dependencies['model_transport'] = configured_providers(SimpleNamespace(profile=SimpleNamespace(value='release')), Workspace(self.workspace_root)).llm is not None
        except Exception:
            dependencies['model_transport'] = False
        return {'available': all(dependencies.values()), 'dependencies': dependencies,
                'max_upload_bytes': self.max_upload_bytes,
                'sample_available': bool(self.config['sample'] and Path(self.config['sample']).is_file()),
                'stages': list(STAGES), 'coverage_note': '音频按重叠窗口覆盖全片；画面为定时和变化采样，并非逐帧解析。'}

    def register_upload(self, stream, length, filename):
        if type(length) is not int or not 0 < length <= self.max_upload_bytes:
            raise ValueError('Invalid upload length or upload exceeds limit')
        filename = str(filename).replace('\\', '/').rsplit('/', 1)[-1].strip()[:180] or 'video.mp4'
        if Path(filename).suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}:
            raise ValueError('Unsupported video filename extension')
        source_id = uuid.uuid4().hex
        partial = self.uploads / (source_id + '.part')
        target = self.uploads / (source_id + '.video')
        digest = hashlib.sha256()
        try:
            remaining = length
            with partial.open('xb') as out:
                while remaining:
                    data = stream.read(min(1024 * 1024, remaining))
                    if not isinstance(data, bytes) or not data or len(data) > remaining:
                        raise ValueError('Upload length mismatch; incomplete upload')
                    out.write(data); digest.update(data); remaining -= len(data)
                out.flush(); os.fsync(out.fileno())
            os.replace(partial, target)
            row = {'id': source_id, 'source_id': source_id, 'filename': filename, 'bytes': length, 'sha256': digest.hexdigest(), 'created_at': time.time()}
            write_json_atomic(self.uploads / (source_id + '.json'), row)
            return row
        except BaseException:
            partial.unlink(missing_ok=True); target.unlink(missing_ok=True)
            raise

    def import_source(self, source_path):
        """Explicit local CLI only. Never called with browser JSON."""
        path = Path(source_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError('Source must be a regular video file')
        with path.open('rb') as stream:
            return self.register_upload(stream, path.stat().st_size, path.name)

    def sample(self):
        if not self.config['sample']:
            raise ValueError('No video sample configured')
        path = Path(self.config['sample'])
        digest = _sha(path)
        for metadata in self.uploads.glob('*.json'):
            row = _read(metadata)
            if row['sha256'] == digest and (self.uploads / (row['id'] + '.video')).is_file():
                return row
        return self.import_source(path)

    def _dependency_identity(self):
        # Reuse the media cache's declared component and effective-provider identity.
        # No credential values or credential digests are read into this descriptor.
        from ..blocks.media.dependencies import component_identity, file_identity, model_role_identity
        from ..pipeline.runtime_dependencies import COMMON
        from .video_recognition_worker import _model_identity
        from ..providers.factory import configured_providers
        from .workspace import Workspace
        dependencies = {'components': component_identity(VIDEO_COMPONENTS + COMMON)}
        model = self.config['asr_model']
        dependencies['asr_model'] = _model_identity(model) if model and Path(model).is_dir() else 'unconfigured'
        runtimes = {}
        worker = Path(__file__).with_name('video_recognition_worker.py')
        script = 'import runpy,sys,json; print(json.dumps(runpy.run_path(sys.argv[1])["_runtime"](sys.argv[2])))'
        for mode in ('asr', 'ocr'):
            interpreter = self.config[mode + '_python'] or (self.config['asr_python'] if mode == 'ocr' else '')
            identity = {'interpreter': file_identity(interpreter)}
            if interpreter and Path(interpreter).is_file():
                result = subprocess.run([interpreter, '-c', script, str(worker), mode], capture_output=True, text=True, timeout=20)
                if result.returncode:
                    raise ValueError('Unable to identify ' + mode + ' runtime')
                identity['runtime'] = json.loads(result.stdout)
            runtimes[mode] = identity
        dependencies['runtimes'] = runtimes
        dependencies['media_tools'] = {tool: file_identity(shutil.which(tool)) for tool in ('ffmpeg', 'ffprobe', 'clang')}
        dependencies['ocr_executable'] = file_identity(os.environ.get('AIFORSTUDY_VIDEO_OCR_EXECUTABLE'))
        config_path = self.workspace_root / 'config' / 'models.json'
        dependencies['roles'] = {}
        if config_path.is_file():
            workspace = Workspace(self.workspace_root)
            providers = configured_providers(SimpleNamespace(profile=SimpleNamespace(value='release')), workspace)
            ctx = SimpleNamespace(workspace=workspace, services=SimpleNamespace(providers=providers))
            roles = set(_read(config_path).get('roles', {})) & {'source_repair', 'lesson', 'text_judge'}
            for role in sorted(roles):
                dependencies['roles'][role] = model_role_identity(ctx, role)
                transport = providers.llm.for_role(role) if hasattr(providers.llm, 'for_role') else providers.llm
                if getattr(transport, 'harness_app', None):
                    app = Path(transport.harness_app)
                    dependencies['roles'][role]['account_sdk'] = {
                        'app_archive': file_identity(app / 'Contents/Resources/app.asar'),
                        'executable': file_identity(app / 'Contents/MacOS/DeepSeek Harness')}
        return dependencies

    def _config_snapshot(self):
        model_config = self.workspace_root / 'config' / 'models.json'
        execution = self.workspace_root / 'config' / 'execution.json'
        return dict(self.config, model_config_sha256=_sha(model_config) if model_config.is_file() else '',
                    execution_policy_sha256=_sha(execution) if execution.is_file() else '', dependencies=self._dependency_identity())

    def submit(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'source_id', 'title', 'course_id', 'force'}:
            raise ValueError('Only source_id, title, course_id and force are accepted')
        source_id = payload.get('source_id')
        if not isinstance(source_id, str) or not ID.fullmatch(source_id):
            raise ValueError('Invalid source_id')
        path = self.uploads / (source_id + '.json')
        if not path.is_file():
            raise KeyError('Unknown source_id')
        source = _read(path)
        if _sha(self.uploads / (source_id + '.video')) != source['sha256']:
            raise ValueError('Uploaded source integrity mismatch')
        title = str(payload.get('title') or Path(source['filename']).stem)[:200]
        course_id = str(payload.get('course_id') or 'video-course')[:100]
        cfg = self._config_snapshot()
        identity = _digest({'source': source['sha256'], 'config': cfg, 'title': title, 'course_id': course_id})
        with self._lock(self.root / 'registry.lock'):
            if not payload.get('force'):
                for path in self.jobs.glob('*/job.json'):
                    existing = _read(path)
                    if existing['identity'] == identity:
                        return self.get(existing['job_id'])
            job_id = uuid.uuid4().hex; directory = self.jobs / job_id; directory.mkdir()
            row = {'job_id': job_id, 'source_id': source_id, 'title': title, 'course_id': course_id,
                   'identity': identity, 'config': cfg, 'source_sha256': source['sha256'], 'source_filename': source['filename'],
                   'state': 'queued', 'stage': 'queued', 'progress': 0, 'created_at': time.time(), 'updated_at': time.time(),
                   'stages': {s: {'state': 'pending', 'progress': 0} for s in STAGES}, 'events': [], 'artifacts': [], 'error': None}
            write_json_atomic(directory / 'job.json', row)
            self._launch(job_id)
            return self.get(job_id)

    def list_jobs(self):
        return sorted([self.get(p.parent.name, include_result=False) for p in self.jobs.glob('*/job.json')], key=lambda x: x['created_at'], reverse=True)

    def get(self, job_id, include_result=True):
        row = _read(self._jobdir(job_id) / 'job.json')
        if row['state'] in {'running', 'cancelling'} and job_id not in self._threads:
            self._recover(job_id)
            row = _read(self._jobdir(job_id) / 'job.json')
        # Queued jobs may legitimately be waiting for their first thread start.
        public = _safe_public(row)
        if include_result:
            relative = row.get('active_result', 'result.json')
            result_path = self.artifact(job_id, relative)
            published_baseline = relative in row.get('artifacts', []) and (self._jobdir(job_id) / 'export.done.json').is_file()
            if result_path is None and (row.get('active_result') or published_baseline):
                raise ValueError('Published result integrity check failed（发布结果完整性校验失败）')
            if result_path and (row['state'] == 'completed' or row.get('operation') == 'reconcile'):
                public['result'] = _read(result_path)
                public['result'].setdefault('exports', {'json': relative, 'markdown': 'lesson.md', 'original_json': 'result.json', 'original_markdown': 'lesson.md'})
        return public

    def _update(self, job_id, **values):
        directory = self._jobdir(job_id)
        with self._mutex, self._lock(directory / 'metadata.lock'):
            row = _read(directory / 'job.json'); row.update(values); row['updated_at'] = time.time()
            write_json_atomic(directory / 'job.json', row)
            return row

    def _event(self, job_id, message, **values):
        with self._mutex:
            row = _read(self._jobdir(job_id) / 'job.json')
            event = {'time': time.time(), 'message': message, **values}
            row['events'] = (row['events'] + [event])[-200:]
            with (self._jobdir(job_id) / 'events.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(event, ensure_ascii=False) + '\n')
            self._update(job_id, events=row['events'])

    def cancel(self, job_id):
        row = self.get(job_id, False)
        if row['state'] in ACTIVE:
            (self._jobdir(job_id) / 'cancel.request').touch()
            token = self._tokens.get(job_id)
            if token:
                token.cancel('用户取消视频解析')
            self._update(job_id, state='cancelling')
            if job_id not in self._threads:
                self._recover(job_id)
        return self.get(job_id)

    def resume(self, job_id):
        directory = self._jobdir(job_id)
        self._recover(job_id)
        row = _read(directory / 'job.json')
        if row.get('operation') == 'reconcile' and row['state'] not in ACTIVE and row['state'] != 'completed':
            return self.reconcile(job_id, retry_revision_id=row.get('revision', {}).get('id'))
        if row['state'] == 'completed':
            return self.get(job_id)
        if row['state'] in ACTIVE:
            raise ValueError('任务仍有活动worker，不能重复恢复')
        current_config = self._config_snapshot()
        if row['config'] != current_config:
            raise ValueError('运行配置已改变，请重新分析，不能混用先前结果')
        (directory / 'cancel.request').unlink(missing_ok=True)
        self._update(job_id, state='queued', error=None)
        self._launch(job_id)
        return self.get(job_id)

    def reconcile(self, job_id, *, retry_revision_id=None, review_feedback=None):
        """Publish a new derived revision without changing baseline stage receipts."""
        directory = self._jobdir(job_id); self._recover(job_id)
        handle = (directory / 'worker.lock').open('a+b')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close(); raise ValueError('任务仍在运行，不能同时校订')
        try:
            row = _read(directory / 'job.json')
            if row['state'] in ACTIVE:
                raise ValueError('任务仍在运行，不能同时校订')
            relative = row.get('active_result', 'result.json')
            source = self.artifact(job_id, relative)
            if source is None:
                raise ValueError('尚无完整讲义可校订')
            # Always check original publication plus current parent revision bytes.
            receipt = _read(directory / 'export.done.json')
            for path, digest in receipt['files'].items():
                if _sha(directory / path) != digest:
                    raise ValueError('原始导出产物已改变，拒绝混合来源')
            if row.get('active_result_sha256') and _sha(source) != row['active_result_sha256']:
                raise ValueError('现行修订完整性校验失败')
            if retry_revision_id is not None and (not isinstance(retry_revision_id, str) or not ID.fullmatch(retry_revision_id)):
                raise ValueError('Invalid revision id')
            revision_id = retry_revision_id or uuid.uuid4().hex
            output = directory / 'revisions' / revision_id
            previous_record = None
            if retry_revision_id:
                if (output.resolve() != output.absolute() or (output / 'revision.json').is_symlink()
                        or not (output / 'revision.json').is_file()):
                    raise ValueError('Unknown failed revision')
                previous_record = _read(output / 'revision.json')
                intent = row.get('revision') or {}
                if intent.get('publication_digest') and _digest(previous_record) not in {
                        intent['publication_digest'], intent.get('previous_record_digest')}:
                    raise ValueError('Revision publication receipt integrity mismatch')
                if previous_record.get('parent_result') != relative or previous_record.get('parent_result_sha256') != _sha(source):
                    raise ValueError('Revision parent changed')
                if previous_record.get('state') == 'completed':
                    self._publish_revision(job_id, output, previous_record)
                    handle.close()
                    return self.get(job_id)
            else:
                output.mkdir(parents=True)
            if review_feedback is not None and not isinstance(review_feedback, (dict, str)):
                raise ValueError('Review feedback must be text or an object')
            snapshot = self._config_snapshot()
            commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.workspace_root, capture_output=True, text=True, timeout=10)
            producer = {'git_commit': commit.stdout.strip() if commit.returncode == 0 else None,
                        'dependency_digest': _digest(snapshot['dependencies']), 'configuration_digest': _digest(snapshot),
                        'operation': 'whole_lecture_reconciliation_of_preserved_evidence'}
            if previous_record:
                previous_producer = previous_record.get('latest_producer', previous_record.get('producer', {}))
                changed = any(previous_producer.get(key) != producer[key] for key in ('dependency_digest', 'configuration_digest'))
                successful = any(_read(path).get('ok') and not _read(path).get('validation_error')
                                 for path in (output / 'exchanges').glob('*.output.json'))
                if changed and successful:
                    raise ValueError('Revision producer/dependencies changed after a successful exchange; create a new revision（生产身份已变，请新建校订）')
            revision = {'id': revision_id, 'state': 'queued', 'stage': 'reconcile', 'error': None,
                        'parent_result': relative, 'parent_result_sha256': _sha(source),
                        'producer_dependency_digest': producer['dependency_digest']}
            producers = (previous_record.get('producer_attempts') or [previous_record['producer']]) if previous_record else []
            prior_attempts = previous_record.get('attempt_history', []) if previous_record else []
            if previous_record:
                prior_attempts = prior_attempts + [{key: previous_record.get(key) for key in ('state', 'stage', 'error', 'producer_dependency_digest')}]
                failure = output / 'failure.log'
                if failure.is_file():
                    failure.rename(output / ('failure.attempt-' + str(len(producers)) + '.log'))
            record = {**(previous_record or {}), **revision, 'producer': producers[0] if producers else producer,
                'producer_attempts': producers + [producer], 'latest_producer': producer, 'attempt_history': prior_attempts,
                'baseline_run_identity': row['identity'], 'baseline_dependencies': row['config'].get('dependencies', {})}
            if review_feedback is not None:
                feedback_name = 'source-quality-feedback-' + str(len(producers) + 1) + '.json'
                write_json_atomic(output / feedback_name, {'kind': 'source_checked_revision_feedback',
                    'not_independent_mathematical_approval': True, 'content': review_feedback})
                record['source_quality_feedback'] = feedback_name
            write_json_atomic(output / 'revision.json', record)
            (directory / 'cancel.request').unlink(missing_ok=True)
            token = _FileCancel(directory / 'cancel.request'); self._tokens[job_id] = token
            self._update(job_id, state='queued', stage='reconcile', operation='reconcile', revision=revision, error=None, progress=0)
            thread = threading.Thread(target=self._revision_run, args=(job_id, token, handle, revision, producer), daemon=True)
            self._threads[job_id] = thread; thread.start()
            return self.get(job_id)
        except BaseException:
            handle.close(); raise

    def _publish_revision(self, job_id, output, record):
        directory = self._jobdir(job_id)
        row = _read(directory / 'job.json')
        revision = row.get('revision', {})
        parent = row.get('active_result', 'result.json')
        if (output.resolve() != output.absolute() or record.get('state') != 'completed'
                or record.get('id') != output.name or record.get('baseline_run_identity') != row['identity']
                or any(record.get(k) != revision.get(k) for k in
                       ('id', 'parent_result', 'parent_result_sha256', 'producer_dependency_digest'))
                or record.get('parent_result') != parent or self.artifact(job_id, parent) is None
                or _sha(directory / parent) != record['parent_result_sha256']
                or record.get('latest_producer', {}).get('dependency_digest') != revision.get('producer_dependency_digest')
                or ('publication_digest' in revision and revision['publication_digest'] != _digest(record))):
            raise ValueError('Revision publication identity or parent changed')
        files = record.get('files')
        if not isinstance(files, dict) or not {'result.json', 'lesson.md', 'corrections.json'} <= files.keys():
            raise ValueError('Revision export receipt incomplete')
        for name, expected in files.items():
            path = output / name
            if (not name or '\\' in name or path.resolve() != path.absolute() or not path.resolve().is_relative_to(output)
                    or Path(name).as_posix() != name or Path(name).is_absolute() or '..' in Path(name).parts
                    or not path.is_file() or not isinstance(expected, str) or _sha(path) != expected):
                raise ValueError('Revision artifact integrity mismatch')
        result = _read(output / 'result.json')
        provenance = result.get('provenance', {})
        prefix = output.relative_to(directory).as_posix() + '/'
        exports = result.get('exports', {})
        if (not isinstance(exports, dict) or exports.get('original_json') != 'result.json'
                or exports.get('original_markdown') != 'lesson.md' or exports.get('parent_json', parent) != parent
                or result.get('job_id') != job_id or provenance.get('revision_id') != output.name
                or provenance.get('parent_result_sha256') != record['parent_result_sha256']
                or provenance.get('reconciliation_producer') != record.get('latest_producer')
                or any(exports.get(k) != prefix + n for k, n in
                       [('json', 'result.json'), ('markdown', 'lesson.md'), ('corrections', 'corrections.json')])):
            raise ValueError('Revision result/export identity mismatch')
        for key, name in exports.items():
            if key not in {'json', 'markdown', 'corrections'} and self.artifact(job_id, name) is None:
                raise ValueError('Historical export integrity mismatch')
        self._update(job_id, active_result=exports['json'], active_result_sha256=files['result.json'],
            artifacts=sorted(set(row['artifacts']) | {prefix + n for n in files}), exports=exports,
            state='completed', stage='completed', progress=1, error=None,
            revision={**revision, 'state': 'completed', 'stage': 'completed', 'error': None})

    def _review_sources(self, job_id, base):
        reviews = base.get('provenance', {}).get('followup_source_reviews', [])
        relative = base.get('exports', {}).get('review_evidence')
        context = {'followup_source_reviews': reviews} if reviews else {}
        if not relative:
            return context
        evidence = self.artifact(job_id, relative)
        if evidence is None:
            raise ValueError('Source review evidence integrity mismatch')
        parts = Path(relative).parts
        if parts[0] != 'revisions':
            baseline_files = _read(self._jobdir(job_id) / 'export.done.json')['files']
            if relative not in baseline_files:
                raise ValueError('Missing source review evidence hash')
        context['review_evidence'] = _read(evidence)
        if len(parts) == 4 and parts[0] == 'revisions' and parts[2] == 'evidence':
            # artifact() verified every member of this revision against its receipt.
            files = _read(evidence.parent.parent / 'revision.json')['files']
            layers = {}
            for name in ['asr-turbo-full', 'asr-qwen', 'asr-whisper', 'dense-frame-index', 'audio-clip-index']:
                filename = name + '.json'
                if 'evidence/' + filename not in files:
                    continue
                data = _read(evidence.parent / filename)
                if name.startswith('asr-'):
                    segments = data.get('result', data)['segments']
                    data = {**{k: data[k] for k in ('engine', 'model', 'revision', 'note') if k in data},
                            'segments': [{k: r[k] for k in ('id', 'clip', 'start', 'end', 'text') if k in r}
                                         for r in segments]}
                layers[filename] = {'artifact': str(Path(relative).with_name(filename)), 'sha256': files['evidence/' + filename], 'data': data}
            context['supplemental_sources'] = layers
            context['scope'] = '自动转写及带时间索引的资料。索引本身不提供画面内容或人工听审证明。'
        return context

    def _revision_run(self, job_id, token, handle, revision, producer):
        from .video_analysis_models import VideoModelSession
        from .video_lecture_reconciliation import reconcile_lecture, re_review_lecture
        from copy import deepcopy
        self._local.lock_fds = [handle.fileno()]; self._local.job_id = job_id
        directory = self._jobdir(job_id); output = directory / 'revisions' / revision['id']
        prefix = 'revision-' + revision['id'][:16]
        def progress(stage, fraction):
            revision.update(state='running', stage=stage)
            self._update(job_id, state='running', stage=stage, revision=dict(revision), progress=fraction)
            self._event(job_id, '全片校订：' + stage, revision_id=revision['id'])
        try:
            progress('reconcile', .05); token.checkpoint()
            base = _read(directory / revision['parent_result'])
            lesson = {key: deepcopy(base.get(key, [])) for key in ('sections', 'formulas', 'uncertainties', 'deferred_work')}
            frames, transcript, duration = base['frames'], base['transcript'], base['duration']
            # Bind the actual preserved recognitions as well as the parent result.
            write_json_atomic(output / 'input-binding.json', {'parent_result': revision['parent_result'],
                'parent_result_sha256': revision['parent_result_sha256'], 'frames_digest': _digest(frames),
                'transcript_digest': _digest(transcript), 'lesson_digest': _digest(lesson)})
            session = VideoModelSession(self, job_id, token, directory=output)
            prior_review = base.get('review')
            revision_record = _read(output / 'revision.json')
            if revision_record.get('source_quality_feedback'):
                feedback = _read(output / revision_record['source_quality_feedback'])
                prior_review = {'previous_model_review': prior_review, 'source_quality_feedback': feedback}
            sources = self._review_sources(job_id, base)
            source_args = {'source_review': sources} if sources else {}
            corrected = reconcile_lecture(session, lesson, frames, transcript, duration, prefix, review=prior_review, **source_args)
            progress('global_review', .55); token.checkpoint()
            review = re_review_lecture(session, corrected, frames, transcript, duration, prefix, **source_args)
            progress('export', .9); token.checkpoint()
            result = deepcopy(base)
            for key in ('sections', 'formulas', 'uncertainties', 'corrections', 'reconciliation', 'deferred_work'):
                result[key] = corrected.get(key, [])
            result['deferred_work'] = merge_deferred(result['deferred_work'], review.get('deferred_work', []))
            result['review'] = review; result['quality_state'] = 'candidate'; result['production_accepted'] = False
            result['provenance'] = {**base.get('provenance', {}),
                'recognition_producer': deepcopy(base.get('provenance', {}).get('recognition_producer', base.get('provenance', {}))),
                'reconciliation_producer': producer, 'reconciliation_producer_attempts': revision_record.get('producer_attempts', [producer]), 'parent_result_sha256': revision['parent_result_sha256'],
                'revision_id': revision['id'], 'recognition_reused_without_modification': True}
            provenance = result['provenance']
            archived = {}
            if 'editorial_revision' in result:
                archived['result_editorial_revision'] = result.pop('editorial_revision')
            for key in ('editorial_revision', 'active_editorial_producer'):
                if key in provenance:
                    archived[key] = provenance.pop(key)
            if archived:
                archived['review'] = base.get('review')
                if 'recognition_producer' not in base.get('provenance', {}):
                    for key in ('active_editorial_producer', 'editorial_revision', 'followup_source_reviews', 'editorial_history'):
                        provenance['recognition_producer'].pop(key, None)
                provenance.setdefault('editorial_history', []).append(
                    {'parent_result': revision['parent_result'], 'parent_sha256': revision['parent_result_sha256'], **archived})
            if sources.get('supplemental_sources'):
                provenance['review_source_artifacts'] = [
                    {'path': layer['artifact'], 'sha256': layer['sha256']}
                    for layer in sources['supplemental_sources'].values()]
            relative = output.relative_to(directory).as_posix()
            result['exports'] = {'json': relative + '/result.json', 'markdown': relative + '/lesson.md',
                'corrections': relative + '/corrections.json', 'original_json': 'result.json', 'original_markdown': 'lesson.md'}
            if base.get('exports', {}).get('review_evidence'):
                result['exports']['review_evidence'] = base['exports']['review_evidence']
            result['exports']['parent_json'] = revision['parent_result']
            write_json_atomic(output / 'result.json', result); write_json_atomic(output / 'corrections.json', corrected['corrections'])
            (output / 'lesson.md').write_text(self._markdown(result['title'], result, '../../' + base['source']['path']), encoding='utf-8')
            token.checkpoint()
            revision.update(state='completed', stage='completed', error=None)
            record = _read(output / 'revision.json')
            previous_record_digest = _digest(record)
            record.update(revision)
            public_files = [p.relative_to(directory).as_posix() for p in output.iterdir() if p.is_file() and p.suffix in {'.json', '.md'} and p.name not in {'revision.json', 'input-binding.json'}]
            registered = _read(directory / 'job.json')['artifacts']
            public_files = sorted(set(public_files) | {path for path in registered if path.startswith(relative + '/')})
            record['files'] = {Path(path).relative_to(Path(relative)).as_posix(): _sha(directory / path) for path in public_files}
            self._update(job_id, revision={**revision, 'state': 'running', 'publication_digest': _digest(record),
                                           'previous_record_digest': previous_record_digest})
            write_json_atomic(output / 'revision.json', record)
            self._publish_revision(job_id, output, record)
            self._event(job_id, '全片校订和独立复审已完成；原始结果与识别稿保留', revision_id=revision['id'])
        except Exception as exc:
            import traceback
            (output / 'failure.log').write_text(traceback.format_exc())
            state = 'cancelled' if isinstance(exc, Cancelled) or token.cancelled else 'failed'
            message = type(exc).__name__ + ': ' + re.sub(r'/[^\s\"\']+', '[local-path]', str(exc))[:600]
            revision.update(state=state, error=message)
            record = _read(output / 'revision.json')
            if record.get('state') != 'completed':
                record.update(revision)
                write_json_atomic(output / 'revision.json', record)
            current = _read(directory / 'job.json')
            if current.get('active_result') == output.relative_to(directory).as_posix() + '/result.json':
                raise  # publication succeeded before the ancillary event write failed
            self._update(job_id, state=state, error=message, revision={**current['revision'], **revision})
            self._event(job_id, '校订未发布：' + message, revision_id=revision['id'])
        finally:
            handle.close(); self._tokens.pop(job_id, None); self._threads.pop(job_id, None)

    @staticmethod
    def _markdown(title, lesson, source):
        summary = ('> 本轮解析已结束，含局部待核或未完成检查；有效讲义与原始来源均保留。'
                   if lesson.get('deferred_work') else '> 根据完整识别来源统一校订的候选讲义；原始识别与纠错记录保留。')
        lines = ['# ' + title, '', summary, '']
        for section in lesson['sections']:
            seconds = int(section['start'])
            warning = ('> 本段包含未核实来源或尚未完成的整理；依赖这些来源的结论仍待核实。\n\n'
                       if section.get('processing_status') in {'partial', 'deferred'} else '')
            lines += [f"## {section['title']}（{seconds // 60:02}:{seconds % 60:02}）", '', warning + section['markdown'], '',
                '[回看原片](' + source + '#t=' + str(seconds) + ') · 来源：' + ', '.join(section['evidence_ids']), '']
        if lesson.get('uncertainties'):
            lines += ['## 尚待核对', ''] + ['- ' + text for text in lesson['uncertainties']]
        if lesson.get('deferred_work'):
            lines += ['', '## 局部待核与未完成检查', '']
            for item in lesson['deferred_work']:
                lines += [f"- {stage_label(item.get('stage'))}（待核/未完成），{item['start']:g}–{item['end']:g} 秒：{item['message']} " +
                          ''.join('[' + ref + ']' for ref in item['source_ids'])]
        for review in lesson.get('provenance', {}).get('followup_source_reviews', []):
            if review.get('external_sources'):
                lines += ['', '## 来源核查资料', '']
                for citation in review['external_sources']:
                    lines.append('- ' + ' · '.join(str(citation[k]) for k in
                        ('id', 'title', 'url', 'accessed', 'supports') if citation.get(k)))
        exports = lesson.get('exports', {})
        paths = [r['path'] for r in lesson.get('provenance', {}).get('review_source_artifacts', [])]
        if exports.get('review_evidence'):
            paths.insert(0, exports['review_evidence'])
        if paths:
            lines += ['', '## 复核证据文件', '']
            for path in paths:
                link = os.path.relpath(path, Path(exports.get('markdown', 'lesson.md')).parent)
                lines.append(f'- [{Path(path).name}]({link})')
        return '\n'.join(lines)

    def _register_artifacts(self, job_id, paths):
        with self._mutex:
            row = _read(self._jobdir(job_id) / 'job.json')
            self._update(job_id, artifacts=sorted(set(row['artifacts']) | set(paths)))

    def artifact(self, job_id, relative):
        try:
            directory = self._jobdir(job_id)
            if not isinstance(relative, str) or '\\' in relative or '..' in Path(relative).parts or Path(relative).is_absolute():
                return None
            row = _read(directory / 'job.json')
            if relative not in row['artifacts']:
                return None
            target = (directory / relative).resolve()
            if not target.is_relative_to(directory.resolve()) or not target.is_file():
                return None
            baseline_receipt = directory / 'export.done.json'
            if Path(relative).parts[0] != 'revisions' and baseline_receipt.is_file():
                files = _read(baseline_receipt).get('files', {})
                # Only this registered historical export is checked; unrelated
                # original media and private stage files are outside this gate.
                if relative in files:
                    expected = files[relative]
                    if not isinstance(expected, str) or _sha(target) != expected.removeprefix('sha256:'):
                        return None
            if Path(relative).parts[0] == 'revisions':
                if len(Path(relative).parts) < 3:
                    return None
                revision_dir = directory.joinpath(*Path(relative).parts[:2])
                receipt_path = revision_dir / 'revision.json'
                if receipt_path.is_symlink() or not receipt_path.is_file():
                    return None
                receipt = _read(receipt_path); files = receipt.get('files')
                if receipt.get('state') != 'completed' or not isinstance(files, dict) or not files:
                    return None
                requested = Path(relative).relative_to(Path(*Path(relative).parts[:2])).as_posix()
                if requested not in files:
                    return None
                # The published revision is one immutable bundle. A modified
                # companion export invalidates reads of that revision too.
                for name, expected in files.items():
                    if not isinstance(name, str) or not isinstance(expected, str) or Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name:
                        return None
                    member = revision_dir / name
                    if member.is_symlink() or not member.resolve().is_relative_to(revision_dir.resolve()) or not member.is_file():
                        return None
                    if _sha(member) != expected.removeprefix('sha256:'):
                        return None
            if relative == row.get('active_result'):
                expected = row.get('active_result_sha256')
                if not isinstance(expected, str) or _sha(target) != expected.removeprefix('sha256:'):
                    return None
            return target
        except (KeyError, OSError, ValueError):
            return None

    def _launch(self, job_id):
        handle = (self._jobdir(job_id) / 'worker.lock').open('a+b')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close(); raise ValueError('已有worker正在处理此任务')
        token = _FileCancel(self._jobdir(job_id) / 'cancel.request')
        self._tokens[job_id] = token
        thread = threading.Thread(target=self._run, args=(job_id, token, handle), name='video-' + job_id[:8], daemon=True)
        self._threads[job_id] = thread; thread.start()

    def _process(self, args, log, token, timeout=3600):
        """No shell; isolate process group; cancel, kill on grace expiry, always reap."""
        env = dict(os.environ)
        env['PYTHONPATH'] = str(self.workspace_root / 'src') + os.pathsep + env.get('PYTHONPATH', '')
        start = time.monotonic()
        last_progress = 0.0
        with Path(log).open('wb') as output:
            process = subprocess.Popen([str(x) for x in args], stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True, env=env, pass_fds=tuple(getattr(self._local, 'lock_fds', ())))
            try:
                while process.poll() is None:
                    token.checkpoint()
                    if time.monotonic() - start > timeout:
                        raise TimeoutError('Video worker exceeded deadline')
                    if '--mode' in args and '--output' in args and time.monotonic() - last_progress >= 1:
                        last_progress = time.monotonic()
                        mode = args[args.index('--mode') + 1]
                        partial = Path(args[args.index('--output') + 1])
                        if partial.is_file() and getattr(self._local, 'job_id', None):
                            snapshot = _read(partial); coverage = snapshot.get('coverage', {})
                            expected = coverage.get('expected_windows' if mode == 'asr' else 'expected_frames', 0)
                            completed = len(snapshot.get('segments' if mode == 'asr' else 'frames', []))
                            if expected:
                                self._progress(self._local.job_id, mode, min(.99, completed / expected))
                    time.sleep(.1)
                token.checkpoint()
                if process.returncode:
                    raise RuntimeError('视频处理子进程失败：' + Path(log).name + '; exit=' + str(process.returncode))
            finally:
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                else:
                    process.wait()

    def _progress(self, job_id, stage, fraction):
        with self._mutex:
            row = _read(self._jobdir(job_id) / 'job.json')
            row['stages'][stage].update(progress=fraction, state='running')
            self._update(job_id, stages=row['stages'], progress=(STAGES.index(stage) + fraction) / len(STAGES))

    def _stage(self, job_id, stage, token, fn):
        token.checkpoint(); directory = self._jobdir(job_id); checkpoint = directory / (stage + '.done.json')
        row = _read(directory / 'job.json')
        if checkpoint.is_file():
            saved = _read(checkpoint)
            if all((directory / p).is_file() and _sha(directory / p) == sha for p, sha in saved['files'].items()):
                return saved['data']
            raise ValueError('阶段产物完整性校验失败，请重新分析')
        self._update(job_id, stage=stage)
        self._progress(job_id, stage, 0)
        self._event(job_id, '开始 ' + stage, stage=stage)
        data, files = fn()
        token.checkpoint()
        write_json_atomic(checkpoint, {'data': data, 'files': {p: _sha(directory / p) for p in files}})
        row = _read(directory / 'job.json'); row['stages'][stage].update(state='completed', progress=1, error=None)
        self._update(job_id, stages=row['stages'], progress=(STAGES.index(stage) + 1) / len(STAGES))
        self._event(job_id, '完成 ' + stage, stage=stage)
        return data

    def _run(self, job_id, token, handle):
        self._local.lock_fds = [handle.fileno()]
        self._local.job_id = job_id
        try:
            self._update(job_id, state='running', error=None)
            self._pipeline(job_id, token)
            token.checkpoint()
            self._update(job_id, state='completed', stage='completed', progress=1)
            self._event(job_id, '解析完成；采样覆盖不等于数学与语义全部核实')
        except Exception as exc:
            cancelled = isinstance(exc, Cancelled) or token.cancelled
            state = 'cancelled' if cancelled else 'failed'
            # Full diagnostics remain local, never expose arbitrary absolute paths.
            import traceback
            (self._jobdir(job_id) / 'failure.log').write_text(traceback.format_exc())
            message = '用户取消' if cancelled else type(exc).__name__ + ': ' + re.sub(r'/[^\s\"\']+', '[local-path]', str(exc))[:600]
            row = _read(self._jobdir(job_id) / 'job.json')
            stage = row.get('stage')
            if stage in row['stages']:
                row['stages'][stage].update(state=state, error=message)
            self._update(job_id, state=state, error=message, stages=row['stages'])
            self._event(job_id, message, stage=stage)
        finally:
            handle.close(); self._tokens.pop(job_id, None); self._threads.pop(job_id, None)

    def _pipeline(self, job_id, token):
        from .video_analysis_models import VideoModelSession
        d = self._jobdir(job_id); row = _read(d / 'job.json')
        source = d / ('source' + Path(row['source_filename']).suffix.lower())
        if not source.is_file():
            shutil.copyfile(self.uploads / (row['source_id'] + '.video'), source)
        if _sha(source) != row['source_sha256']:
            raise ValueError('Source integrity mismatch')
        self._register_artifacts(job_id, [source.name])
        run = lambda args, name, timeout=3600: self._process(args, d / name, token, timeout)
        def probe():
            run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', source], 'probe.json', 60)
            info = _read(d / 'probe.json')
            duration = float(info['format']['duration'])
            if not math.isfinite(duration) or not 0 < duration <= self.config['max_duration_seconds']:
                raise ValueError('Video duration exceeds configured range')
            types = {s['codec_type'] for s in info['streams']}
            if not {'video', 'audio'} <= types:
                raise ValueError('Teaching video requires video and audio streams')
            self._update(job_id, duration=duration)
            return {'duration': duration}, ['probe.json']
        info = self._stage(job_id, 'probe', token, probe); duration = info['duration']
        def audio():
            run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', source, '-map', '0:a:0', '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', d / 'audio.wav'], 'audio.log')
            run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', source, '-map', '0:a:0', '-vn', '-ar', '48000', '-ac', '1', '-c:a', 'libmp3lame', '-b:a', '96k', d / 'speech.mp3'], 'speech.log')
            run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', source, '-map', '0:v:0', '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', d / 'playback.mp4'], 'playback.log')
            self._register_artifacts(job_id, ['playback.mp4', 'speech.mp3'])
            return {}, ['audio.wav', 'speech.mp3', 'playback.mp4']
        self._stage(job_id, 'audio', token, audio)
        frames = self._stage(job_id, 'frames', token, lambda: self._extract_frames(job_id, source, duration, token))
        def asr():
            python, model = self.config['asr_python'], self.config['asr_model']
            if not python or not model:
                raise ValueError('Configure AIFORSTUDY_VIDEO_ASR_PYTHON and AIFORSTUDY_VIDEO_ASR_MODEL')
            # One GPU owner across jobs and server instances, inherited by ASR child.
            gpu = (self.root / 'gpu.lock').open('a+b')
            try:
                while True:
                    token.checkpoint()
                    try:
                        fcntl.flock(gpu, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                    except BlockingIOError:
                        time.sleep(.2)
                self._local.lock_fds.append(gpu.fileno())
                run([python, '-m', 'aiforstudy.core.video_recognition_worker', '--mode', 'asr', '--input', d / 'audio.wav', '--output', d / 'asr.json', '--model', model, '--chunk-seconds', '20', '--overlap-seconds', '2', '--cancel-file', d / 'cancel.request'], 'asr.log', 14400)
            finally:
                if gpu.fileno() in self._local.lock_fds:
                    self._local.lock_fds.remove(gpu.fileno())
                gpu.close()
            data = _read(d / 'asr.json')
            if data.get('status') not in {'completed', 'completed_with_warnings', 'partial'} or not data.get('segments'):
                raise ValueError('ASR did not complete all audio windows')
            return data, ['asr.json']
        transcript = self._stage(job_id, 'asr', token, asr)
        def ocr():
            python = self.config['ocr_python'] or self.config['asr_python']
            if not python:
                raise ValueError('Configure AIFORSTUDY_VIDEO_OCR_PYTHON')
            manifest = {'frames': [{**f, 'path': str(d / f['path'])} for f in frames]}
            write_json_atomic(d / 'ocr-input.json', manifest)
            run([python, '-m', 'aiforstudy.core.video_recognition_worker', '--mode', 'ocr', '--input', d / 'ocr-input.json', '--output', d / 'ocr.json', '--cancel-file', d / 'cancel.request'], 'ocr.log')
            data = _read(d / 'ocr.json')
            if data.get('status') not in {'completed', 'completed_with_warnings', 'partial'} or {f['id'] for f in data['frames']} != {f['id'] for f in frames}:
                raise ValueError('OCR frames do not match sampled evidence')
            return data, ['ocr.json']
        recognized = self._stage(job_id, 'ocr', token, ocr)
        by_id = {f['id']: f for f in recognized['frames']}
        frames = [{**f, 'text': by_id[f['id']].get('text', ''), 'ocr_status': by_id[f['id']].get('status', 'completed'),
                   'ocr_quality_warnings': by_id[f['id']].get('quality_warnings', []) + (['recognition_failed'] if by_id[f['id']].get('status') == 'failed' else []),
                   'ocr_observations': by_id[f['id']].get('observations', []), 'ocr_raw_output': by_id[f['id']].get('raw_output', {})} for f in frames]
        models = VideoModelSession(self, job_id, token)
        frames = self._stage(job_id, 'vision', token, lambda: models.vision(frames, transcript, duration))
        lesson = self._stage(job_id, 'lesson', token, lambda: models.lesson(frames, transcript, duration))
        from .video_lecture_reconciliation import reconcile_lecture, re_review_lecture
        def reconcile_stage():
            corrected = reconcile_lecture(models, lesson, frames, transcript, duration, 'full-lecture')
            return corrected, ['full-lecture.reconciliation.json']
        lesson = self._stage(job_id, 'reconcile', token, reconcile_stage)
        def review_stage():
            report = re_review_lecture(models, lesson, frames, transcript, duration, 'full-lecture')
            write_json_atomic(d / 'review.json', report)
            return report, ['review.json', 'full-lecture.review.json']
        review = self._stage(job_id, 'review', token, review_stage)
        def export():
            deferred = merge_deferred(lesson.get('deferred_work', []), review.get('deferred_work', []), evidence_gaps(frames, transcript))
            result = {'schema_version': VERSION, 'job_id': job_id, 'title': row['title'], 'course_id': row['course_id'], 'duration': duration,
                'source': {'sha256': row['source_sha256'], 'path': source.name,
                    'playback': {'video_src': 'playback.mp4', 'audio': {'src': 'speech.mp3', 'data_base64': base64.b64encode((d / 'speech.mp3').read_bytes()).decode(), 'mime': 'audio/mpeg'}},
                    'timing_note': 'ASR为20秒重叠窗口；帧sampled_time为请求采样时间，非精确PTS；MP3编码可能有毫秒级偏移。'},
                'sections': lesson['sections'], 'transcript': _safe_public(transcript), 'frames': frames,
                'formulas': lesson.get('formulas', []), 'uncertainties': lesson.get('uncertainties', []),
                'coverage': {'audio': transcript.get('coverage', {}), 'visual': {'sampled_frames': len(frames), 'max_frames': self.config['max_frames'],
                    'strategy': 'uniform timestamps plus scene-change candidates, bounded; not every frame', 'requested_interval_seconds': self.config['frame_interval'],
                    'sampled_times': [f['sampled_time'] for f in frames]}, 'semantic_completeness_verified': False},
                'provenance': {'pipeline_version': VERSION, 'run_identity': row['identity'], 'dependency_digest': _digest(row['config'].get('dependencies', {})), 'asr': _safe_public(transcript.get('identity', {})), 'ocr': _safe_public(recognized.get('identity', {})), 'models': models.identities(), 'originals_preserved': True},
                'review': review, 'corrections': lesson.get('corrections', []), 'reconciliation': lesson.get('reconciliation', {}),
                'deferred_work': deferred,
                'exports': {'json': 'result.json', 'markdown': 'lesson.md', 'original_json': 'result.json', 'original_markdown': 'lesson.md'},
                'quality_state': 'candidate', 'production_accepted': False}
            write_json_atomic(d / 'result.json', result)
            (d / 'lesson.md').write_text(self._markdown(row['title'], result, source.name), encoding='utf-8')
            write_json_atomic(d / 'transcript.json', _safe_public(transcript))
            write_json_atomic(d / 'frames.json', frames)
            self._register_artifacts(job_id, ['result.json', 'lesson.md', 'transcript.json', 'frames.json', 'review.json'])
            return {}, ['result.json', 'lesson.md', 'transcript.json', 'frames.json']
        self._stage(job_id, 'export', token, export)

    def _extract_frames(self, job_id, source, duration, token):
        d = self._jobdir(job_id); (d / 'frames').mkdir(exist_ok=True)
        maximum = self.config['max_frames']; regular_cap = max(2, maximum * 3 // 4)
        interval = max(self.config['frame_interval'], duration / (regular_cap - 1))
        regular = [min(i * interval, max(0, duration - .1)) for i in range(math.ceil(duration / interval))]
        regular.append(max(0, duration - .1))
        # Full-video downscaled scene scan records timestamps, never unlimited images.
        scene_log = d / 'scene.log'
        self._process(['ffmpeg', '-nostdin', '-hide_banner', '-i', source, '-an', '-vf',
            f"scale=320:-2,select='gt(scene,{self.config['scene_threshold']})',showinfo", '-fps_mode', 'vfr', '-f', 'null', '-'], scene_log, token)
        candidates = []
        with scene_log.open(errors='replace') as stream:
            for line in stream:
                match = re.search(r'pts_time:([0-9.]+)', line)
                if match:
                    t = float(match.group(1))
                    if 0 <= t < duration and all(abs(t - v) >= 3 for v in regular) and (not candidates or t - candidates[-1] >= 3):
                        candidates.append(t)
        room = max(0, maximum - len(regular))
        if len(candidates) > room and room:
            candidates = [candidates[int(i * len(candidates) / room)] for i in range(room)]
        elif not room:
            candidates = []
        times = sorted(set(regular + candidates))[:maximum]
        frames = []
        for index, t in enumerate(times):
            token.checkpoint(); frame_id = f'frame-{index + 1:04d}'; path = f'frames/{frame_id}.jpg'
            self._process(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{t:.3f}', '-i', source, '-frames:v', '1', '-vf', 'scale=1600:-2', '-q:v', '2', d / path], d / 'extract-frame.log', token, 60)
            if not (d / path).is_file():
                raise ValueError('Frame extraction produced no image')
            frames.append({'id': frame_id, 'path': path, 'start': t, 'end': min(duration, times[index + 1] if index + 1 < len(times) else duration),
                           'sampled_time': t, 'sampling': 'scene_change' if t in candidates else 'periodic', 'sha256': _sha(d / path)})
            self._progress(job_id, 'frames', (index + 1) / len(times))
        write_json_atomic(d / 'frames-manifest.json', {'frames': frames})
        self._register_artifacts(job_id, [f['path'] for f in frames])
        return frames, ['frames-manifest.json'] + [f['path'] for f in frames]
