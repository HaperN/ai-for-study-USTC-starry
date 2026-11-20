"""Read-only validation for the integrated loopback services."""
from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from ..community.book import BookSnapshot
from ..community.images import image_capabilities
from ..core.workspace import open_workspace


@dataclass(frozen=True)
class Config:
    reader: Path
    database: Path
    practice: Path | None = None
    port: int = 8792
    workbench_workspace: Path | None = None
    workbench_port: int = 18917
    workbench_url: str | None = None
    ocr_python: Path | None = None
    ocr_model: Path | None = None


def check_link(url, reader_port):
    if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
        raise ValueError('工作台链接需要本机地址。')
    parts = urlsplit(url)
    if (parts.scheme not in ('https', 'http') or parts.hostname not in ('127.0.0.1','localhost','::1')
            or parts.username is not None or parts.path not in ('','/') or '?' in url or '#' in url):
        raise ValueError('工作台链接仅接受本机 HTTP(S) 根地址。')
    port = parts.port if parts.port is not None else (80 if parts.scheme == 'http' else 443)
    if not port or (parts.scheme == 'http' and port == reader_port):
        raise ValueError('工作台需要单独的本机地址。')


def load_config(filename):
    file = Path(filename).expanduser().resolve()
    values = json.loads(file.read_text(encoding='utf-8'))
    if not isinstance(values, dict) or set(values) - {f.name for f in fields(Config)}:
        raise ValueError('配置含有未知字段。')
    if not {'reader', 'database'} <= values.keys():
        raise ValueError('请配置教材目录和批注数据库。')
    if 'workbench_workspace' not in values and 'workbench_port' in values:
        raise ValueError('工作台端口需要同时配置工作区。')
    if 'workbench_url' in values and 'workbench_workspace' in values:
        raise ValueError('请选择一个工作台入口。')
    if ('ocr_python' in values) != ('ocr_model' in values):
        raise ValueError('本地识别需要同时配置 Python 和模型目录。')
    for name in ('reader','database','practice','workbench_workspace','ocr_python','ocr_model'):
        if name in values:
            if not isinstance(values[name],str) or not values[name].strip():
                raise ValueError(f'{name} 需要有效路径。')
            path = file.parent / Path(values[name]).expanduser()
            if name in ('reader', 'practice') and any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('教材与练习目录须使用实际目录路径，不能经过符号链接。')
            if name in ('reader', 'practice', 'ocr_python'):
                values[name] = Path(os.path.abspath(path))
            else:
                values[name] = path.resolve()
    cfg = Config(**values)
    if any(type(p) is not int or not 0 <= p <= 65535 for p in (cfg.port,cfg.workbench_port)):
        raise ValueError('端口必须为 0–65535 的整数。')
    if 'workbench_url' in values:
        check_link(cfg.workbench_url, cfg.port)
    roots = [cfg.reader] + ([cfg.practice] if cfg.practice else [])
    if any(not (root/'index.html').is_file() for root in roots):
        raise ValueError('教材或练习目录缺少首页。')
    if cfg.database.is_dir() or any(cfg.database.is_relative_to(root) for root in roots):
        raise ValueError('批注数据库必须独立于教材和练习目录。')
    parent = next(p for p in cfg.database.parents if p.exists())
    if not parent.is_dir():
        raise ValueError('数据库的上级路径必须是目录。')
    if cfg.ocr_python and (not cfg.ocr_python.is_file() or not os.access(cfg.ocr_python,os.X_OK)
                           or not (cfg.ocr_model/'config.json').is_file()):
        raise ValueError('请检查本地识别的解释器和模型目录。')
    if cfg.workbench_workspace:
        if cfg.port and cfg.port == cfg.workbench_port:
            raise ValueError('工作台与教材端口须不同。')
        open_workspace(cfg.workbench_workspace)
    return cfg


def check(config):
    snapshot = BookSnapshot(config.reader, config.practice)
    report = {}
    for field in fields(config):
        value = getattr(config, field.name)
        if value is not None and (field.name != 'workbench_port' or config.workbench_workspace):
            report[field.name] = str(value) if isinstance(value, Path) else value
    return {**report,
            'node':str(Path(shutil.which(os.environ.get('AIFS_BROWSER_NODE') or 'node')).resolve()),
            'book':snapshot.context['book'], 'revision':snapshot.context['revision'],
            'practice_revision':snapshot.context.get('practice_revision'),
            'units':len(snapshot.context['units']), 'ocr_enabled':bool(config.ocr_python),
            'image_upload_available':image_capabilities()['available']}
