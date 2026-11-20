"""Compose the existing annotation and administration services on separate sockets."""
from contextlib import ExitStack, contextmanager
from html import escape
from html.parser import HTMLParser
from importlib.resources import files
import re
from string import Template
from threading import Thread
from urllib.parse import quote

from ..community.book import BOOK, MEDIA, PRACTICE, _assignment
from ..community.recognition import LocalRecognizer
from ..community.server import CommunityHandler, CommunityServer
from ..core.workspace import open_workspace
from ..ui.server import ControlServer
from .config import check_link


PRACTICE_ASSETS = set("""animations.js audible-audio.js katex.min.css katex.min.js marked.min.js
practice-media.css practice-media.js practice-reader.js practice.css practice.js reader-markdown.js
render-core.js study-math.js practice-para23-15.svg""".split())
READER_ASSETS = PRACTICE_ASSETS | set("""app.js course-library.css course-library.js media-purpose.css
media-purpose.js media-surface.css models.js notes.css notes.js reader-book.css reader-layout.js
study-foundation-scenes.js study-guides.js study-lesson.js study-light.css study-reader.css
study-special-scenes.js study-text-led-figures.css study-text-led-figures.js study-visuals.js
styles.css text-led-multiplication.js textbook.pdf three.min.js video-evidence.js""".split())
EDITION = ('基础版','完整版','定制版')
WORKBOOKS = [f'{e}-{kind}.html' for e in EDITION for kind in ('习题集','习题题册')]
PRINTS = [f'可打印/{e}-{kind}.pdf' for e in EDITION for kind in ('习题解析','习题题册')]
PAGES = WORKBOOKS + ['动画目录.html','试卷包/index.html','题目来源对照.html'] + PRINTS

# These names are the public renderer outputs. Source records, receipts and caches
# in the very same directories have no route.
READER_MEDIA = re.compile(
    rb'"(assets/(?:source/[0-9a-f]{64}\.(?:pdf|jpg|png)|purpose-audio/[0-9a-f]{64}\.(?:mp3|wav)|'
    rb'textbook-tables/pdf-page-[0-9]+\.png|audible-video/[0-9a-f]{24}/(?:voice\.mp3|picture\.mp4)|'
    rb'narrated/CH\d+(?:-[A-Z]+\d+)*/MR-[0-9a-f]+/))"')
NARRATED_FILES = ('poster.png','narrated.mp4','narrated.wav','narrated.txt','narrated.vtt')


def reader_media_names(raw):
    paths = set()
    for match in READER_MEDIA.finditer(raw):
        name = match[1].decode('ascii')
        if name.endswith('/'):
            paths.update(name + filename for filename in NARRATED_FILES)
        else:
            paths.add(name)
    return paths

FONT = re.compile(r'assets/fonts/KaTeX_(?:AMS|Caligraphic|Fraktur|Main|Math|SansSerif|Script|Size[1-4]|Typewriter)'
                  r'-(?:Regular|Italic|Bold|BoldItalic)\.woff2')
PAPER = re.compile(r'(?:生成试卷/[\w-]+-(?:题卷|解析)\.html|往年原卷/[^./\\]+/[^./\\]+\.pdf)')
MEDIA_TYPES = {'svg':'.svg','mp4':'.mp4','picture':'.mp4','audio':'.mp3','poster':'.png',
               'captions':'.vtt','transcript':'.txt'}


PRACTICE_FILES = {'assets/'+s for s in PRACTICE_ASSETS}
READER_FILES = {'assets/'+s for s in READER_ASSETS}


def public_file(root, name):
    path = root / name
    return path if path.resolve() == path and path.is_file() else None


class PaperLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = set()

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get('href') or ''
        if tag == 'a' and PAPER.fullmatch(href):
            name = '试卷包/' + href
            self.names.add(name)
            if name.endswith('.html'):
                self.names.add(name[:-5] + '.pdf')


def delivery_names(server):
    practice = server.config.practice
    names = set(PAGES)
    if practice and (index := public_file(practice,'试卷包/index.html')):
        links = PaperLinks()
        links.feed(index.read_text(encoding='utf-8'))
        names.update(links.names)
    media = set()
    if MEDIA in server.snapshot.raw:
        for item in _assignment(server.snapshot.raw[MEDIA], 'PRACTICE_MEDIA'):
            base = item.get('base','')
            if not re.fullmatch(r'practice-media/[\w-]+/',base):
                continue
            for role, resource in item.get('resources',{}).items():
                filename = resource['relative_path']
                if (role in MEDIA_TYPES and re.fullmatch(r'[\w-]+\.[a-z0-9]+',filename)
                        and filename.endswith(MEDIA_TYPES[role])):
                    media.add('assets/' + base + filename)
    return names, media


def link(href, label):
    return f'<a href="{escape(href,quote=True)}">{escape(label)}</a>'


