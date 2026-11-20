"""Optional offline OCR in one bounded, disposable local worker process."""
from __future__ import annotations

import hashlib,json,os,signal,subprocess,tempfile,threading
from pathlib import Path
from .store import StoreError


class LocalRecognizer:
    def __init__(self, python: Path, model: Path, *, device: str = 'auto', timeout: float = 90):
        # Keep the declared interpreter path: resolving a venv symlink loses its packages.
        self.python, self.model = Path(os.path.abspath(Path(python).expanduser())), Path(model).expanduser().resolve()
        if not self.python.is_file() or not os.access(self.python,os.X_OK) or not (self.model/'config.json').is_file():
            raise ValueError('本地识别需要可执行的独立 Python 和已下载的模型目录。')
        if device not in ('auto','cpu','mps','cuda') or not 0 < timeout <= 90:
            raise ValueError('识别运行参数无效。')
        self.device,self.timeout=device,float(timeout)
        if not hasattr(os, 'killpg'): raise ValueError('此本地识别后端需要 macOS 或 Linux 进程管理。')
        self._slot=threading.Lock()
        self._process_lock=threading.Lock()
        self._process=None
        self._closed=threading.Event()

    @property
    def engine(self):
        manifest=[(p.name,p.stat().st_size,p.stat().st_mtime_ns) for p in sorted(self.model.glob('*.safetensors'))]
        digest=hashlib.sha256((self.model/'config.json').read_bytes()+json.dumps(manifest).encode()).hexdigest()[:16]
        return self.model.name+':'+digest

    def recognize(self, data: bytes, mime: str) -> dict:
        if self._closed.is_set(): raise StoreError(503, '识别服务正在停止。')
        if not self._slot.acquire(blocking=False):
            raise StoreError(429,'本地识别正在处理另一张图片，请稍后重试，也可以直接发图片。')
        process=None
        try:
            with tempfile.TemporaryDirectory(prefix='aifs-image-ocr-') as folder:
                root=Path(folder);image=root/('image'+{'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}[mime]);result=root/'result.json'
                image.write_bytes(data)
                (root/'.aifs-ocr-owner').write_text(str(os.getpid()))
                env={key:os.environ[key] for key in ('PATH','LANG','LC_ALL','TMPDIR') if key in os.environ}
                env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false',PYTHONDONTWRITEBYTECODE='1')
                command=[str(self.python),str(Path(__file__).with_name('ocr_worker.py')),'--model',str(self.model),'--image',str(image),'--output',str(result),'--device',self.device]
                guarded=[str(self.python),str(Path(__file__).with_name('process_guard.py')),'--parent-pid',str(os.getpid()),'--timeout',str(self.timeout),'--cleanup-dir',str(root),'--',*command]
                with self._process_lock:
                    if self._closed.is_set(): raise StoreError(503, '识别服务正在停止。')
                    process=subprocess.Popen(guarded,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,start_new_session=True)
                    self._process=process
                try: process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired as exc:
                    self._signal(process,signal.SIGKILL);process.wait(timeout=3)
                    raise StoreError(504,'识别超时，可以直接按图片发布或换一张更清晰的小图。') from exc
                if process.returncode == 124: raise StoreError(504, '识别超时，可以直接按图片发布。')
                if process.returncode or not result.is_file() or result.stat().st_size>128_000:
                    raise StoreError(503,'本地识别暂时失败，图片仍可正常发布。')
                value=json.loads(result.read_text(encoding='utf-8'))
                if not isinstance(value,dict) or value.get('error'):
                    raise StoreError(503,'本地识别暂时不可用，图片仍可正常发布。')
                return {'text':value.get('text'),'truncated':value.get('truncated'),'engine':self.engine}
        except (OSError,ValueError,KeyError) as exc:
            raise StoreError(503,'本地识别暂时失败，图片仍可正常发布。') from exc
        finally:
            if process is not None:
                self._signal(process,signal.SIGKILL)
                if process.poll() is None: process.wait(timeout=3)
            with self._process_lock:
                if self._process is process:self._process=None
            self._slot.release()

    @staticmethod
    def _signal(process, signum):
        try:os.killpg(process.pid,signum)
        except ProcessLookupError:pass

    def close(self):
        self._closed.set()
        with self._process_lock:process=self._process
        if process is not None:
            self._signal(process,signal.SIGTERM)
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._signal(process,signal.SIGKILL);process.wait(timeout=3)
        # Let the request thread remove its temporary photo before service exit.
        if self._slot.acquire(timeout=5):self._slot.release()
