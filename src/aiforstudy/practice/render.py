"""Portable HTML projection of the canonical catalog; no model work in rendering."""
from pathlib import Path
from copy import deepcopy
import html,json,re,shutil

RESOURCE=Path(__file__).resolve().parents[1]/'resources/practice'

def js_data(data):
 return json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')

PUBLIC_QUESTION_FIELDS = (
 'id', 'title', 'stem', 'topic', 'unit_id', 'priority', 'difficulty', 'points',
 'answer', 'solution', 'necessary_conditions', 'methods', 'pitfalls', 'priority_reason',
 'public_source_path', 'public_textbook_path', 'section', 'anchor_text',
 'mount_unit_id', 'subquestion_count',
)
PUBLIC_SOURCE_FIELDS = ('kind', 'course', 'year', 'pdf_page', 'question_number', 'common_header', 'public_path')

PUBLIC_PLACEMENT_FIELDS = ('unit_id', 'quote', 'paragraph_sha256', 'method')

def _public_placement(value):
 if not isinstance(value, dict):
  return None
 placement = {key: value.get(key) for key in PUBLIC_PLACEMENT_FIELDS}
 if not all(isinstance(part, str) and part.strip() for part in placement.values()):
  return None
 if not re.fullmatch(r'[0-9a-f]{64}', placement['paragraph_sha256']):
  return None
 return placement

def public_question(question: dict) -> dict:
 """Project a reviewed record into student-visible learning and source fields."""
 source = question.get('source', {})
 public = {
  **{key: question[key] for key in PUBLIC_QUESTION_FIELDS if key in question},
  'source': {key: source[key] for key in PUBLIC_SOURCE_FIELDS if key in source},
 }
 placement = _public_placement(question.get('placement'))
 if placement is not None:
  public['placement'] = placement
 return deepcopy(public)

def build_page(title, questions, *, question_only=False, paper=None, description='', assets='assets', home='index.html'):
 rows=[public_question(q) for q in questions]
 if question_only:
  fields=('id','stem','points','section') if paper else ('id','title','stem','priority','difficulty','topic','public_source_path')
  source_fields=('common_header',) if paper else ('kind','course','year','pdf_page','question_number','common_header')
  rows=[{**{k:q[k] for k in fields if k in q},'source':{k:q.get('source',{})[k] for k in source_fields if k in q.get('source',{})}} for q in rows]
 payload={'asset_base':assets,'questions':rows,'question_only':question_only,'paper':({'id':paper['id'],'title':paper.get('title','')} if paper else None)}
 script_names=['marked.min.js','katex.min.js','reader-markdown.js','study-math.js','render-core.js']
 if not question_only:script_names += ['audible-audio.js','animations.js','practice-media-data.js','practice-media.js']
 media_css='' if question_only else f'<link rel="stylesheet" href="{assets}/practice-media.css">'
 scripts=''.join(f'<script src="{html.escape(assets)}/{n}"></script>' for n in script_names)
 tools='<input id="practice-search" aria-label="搜索题目" placeholder="搜索考点、题号或题目"><select id="practice-tier" aria-label="优先级"><option value="">全部等级</option><option>必会</option><option>可能考</option><option>罕见</option></select>' if not paper else ''
 if not question_only:tools+='<a class="practice-link" href="'+html.escape(str(Path(home).parent/'动画目录.html'))+'">动画目录</a><button id="practice-expand">展开全部解析</button><button id="practice-collapse">收起解析</button>'
 tools+='<button id="practice-print">打印 / 保存 PDF</button><span id="practice-count"></span>'
 return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><link rel="stylesheet" href="{assets}/katex.min.css"><link rel="stylesheet" href="{assets}/practice.css">{media_css}<body class="practice-page"><main class="practice-shell"><header class="practice-hero"><nav class="practice-nav"><a href="{html.escape(home)}">返回总目录</a></nav><h1>{html.escape(title)}</h1><p>{html.escape(description)}</p></header><div class="practice-toolbar">{tools}</div><div id="practice-questions"></div><footer class="practice-finish">复变函数 A · 2026-11-20 制作 · 分级表示备考优先级，不保证未来命中。训练分值和用时以页面说明为准。</footer></main>{scripts}<script>window.PRACTICE_DATA={js_data(payload)};</script><script src="{assets}/practice.js"></script></body></html>'''

def copy_assets(source, target):
 source,target=Path(source),Path(target);target.mkdir(parents=True,exist_ok=True)
 for name in ['marked.min.js','katex.min.js','katex.min.css','reader-markdown.js','study-math.js','render-core.js','audible-audio.js','animations.js']:
  shutil.copy2(source/name,target/name)
 shutil.copytree(source/'fonts',target/'fonts',dirs_exist_ok=True)
 for p in RESOURCE.iterdir():shutil.copy2(p,target/p.name)
 if not (target/'practice-media-data.js').exists():(target/'practice-media-data.js').write_text('window.PRACTICE_MEDIA=[];\n')

def attach_reader(book, editions):
 book=Path(book);assets=book/'assets';assets.mkdir(exist_ok=True)
 for p in RESOURCE.iterdir():shutil.copy2(p,assets/p.name)
 public_editions = {name: [public_question(q) for q in rows] for name, rows in editions.items()}
 (assets/'practice-data.js').write_text('window.PRACTICE_EDITIONS='+js_data(public_editions)+';\n',encoding='utf-8')
 index=book/'index.html';text=index.read_text(encoding='utf-8')
 if 'src="assets/practice-reader.js"' not in text:
  injected='<link rel="stylesheet" href="assets/practice.css"><script src="assets/practice-data.js"></script><script src="assets/practice.js"></script><script src="assets/practice-reader.js"></script>'
  text=text.replace('</body>',injected+'</body>') if '</body>' in text else text+injected
  index.write_text(text,encoding='utf-8')
 if 'src="assets/practice-media.js"' not in text:
  text=text.replace('<script src="assets/practice.js">','<link rel="stylesheet" href="assets/practice-media.css"><script src="assets/practice-media-data.js"></script><script src="assets/practice-media.js"></script><script src="assets/practice.js">')
  index.write_text(text,encoding='utf-8')
 if not (assets/'practice-media-data.js').exists():(assets/'practice-media-data.js').write_text('window.PRACTICE_MEDIA=[];\n')
 if 'id="practice-animation-index"' not in text:
  text=text.replace('<div id="reader-toc-content">','<p class="practice-directory-link"><a id="practice-animation-index" href="../动画目录.html">动画目录（正文与习题）</a></p><div id="reader-toc-content">')
  index.write_text(text,encoding='utf-8')
