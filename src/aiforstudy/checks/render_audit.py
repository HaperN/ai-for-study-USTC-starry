"""修改计划 R4：最终交付产物渲染检验——只读，只出一份定位报告，不改界面、不阻止交付。

`aifs check-render <交付目录>` 找出目录下每个阅读入口（含 data/data.js 的 index.html），
用本地 Chromium 逐单元扫描，再逐个扫描 WebVTT 字幕文件，最后写 JSON 和 Markdown 报告。
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from ..errors import CapabilityError, InputError

KIND_LABELS = {
    'katex_error': '公式渲染报错',
    'raw_dollar': '可见文字残留 $',
    'raw_command': '可见文字残留 \\命令',
    'raw_script': '可见文字残留 ^{ 或 _{',
    'empty_body': '正文为空或不可见',
    'broken_image': '图片加载失败',
    'animation_missing': '动画缺件',
    'media_missing': '媒体缺失提示',
    'unit_not_rendered': '单元没有渲染出来',
    'vtt_unescaped_angle': '字幕文件含未转义的 <（原生字幕会截断）',
    'vtt_raw_tex': '字幕文件含原始 TeX（只在原生字幕兜底时可见）',
    'vtt_invalid': '字幕文件格式无效',
}
_ARROW = re.compile(r'^\d{2,}:\d{2}:\d{2}\.\d{3}\s+-->')


def find_entries(root: Path) -> list[dict]:
    entries = []
    for index in sorted(Path(root).rglob('index.html')):
        if (index.parent / 'data' / 'data.js').is_file():
            entries.append({'name': index.parent.relative_to(root).as_posix() or '.', 'index': str(index)})
    return entries


def scan_vtt(root: Path) -> list[dict]:
    """逐个 cue 检查转义与残留 TeX；按文件去重（同一视频的多份副本只报一次）。"""
    from .offline import parse_webvtt_cues
    issues, seen = [], set()
    for path in sorted(Path(root).rglob('*.vtt')):
        raw = path.read_bytes()
        if raw in seen:
            continue
        seen.add(raw)
        text = raw.decode('utf-8', errors='replace')
        try:
            parse_webvtt_cues(text)
        except ValueError as exc:
            issues.append({'kind': 'vtt_invalid', 'file': path.relative_to(root).as_posix(), 'snippet': str(exc)})
            continue
        lines = text.replace('\r\n', '\n').split('\n')
        for number, line in enumerate(lines):
            if not line or line == 'WEBVTT' or _ARROW.match(line) or (number + 1 < len(lines) and _ARROW.match(lines[number + 1])):
                continue
            where = {'file': path.relative_to(root).as_posix(), 'line': number + 1, 'snippet': line[:160]}
            if '<' in line:
                issues.append({'kind': 'vtt_unescaped_angle', **where})
            if '$' in line or re.search(r'\\[A-Za-z]{2,}|[_^]\{', line):
                issues.append({'kind': 'vtt_raw_tex', **where})
    return issues


def _summary(report: dict) -> dict:
    counts = Counter()
    for entry in report['entries']:
        for unit in entry.get('units', []):
            counts.update(issue['kind'] for issue in unit.get('issues', []))
    counts.update(issue['kind'] for issue in report['vtt'])
    failed_entries = [e['name'] for e in report['entries'] if e.get('ready') != 'ok' or e.get('error')]
    return {'issue_counts': dict(sorted(counts.items())), 'entries': len(report['entries']),
            'entries_not_ready': failed_entries,
            'units_checked': sum(len(e.get('units', [])) for e in report['entries']),
            'units_with_issues': sum(1 for e in report['entries'] for u in e.get('units', []) if u.get('issues'))}


def _markdown(report: dict) -> str:
    s = report['summary']
    lines = ['# 最终产物渲染检验报告', '',
             f"检验目录：`{report['root']}`  ", f"时间：{report['checked_at']}  ",
             '本报告只定位问题，不改动交付物、不影响页面显示；“通过”只代表本程序检查的这几类显示问题未发现，'
             '不代表数学内容、动画全程或音画同步已验收。', '',
             f"入口 {s['entries']} 个；单元检查 {s['units_checked']} 次，其中 {s['units_with_issues']} 次有问题。", '']
    if s['entries_not_ready']:
        lines += ['**未就绪的入口：** ' + '、'.join(s['entries_not_ready']), '']
    lines += ['## 问题统计', '', '| 类别 | 数量 |', '| --- | --- |']
    lines += [f"| {KIND_LABELS.get(kind, kind)} | {count} |" for kind, count in s['issue_counts'].items()] or ['| （无） | 0 |']
    for entry in report['entries']:
        lines += ['', f"## 入口 {entry['name']}", '',
                  f"就绪：{entry.get('ready')}（{entry.get('ready_seconds', 0):.1f} 秒）；单元 {entry.get('declared_units', 0)} 个"
                  + ('；旧版页面，无就绪信号，按占位文字消失判断' if entry.get('legacy_ready_signal') else '')]
        if entry.get('error'):
            lines.append(f"- **{entry['error']}**")
        for err in entry.get('script_errors', [])[:20]:
            lines.append(f"- 脚本错误：`{err}`")
        for unit in entry.get('units', []):
            for issue in unit.get('issues', []):
                place = ' / '.join(x for x in (unit['unit_id'], issue.get('pointer', ''), issue.get('media', '')) if x)
                lines.append(f"- {KIND_LABELS.get(issue['kind'], issue['kind'])}｜{place}｜`{issue.get('snippet', '')}`")
    if report['vtt']:
        lines += ['', '## 字幕文件', '']
        lines += [f"- {KIND_LABELS.get(i['kind'], i['kind'])}｜{i['file']}:{i.get('line', '')}｜`{i['snippet']}`"
                  for i in report['vtt']]
    return '\n'.join(lines) + '\n'


def audit_delivery(root, *, out_dir, workspace_root=None, ready_timeout=180.0, unit_timeout=30.0) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise InputError(f'交付目录不存在：{root}')
    entries = find_entries(root)
    if not entries:
        raise InputError(f'{root} 下没有找到带 data/data.js 的阅读入口')
    from ..blocks.media.validation import display_runtime
    browser, node, playwright = display_runtime(workspace_root)
    with tempfile.TemporaryDirectory(prefix='aifs-render-audit-', dir='/tmp') as scratch:
        job = Path(scratch) / 'job.json'
        job.write_text(json.dumps({'entries': entries, 'ready_timeout_ms': int(ready_timeout * 1000),
                                   'unit_timeout_ms': int(unit_timeout * 1000)}), encoding='utf-8')
        proc = subprocess.run([str(node), str(Path(__file__).with_name('render_audit.cjs')), str(playwright),
                               str(browser), str(job)], capture_output=True, text=True,
                              env={'PATH': '/usr/bin:/bin', 'HOME': scratch, 'TMPDIR': scratch})
    if proc.returncode:
        raise CapabilityError('渲染检验浏览器运行失败：' + proc.stderr[-2000:])
    report = {'schema_version': 'RenderAuditReportV1', 'root': str(root),
              'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'), **json.loads(proc.stdout)}
    report['vtt'] = scan_vtt(root)
    report['summary'] = _summary(report)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'render-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'render-check.md').write_text(_markdown(report), encoding='utf-8')
    return report


__all__ = ['audit_delivery', 'find_entries', 'scan_vtt', 'KIND_LABELS']
