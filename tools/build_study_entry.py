"""Write the three-action study homepage into an offline release root."""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit


TEMPLATE = Path(__file__).resolve().parent / 'release_templates' / '点这里开始阅读.html'
DESTINATIONS = ('教材/index.html?practice=full', '日常巩固.html', '押题.html')


def build_entry(root: str | Path, course_prefix: str = '复变函数A/') -> dict[str, str]:
    """Write index.html and 点这里开始阅读.html; leave all other release files intact.

    ``course_prefix`` is the URL folder path from the homepage to the course.
    It may be empty when the homepage is written inside the course directory.
    The destination pages are provided by the course delivery builder.
    """
    parsed = urlsplit(course_prefix)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '\\' in course_prefix:
        raise ValueError('course_prefix 必须是本地课程文件夹的 URL 路径')
    if any(ord(character) < 32 for character in course_prefix):
        raise ValueError('course_prefix 不能包含控制字符')
    prefix = course_prefix.rstrip('/') + '/' if course_prefix else ''
    page = TEMPLATE.read_text(encoding='utf-8')
    for destination in DESTINATIONS:
        original = 'href="复变函数A/' + destination + '"'
        if page.count(original) != 1:
            raise ValueError('学习入口模板缺少唯一的课程链接：' + destination)
        page = page.replace(original, 'href="' + escape(prefix + destination, quote=True) + '"')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    written = {}
    for name in ('index.html', '点这里开始阅读.html'):
        target = root / name
        target.write_text(page, encoding='utf-8')
        written[name] = str(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path, help='已生成的阅读包根目录')
    parser.add_argument('--course-prefix', default='复变函数A/', help='首页到课程文件夹的 URL 路径')
    args = parser.parse_args()
    print(json.dumps(build_entry(args.root, args.course_prefix), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