class LearningHandler(CommunityHandler):
    def _missing(self):
        return self._reply(404, {'error':'未找到学习资料。'})

    def _redirect(self, path, query=''):
        target = quote(path,safe='/') + ('?' + query if query else '')
        return self._bytes(302,b'', 'text/plain', headers={'Location':target})

    def _entry(self, title, links, footer=''):
        page = self.server.template.substitute(title=title,
            links=''.join(f'<li>{link(url,label)}</li>' for url,label in links), footer=footer)
        return self._bytes(200,page.encode(),'text/html; charset=utf-8')

    def reader_html(self, source):
        nav = '<nav aria-label="学习导航">' + link('/start','学习首页')
        if self.server.config.practice:
            nav += ' · ' + link('/practice/','练习目录')
        nav += '</nav>'
        aside = re.search(r'<aside\b[^>]*\bid="sidebar"[^>]*>',source)
        pos = aside.end() if aside else source.lower().find('</body>')
        if pos < 0:
            pos = len(source)
        return source[:pos] + nav + source[pos:]

    def _static(self, path, query):
        if any(s.startswith('.') or '\\' in s or '\x00' in s for s in path.split('/')) or '//' in path:
            return self._missing()
        practice = self.server.config.practice
        if path == '/start':
            links = [('/','阅读教材与共享感悟')]
            if practice:
                links.append(('/practice/','练习与试卷'))
            footer = link(self.server.workbench_url,'打开制作工作台') if self.server.workbench_url else ''
            return self._entry('学习首页',links,footer)
        if path in ('/', '/index.html') and not public_file(self.server.reader, 'index.html'):
            return self._missing()
        if path in ('/', '/index.html') or path.startswith('/community-assets/'):
            return super()._static(path, query)
        if practice:
            if path == '/practice':
                return self._redirect('/practice/',query)
            if path in ('/教材/index.html','/practice/教材/index.html','/practice/教材/'):
                return self._redirect('/',query)
            if path.startswith('/教材/assets/'):
                path = path.removeprefix('/教材')
            if path.startswith('/practice/教材/assets/'):
                path = path.removeprefix('/practice/教材')
            if path[1:] in self.server.practice_pages:
                return self._redirect('/practice'+path,query)
            if path in ('/practice/','/practice/index.html'):
                links = [('/practice/'+name, name.removesuffix('.html') if name != '试卷包/index.html' else '试卷目录')
                         for name in PAGES if public_file(practice,name)]
                return self._entry('练习与试卷',links,link('/','阅读教材') + link('/start','学习首页'))
            if path.startswith('/practice/'):
                name = path.removeprefix('/practice/')
                if name in (PRACTICE, MEDIA):
                    return super()._static('/'+name, query)
                if (name not in self.server.practice_pages and name not in self.server.public_media
                        and name not in PRACTICE_FILES
                        and name != 'assets/textbook-animation-data.js' and not FONT.fullmatch(name)):
                    return self._missing()
                target = public_file(practice,name)
                if not target:
                    return self._missing()
                if target.suffix == '.html':
                    nav = '<nav>'+link('/start','学习首页')+' · '+link('/practice/','练习目录')+'</nav>'
                    source = target.read_text(encoding='utf-8')
                    body = re.search(r'<body\b[^>]*>',source,re.I)
                    pos = body.end() if body else len(source)
                    return self._bytes(200,(source[:pos]+nav+source[pos:]).encode(),'text/html; charset=utf-8')
                return self._file(target)
        name = path.removeprefix('/')
        if name in (BOOK,PRACTICE,MEDIA):
            return super()._static(path,query)
        if not public_file(self.server.reader,name):
            return self._missing()
        if name in self.server.public_media or name in self.server.reader_media or FONT.fullmatch(name):
            return self._file(self.server.reader/name)
        if name in READER_FILES:
            return super()._static(path,query)
        return self._missing()


@contextmanager
def running(config):
    """Bind both origins before serving. Only shutdown loops which actually started."""
    with ExitStack() as stack:
        learning = CommunityServer(config.reader,config.database,config.port,practice=config.practice)
        stack.callback(learning.server_close)
        if config.ocr_python:
            learning.recognizer = LocalRecognizer(config.ocr_python,config.ocr_model)
        learning.config = config
        learning.RequestHandlerClass = LearningHandler
        learning.template = Template(files('aiforstudy').joinpath('resources/integrated/entry.html').read_text('utf-8'))
        learning.reader_media = reader_media_names(learning.snapshot.raw[BOOK])
        learning.practice_pages, learning.public_media = delivery_names(learning)
        learning.workbench_url = config.workbench_url
        servers = [learning]
        if config.workbench_workspace:
            control = ControlServer(open_workspace(config.workbench_workspace),
                                    port=config.workbench_port, recover_interrupted=False)
            workbench = control.make_server()
            stack.callback(workbench.server_close)
            control.mark_interrupted()
            servers.append(workbench)
            learning.workbench_url = control.origin
        if learning.workbench_url:
            check_link(learning.workbench_url,learning.server_address[1])
        for httpd in servers:
            thread = Thread(target=httpd.serve_forever,kwargs={'poll_interval':0.05},daemon=True)
            thread.start()
            stack.callback(thread.join)
            stack.callback(httpd.shutdown)
        yield learning
