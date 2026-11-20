#!/usr/bin/env python3
"""把交付包里的 `00-先看这个.md` 转成一个**能在浏览器里看**的 `00-先看这个.html`。

为什么要单独一个工具：md 在 Windows 上双击是记事本，排版全丢；负责人要看的是**打开就好看的**
那一版。转换只认我们用到的几种语法（标题/表格/列表/引用/加粗/行内代码），够用且可预期。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HEAD = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<title>先看这个 · AIforStudy 交付包</title><style>',
        'body{margin:0;background:linear-gradient(180deg,#f7fcff,#fdfaf7 45%,#f7fbf9);color:#26303a;',
        'font-family:-apple-system,"Noto Sans CJK SC","Microsoft YaHei",sans-serif;font-size:17px;line-height:1.9}',
        '.wrap{max-width:920px;margin:0 auto;padding:36px 22px 90px}',
        'h1{font-family:"Noto Serif CJK SC",Georgia,serif;font-size:30px;color:#1c4a6e;margin:0 0 14px}',
        'h2{font-family:"Noto Serif CJK SC",Georgia,serif;font-size:21px;color:#1c4a6e;margin:34px 0 10px;',
        'border-left:5px solid #e0834f;padding-left:12px}',
        'table{border-collapse:collapse;width:100%;font-size:15.5px;margin:10px 0 18px}',
        'th,td{border:1px solid #e4edf4;padding:8px 10px;text-align:left;vertical-align:top}',
        'th{background:#f4f9fd;color:#2b5f8a}',
        'code{background:#f4f9fd;border:1px solid #e4edf4;border-radius:6px;padding:1px 6px;font-size:.9em}',
        'strong{background:linear-gradient(transparent 62%,#ffe9c9 62%)}',
        'blockquote{margin:12px 0;padding:10px 16px;background:#fffaf5;border-left:4px solid #f0c09a;',
        'border-radius:0 12px 12px 0;color:#4a4038}',
        'li{margin:5px 0}</style></head><body><div class="wrap">']


def inline(text: str) -> str:
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', text)


def convert(md: str) -> str:
    body, in_table = [], False
    for line in md.split('\n'):
        st = line.strip()
        if not st:
            continue
        if st.startswith('|'):
            cells = [c.strip() for c in st.strip('|').split('|')]
            if all(set(c) <= set('-: ') for c in cells):
                continue
            tag = 'th' if not in_table else 'td'
            if not in_table:
                body.append('<table>')
                in_table = True
            body.append('<tr>' + ''.join(f'<{tag}>{inline(c)}</{tag}>' for c in cells) + '</tr>')
            continue
        if in_table:
            body.append('</table>')
            in_table = False
        if st.startswith('# '):
            body.append(f'<h1>{inline(st[2:])}</h1>')
        elif st.startswith('## '):
            body.append(f'<h2>{inline(st[3:])}</h2>')
        elif st.startswith('* '):
            body.append(f'<li>{inline(st[2:])}</li>')
        elif re.match(r'^\d+\. ', st):
            body.append(f'<li>{inline(st)}</li>')
        elif st.startswith('> '):
            body.append(f'<blockquote>{inline(st[2:])}</blockquote>')
        else:
            body.append(f'<p>{inline(st)}</p>')
    if in_table:
        body.append('</table>')
    return '\n'.join(HEAD + body + ['</div></body></html>'])


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    folder = Path(argv[0]) if argv else Path('.')
    md_path = folder / '00-先看这个.md'
    if not md_path.is_file():
        print(f'✗ 找不到 {md_path}', file=sys.stderr)
        return 2
    (folder / '00-先看这个.html').write_text(convert(md_path.read_text(encoding='utf-8')),
                                             encoding='utf-8')
    print(f'一页纸已生成：{folder / "00-先看这个.html"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
