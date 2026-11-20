"""Prepare the pinned complex-analysis local and browser-only releases offline."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO/'src'), str(REPO)]
from aiforstudy.community.book import BOOK, MEDIA, PRACTICE, _assignment
from aiforstudy.integrated.server import (delivery_names, reader_media_names, FONT, READER_FILES,
                                         PRACTICE_FILES)
from aiforstudy.practice.render import js_data, public_question

MANIFEST_HASH = '117692f391b82e073bb4309004238d7b9a0eec9bda0ea01bd78bb88ea27b0af0'
BOOK_HASH = '600035f51e4d85036c2998421e3b0814e96b128065a3b2ed6f91904b87ed4a60'
TEMPLATES = REPO/'tools/release_templates'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def inventory(path):
    result = {}
    for item in sorted(path.rglob('*')):
        if item.is_symlink():
            raise ValueError(f'意外的符号链接 {item}')
        if item.is_file():
            result[item.relative_to(path).as_posix()] = sha(item)
    return result


def verify_inventory(path, expected):
    if inventory(path) != expected:
        raise ValueError(f'目录校验失败 {path}')


def publish(staged, dest):
    if dest.exists():
        verify_inventory(dest, inventory(staged))
    else:
        staged.rename(dest)


def check_roots(inputs, outputs):
    for i, target in enumerate(outputs):
        if target.is_symlink():
            raise ValueError(f'输出不能为链接 {target}')
        for other in [*inputs, *outputs[:i]]:
            a,b = target.resolve(), other.resolve()
            if a.is_relative_to(b) or b.is_relative_to(a):
                raise ValueError(f'输入输出路径重叠 {a}, {b}')


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf8')


def copy(src, dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(src,dst)


def database_files(source):
    journal = Path(str(source)+'-journal')
    if journal.is_file() and journal.stat().st_size:
        raise ValueError('批注数据库有回滚日志，稍后重试')
    return [p for p in (source,Path(str(source)+'-wal')) if p.is_file()]


def snapshot(source, dest, evidence=None):
    # Even SQLite mode=ro may write shared-memory reader marks. Work on a stable
    # private main/WAL pair. SQLite regenerates its WAL index from committed frames.
    if not source.is_file():
        raise ValueError(f'缺少数据库 {source}')
    with TemporaryDirectory(dir=dest.parent) as tmp:
        private = Path(tmp)/'input'
        inputs = database_files(source)
        before = {str(p):sha(p) for p in inputs}
        for path in inputs:
            copied = Path(str(private)+str(path)[len(str(source)):])
            shutil.copyfile(path,copied)
            if sha(copied) != before[str(path)]:
                raise ValueError('复制期间批注数据发生变化，请重试')
        if before != {str(p):sha(p) for p in database_files(source)}:
            raise ValueError('复制期间批注数据发生变化，请重试')
        if evidence is not None:
            evidence.update(before)
        return backup_private(private,dest)


def backup_private(private, dest):
    dest.touch(mode=0o600,exist_ok=False)
    with closing(sqlite3.connect(private)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)
        if dst.execute('pragma integrity_check').fetchone() != ('ok',):
            raise ValueError('批注快照损坏')
        tables = dst.execute("select name from sqlite_master where type='table'").fetchall()
        return {name:dst.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name, in tables}


def snapshot_notes(active, alternate, dest, evidence=None):
    dest.mkdir(parents=True)
    counts = {'A':snapshot(active, dest/'A.sqlite3',evidence)}
    if 'annotations' not in counts['A'] or 'annotation_images' not in counts['A']:
        raise ValueError('A 批注库格式不兼容')
    if alternate:
        counts['B'] = snapshot(alternate, dest/'B.sqlite3',evidence)
        if counts['B'].get('notes') != 0:
            raise ValueError('拒绝自动迁移 B，须先验证作者身份和原文映射')
    copy(dest/'A.sqlite3',dest/'notes.sqlite3')
    return counts


def discard(obj, names):
    for key in names.split():
        obj.pop(key,None)


def strip_record_fields(value, names):
    if isinstance(value, dict):
        for key in names:
            value.pop(key,None)
        for child in value.values():
            strip_record_fields(child,names)
    elif isinstance(value,list):
        for child in value:
            strip_record_fields(child,names)


# The reader consumes authored descriptions, displayed captions and the final media
# clock. Original production clocks, script revisions and direction records belong to the source.
PUBLIC_STEP_FIELDS = frozenset((
    'step_id', 'display_title', 'title', 'label', 'display_markdown', 'caption_tex', 'caption',
    'text', 'observation', 'expected_change', 'narration_text', 'narration_duration',
    'start', 'end', 'timeline', 'keyframes', 'checkpoints',
))


def project_book(book):
    strip_record_fields(book, ('review_outcome','source_review_outcome','production_accepted'))
    discard(book,'manual_delivery study_global_support_corrections study_global_display_corrections study_material_display_corrections')
    for unit in book['units'].values():
        lesson = unit.get('study_lesson',{})
        discard(lesson,'source_path')
        discard(lesson.get('source_metadata',{}),'source_path')
        discard(unit,'automatic_processing diagnostic_problems study_provenance study_support_corrections study_display_corrections study_textbook_format_corrections study_recovery')
    strip_record_fields(book['course_library'],('evidence_png','evidence_pngs'))
    if not book.get('unavailable_units'):
        discard(book,'problem_summary')
    for rows in book['animations'].values():
        for item in rows:
            discard(item,'pixel_correction technical_checks technical_report narration verification')
            item['steps'] = [{key:value for key,value in step.items() if key in PUBLIC_STEP_FIELDS}
                             for step in item['steps']]
    purposes = book['media_purposes']
    discard(purposes,'audio_build')
    for item in purposes['items']:
        discard(item['source_basis'],'lesson_path plan_sha256')
        discard(item['audio'],'receipt_src receipt_sha256 cache_key source_identity content_review')
    discard(book['course_library'],'input_manifests')
    for item in book['course_library']['sources']:
        discard(item,'local_paths')
    return book


def verified_input(practice, reader):
    manifest = practice/'文件校验清单.json'
    if sha(manifest) != MANIFEST_HASH or sha(reader/BOOK) != BOOK_HASH or sha(practice/'教材'/BOOK) != BOOK_HASH:
        raise ValueError('输入版本不匹配')
    rows = json.loads(manifest.read_text())['files']
    for row in rows:
        path = practice/row['path']
        if path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            raise ValueError(f'输入文件校验失败 {path}')
    return {row['path']:row['sha256'] for row in rows}


def public_materials(reader, practice, rebuilt, dest, hashes):
    rb = rebuilt/'教材'
    raw = (rb/BOOK).read_bytes()
    if hashlib.sha256(raw).hexdigest() != BOOK_HASH:
        raise ValueError('教材发生变化')
    media = (rb/MEDIA).read_bytes()
    server = SimpleNamespace(config=SimpleNamespace(practice=rebuilt), snapshot=SimpleNamespace(raw={MEDIA:media}))
    pages, media_files = delivery_names(server)
    runtime = dest/'教材'
    media_paths = reader_media_names(raw)
    book_names = READER_FILES | media_paths | media_files | {PRACTICE, MEDIA, 'index.html'}
    extra = {MEDIA, 'assets/textbook-animation-data.js'}
    for source, target, names in ((rb,runtime,book_names),(rebuilt,dest,pages|media_files|PRACTICE_FILES|extra)):
        fonts = [str(f.relative_to(source)) for f in (source/'assets/fonts').glob('*') if FONT.fullmatch(str(f.relative_to(source)))]
        for name in sorted(set(fonts)|names):
            src = source/name
            if not src.is_file() and name in pages:
                src = practice/name
            if not src.is_file():
                continue
            rel = (target/name).relative_to(dest).as_posix()
            if src.suffix in {'.mp3','.mp4','.wav','.vtt','.txt','.png','.pdf','.svg','.jpg','.woff2'} and sha(src) != hashes.get(rel):
                raise ValueError(f'学习资源发生变化 {rel}')
            if target == runtime and name in media_paths and sha(src) != sha(reader/name):
                raise ValueError(f'有声教材资源不匹配 {name}')
            copy(src,target/name)
    if not all((runtime/p).is_file() for p in media_paths|media_files):
        raise ValueError('教材缺少播放资源')
    for script in ('reader-markdown.js','study-lesson.js','notes.js','marked.min.js'):
        layer = 'vendor' if script == 'marked.min.js' else 'assets'
        copy(REPO/'blocks/render'/layer/script,runtime/'assets'/script)
    for css in dest.rglob('katex.min.css'):
        css.write_text(re.sub(r',url\(fonts/[^)]+\.(?:woff|ttf)\)\s*format\([^)]*\)', '', css.read_text()))
    original = _assignment((practice/'教材'/PRACTICE).read_bytes(),'PRACTICE_EDITIONS')
    expected = {k:[public_question(q) for q in rows] for k,rows in original.items()}
    if expected != _assignment((runtime/PRACTICE).read_bytes(),'PRACTICE_EDITIONS'):
        raise ValueError('公开习题与正式版不符')
    book = project_book(_assignment(raw,'__DATA__'))
    (runtime/BOOK).parent.mkdir()
    (runtime/BOOK).write_text('window.__DATA__='+js_data(book)+';\n')
    metadata = {'book':book['book'],'version':sha(runtime/BOOK)}
    index = runtime/'index.html'
    index.write_text(index.read_text().replace('<head>', '<head><script>window.__BOOK_VERSION__='+js_data(metadata)+';</script>',1))
    for page in [runtime/BOOK,*dest.rglob('*.html'),runtime/PRACTICE]:
        if b'/Users/' in page.read_bytes():
            raise ValueError(f'公开文件含本机路径 {page}')
    copy(TEMPLATES/'教材与练习.html',dest/'index.html')
    return {'units':len(book['units']), 'questions':{k:len(v) for k,v in expected.items()},'reader_revision':metadata['version']}


def source_snapshot(dest):
    status = subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=REPO)
    if status:
        raise ValueError('请先提交源代码修改')
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
    roots = ['src','tools','blocks','lib','figures','config','THIRD_PARTY_NOTICES','docs/integrated-study.md',
             'README.md','aifs','setup.py','pyproject.toml','MANIFEST.in','package.json','package-lock.json',
             'requirements*.txt','requirements*.lock','vendor.lock.json']
    archive = dest/'source.zip'
    subprocess.run(['git','archive','--format=zip','-o',archive,commit,'--',*roots],cwd=REPO,check=True)
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            if entry.is_dir():
                continue
            path = Path(z.extract(entry,dest/'source'))
            path.chmod(0o755 if (entry.external_attr >> 16) & 0o111 else 0o644)
    return commit


ENV_PATHS = set('AIFS_BROWSER_NODE AIFS_BROWSER_EXECUTABLE AIFS_PLAYWRIGHT_MODULE AIFORSTUDY_MANIM_PYTHON AIFORSTUDY_VIDEO_ASR_PYTHON AIFORSTUDY_VIDEO_ASR_MODEL AIFORSTUDY_VIDEO_OCR_PYTHON AIFORSTUDY_TTS_PYTHON AIFORSTUDY_TTS_MODEL_DIR AIFORSTUDY_TTS_MODEL_MANIFEST'.split())


def prepare(args):
    inputs = [args.reader,args.practice,args.database,args.runtime,REPO]
    if args.alternate_database:
        inputs.append(args.alternate_database)
    output,student = args.output.absolute(),args.student.absolute()
    check_roots(inputs,[output,student])
    host = json.loads(args.runtime.read_text())
    paths = host['environment']
    if set(host) - {'environment','ocr_model','ocr_python'} or set(paths)-ENV_PATHS:
        raise ValueError('运行环境只接受指定的本地工具路径')
    for value in [*paths.values(),host['ocr_model'],host['ocr_python']]:
        if not Path(value).is_absolute() or not Path(value).exists():
            raise ValueError(f'本地工具路径不可用 {value}')
    hashes = verified_input(args.practice,args.reader)
    for dst in (output,student):
        dst.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp, TemporaryDirectory(dir=student.parent) as tmp_student:
        stage, rebuilt = Path(tmp)/'release',Path(tmp)/'rebuilt'
        stage.mkdir()
        stu = Path(tmp_student)/'student'
        commit = source_snapshot(stage)
        subprocess.run([sys.executable,stage/'source/tools/rebuild_practice_delivery.py','--delivery',args.practice,
                        '--out',rebuilt],check=True,env={**os.environ,'PYTHONPATH':str(stage/'source/src')}, stdout=subprocess.DEVNULL)
        content = stage/'复变函数A'
        info = public_materials(args.reader,args.practice,rebuilt,content,hashes)
        input_db_hashes = {}
        counts = snapshot_notes(args.database,args.alternate_database,stage/'批注',input_db_hashes)
        shutil.copytree(content,stu/'复变函数A')
        for name in ('README.txt','点这里开始阅读.html','index.html'):
            copy(TEMPLATES/name,stu/name)
        registry = stage/'制作工作区/config'
        write(registry/'books.json',{'workspace':'.','books':{}})
        for name in ('execution','models','capabilities','narration','review_mode','teaching_profiles'):
            copy(stage/f'source/config/{name}.json',registry/f'{name}.json')
        write(stage/'config.json',dict(reader='复变函数A/教材',practice='复变函数A',database='批注/notes.sqlite3',
             port=8792,workbench_workspace='制作工作区',workbench_port=18917,
             ocr_model=host['ocr_model'],ocr_python=host['ocr_python']))
        write(stage/'runtime.json', {'environment':paths,'python':sys.executable})
        launch = stage/'启动整合版.command'
        launch.write_text('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\nexec '+shlex.quote(sys.executable)+' source/tools/launch_integrated_release.py "$@"\n')
        launch.chmod(0o755)
        copy(TEMPLATES/'使用说明.md',stage/'使用说明.md')
        write(stage/'input-files.json',hashes)
        write(stage/'release.json',dict(source_commit=commit, source_archive=sha(stage/'source.zip'),
             base_commits={'active':'1274001','practice':'7b4eba4','video':'fd366e1','media':'d46b2a7','alternate_reference':'c6c2412'},
             original_practice_manifest=MANIFEST_HASH,original_voiced_reader=BOOK_HASH,
             alternate_silent_reader='886d93c525105ef883f350fbecd22fdcc9c6a1823898fc79ac80b997ff2b6cff',
             input_paths=[str(p.absolute()) for p in inputs],notes=counts, database_input_sha256=input_db_hashes, **info))
        write(stage/'student-sha256.json',inventory(stu))
        write(stage/'sha256.json',inventory(stage))
        # Serialize check-and-publish between builders sharing the release parent.
        lock = os.open(output.parent,os.O_RDONLY)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX)
            for source,target in ((stage,output),(stu,student)):
                if target.exists():
                    verify_inventory(target,inventory(source))
            publish(stage,output)
            publish(stu,student)
        finally:
            os.close(lock)
    return {'release':str(output),'student':str(student),'source_commit':commit, **info}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('reader','practice','database','alternate-database','runtime','output','student'):
        parser.add_argument('--'+key,type=Path,required=key!='alternate-database')
    print(json.dumps(prepare(parser.parse_args()),ensure_ascii=False))


if __name__ == '__main__':
    main()
