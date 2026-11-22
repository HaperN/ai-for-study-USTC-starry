"""Publish only the reviewed learning surface of an expanded local course."""
from pathlib import Path
from types import SimpleNamespace
import argparse,hashlib,json,re,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from aiforstudy.community.book import BOOK,MEDIA,PRACTICE,_assignment
from aiforstudy.integrated.server import delivery_names,reader_media_names,PRACTICE_FILES,READER_FILES,FONT
from aiforstudy.practice.catalog import validate_catalog,validate_review_files,select_edition
from aiforstudy.practice.assembly import validate_papers
from aiforstudy.practice.render import js_data
from tools.prepare_integrated_release import project_book,check_roots
from tools.build_practice_animation_gallery import build_gallery
from tools.build_study_entry import build_entry
from tools.build_practice_delivery import (
    validate_paper_view_parents, validate_paper_parent_exclusion, validate_paper_view_excerpts,
    study_profile, paper_edition_questions,
)

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def learning_files(source,reader_data):
    source=Path(source);raw=(source/'教材'/BOOK).read_bytes() if (source/'教材'/BOOK).is_file() else b''
    media=(source/'教材'/MEDIA).read_bytes()
    server=SimpleNamespace(config=SimpleNamespace(practice=source),snapshot=SimpleNamespace(raw={MEDIA:media}),units=set(reader_data['units']))
    pages,practice_media=delivery_names(server)
    course=pages|practice_media|PRACTICE_FILES|{MEDIA,'index.html','点这里开始阅读.html','assets/textbook-animation-data.js'}
    reader=READER_FILES|reader_media_names(raw)|practice_media|{PRACTICE,MEDIA,'index.html'}
    for base,names in ((source,course),(source/'教材',reader)):
        names.update(p.relative_to(base).as_posix() for p in (base/'assets/fonts').glob('*') if FONT.fullmatch(p.relative_to(base).as_posix()))
    return {'course':course,'reader':reader}

def publish_student(source,output,*,paper_catalog=None):
    source,output=Path(source).resolve(),Path(output).resolve();check_roots([source],[output])
    if output.exists() and any(output.iterdir()):raise ValueError('Use a new empty student output folder')
    inventory=json.loads((source/'文件校验清单.json').read_text())['files']
    for row in inventory:
        p=source/row['path']
        if p.is_symlink() or not p.resolve().is_relative_to(source) or not p.is_file() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('Source inventory mismatch: '+row['path'])
    questions=json.loads((source/'catalog.json').read_text());validate_catalog(questions);validate_review_files(questions,source)
    extra_path=source/'teacher-additions.json'
    extra=json.loads(extra_path.read_text()) if extra_path.is_file() else []
    if extra:validate_catalog(extra);validate_review_files(extra,source)
    if any(q.get('paper_only') is True for q in questions+extra):raise ValueError('paper-only views must not enter main study catalog')
    saved_views=source/'paper-catalog.json'
    view_path=Path(paper_catalog).resolve() if paper_catalog else saved_views
    if paper_catalog and not view_path.is_file():raise ValueError('paper catalog missing')
    views=json.loads(view_path.read_text()) if view_path.is_file() else []
    if paper_catalog:
        if not saved_views.is_file():raise ValueError('paper catalog must be saved with the rendered delivery')
        stored=json.loads(saved_views.read_text())
        identity=lambda rows:{q['id']:(q['review']['content_sha256'],q.get('basic')) for q in rows}
        if identity(views)!=identity(stored):raise ValueError('paper catalog differs from rendered delivery')
    if views:
        validate_catalog(views);validate_review_files(views,view_path.parent)
        validate_paper_view_parents(questions+extra,views)
        validate_paper_view_excerpts(questions+extra,views,view_path.parent)
    allquestions=questions+extra+views
    papers=json.loads((source/'papers.json').read_text());validate_papers(allquestions,papers)
    validate_paper_parent_exclusion(allquestions,papers)
    editions={'basic':select_edition(questions,'basic'),'full':select_edition(questions,'full')}
    profile_path=source/'teacher-profile.json';profile=json.loads(profile_path.read_text()) if profile_path.is_file() else {}
    if profile_path.is_file():editions['custom']=select_edition(questions,'custom',study_profile(profile,{q['id'] for q in views}),extra)
    allowed=paper_edition_questions(editions,views,profile)
    for paper in papers:
        if paper.get('edition','full') not in allowed or any(item['question_id'] not in allowed[paper.get('edition','full')] for item in paper['items']):
            raise ValueError('paper is outside its edition')
    book=project_book(_assignment((source/'教材'/BOOK).read_bytes(),'__DATA__'))
    selected=learning_files(source,book);course=output/'复变函数A';course.mkdir(parents=True,exist_ok=True)
    for source_root,target,names in ((source,course,selected['course']),(source/'教材',course/'教材',selected['reader'])):
        for name in sorted(names):
            src=source_root/name
            if not src.is_file():continue
            if src.is_symlink():raise ValueError('Symbolic link in learning surface')
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dest)
    data=course/'教材'/BOOK;data.parent.mkdir(parents=True,exist_ok=True);data.write_text('window.__DATA__='+js_data(book)+';\n',encoding='utf-8')
    for script in ('reader-markdown.js','study-lesson.js','notes.js','marked.min.js'):
        layer='vendor' if script=='marked.min.js' else 'assets';shutil.copy2(ROOT/'blocks/render'/layer/script,course/'教材/assets'/script)
    for css in course.rglob('katex.min.css'):
        css.write_text(re.sub(r',url\(fonts/[^)]+\.(?:woff|ttf)\)\s*format\([^)]*\)', '',css.read_text()),encoding='utf-8')
    index=course/'教材/index.html';text=index.read_text();text=re.sub(r'<script>window\.__BOOK_VERSION__=.*?</script>','',text,flags=re.S)
    text=text.replace('<head>','<head><script>window.__BOOK_VERSION__='+js_data({'book':book['book'],'version':sha(data)})+';</script>',1);index.write_text(text,encoding='utf-8')
    build_gallery(course);build_entry(course,course_prefix='');build_entry(output)
    for name in ('日常巩固.html','押题.html','三维图解.html'):
        if not (course/name).is_file():raise ValueError('Missing required learning page '+name)
    for page in [data,course/'教材'/PRACTICE,*course.rglob('*.html'),course/MEDIA]:
        if b'/Users/' in page.read_bytes():raise ValueError('Private local path in student surface '+str(page.relative_to(output)))
    files=[{'path':p.relative_to(output).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(output.rglob('*')) if p.is_file()]
    report={'schema_version':'ExpandedStudentReleaseV1','questions':len(questions),'basic_questions':sum(q.get('basic') is True for q in questions),'units':len(book['units']),'papers':len(papers),'source_manifest_sha256':sha(source/'文件校验清单.json'),'files':files}
    saved_report=json.loads((source/'build-report.json').read_text()) if (source/'build-report.json').is_file() else {}
    report.update(paper_views=len(views),paper_view_new_source_materials=0,release_date=saved_report.get('release_date'))
    (output/'文件校验清单.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--delivery',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--paper-catalog',type=Path);args=parser.parse_args();result=publish_student(args.delivery,args.out,paper_catalog=args.paper_catalog);print(json.dumps({k:v for k,v in result.items() if k!='files'},ensure_ascii=False))
