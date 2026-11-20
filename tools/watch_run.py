#!/usr/bin/env python3
"""真跑实时监视：每 30 秒把当前进度打到 stdout（谁在跑、跑到哪、有没有坏消息）。

用法：python3 tools/watch_run.py <run_dir> [轮询秒数]
只读：读 state.json / events.jsonl，不碰运行中的任何产物。
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PREP = {'mineru', 'toc', 'structure', 'export', 'body', 'concepts'}
BAD_WORDS = ('blocked', 'failed', 'ModelCallFailed', 'exceeds capacity', 'bad-json',
             '内容来源已变化', 'local correction limit')


def snapshot(run: Path) -> dict:
    state = json.loads((run / 'state.json').read_text())
    nodes = state.get('nodes', {})
    events = []
    path = run / 'events.jsonl'
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                events.append(json.loads(line))
    return {'status': state.get('status'), 'nodes': nodes, 'events': events}


def line(run: Path, previous_bad: int) -> tuple:
    """打印一行进度，返回 (本行文本, 新的坏消息条数)。"""
    try:
        snap = snapshot(run)
    except (OSError, ValueError) as exc:
        return f'{run.name} 状态文件暂时不可读（{type(exc).__name__}）', previous_bad
    nodes = snap['nodes']
    done = {k: v for k, v in nodes.items() if v.get('execution_state') not in (None, 'running', 'pending')}
    running = [k for k, v in nodes.items() if v.get('execution_state') == 'running']
    bad = {k: v for k, v in nodes.items()
           if v.get('execution_state') in ('blocked', 'failed')}
    stamp = datetime.now().strftime('%H:%M:%S')
    head = (f'{stamp}  {run.name[-6:]}  status={snap["status"]:<12} '
            f'节点 {len(done)}/{len(nodes)}  运行中={running or "无"}  阻塞={len(bad)}')
    out = [head]
    for key in sorted(bad):
        out.append(f'    ✗ {key}: {str(bad[key].get("error"))[:150]}')
    fresh = [e for e in snap['events']
             if any(w in (e.get('message') or '') for w in BAD_WORDS)]
    for event in fresh[previous_bad:]:
        out.append(f'    ⚠ {event.get("kind")} {event.get("node_id")}: '
                   f'{(event.get("message") or "")[:150]}')
    return '\n'.join(out), len(fresh)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run = Path(sys.argv[1]).resolve()
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    seen = 0
    while True:
        text, seen = line(run, seen)
        print(text, flush=True)
        try:
            status = json.loads((run / 'state.json').read_text()).get('status')
        except (OSError, ValueError):
            status = None
        if status and status != 'running':
            print(f'{datetime.now().strftime("%H:%M:%S")}  === 运行结束：{status} ===', flush=True)
            return 0
        time.sleep(interval)


if __name__ == '__main__':
    raise SystemExit(main())
