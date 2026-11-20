"""Build basic/full/custom practice from reviewed records and an existing reader.

No model requests occur during publication. Use practice_model.py for authoring
and independent review; preserve its original exchanges before accepting records.
"""
from pathlib import Path
import argparse,hashlib,html,json,shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiforstudy.practice.catalog import validate_catalog,select_edition,validate_review_files
from aiforstudy.practice.assembly import validate_papers
from aiforstudy.practice.render import build_page,copy_assets,attach_reader,js_data
from aiforstudy.practice.media import validate_media_manifest,copy_media_assets

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')

def export_reviewed_catalog(questions, source_dir, output, filename):
    """Copy verified bytes without letting an unrelated evidence file replace them."""
    copied = {}
    for question in questions:
        for row in question['review']['evidence_files']:
            source = (source_dir / row['path']).resolve()
            key = (source, row['sha256'])
            if key not in copied:
                if not source.is_relative_to(source_dir):
                    raise ValueError('evidence path escape')
                evidence = source.read_bytes()
                digest = hashlib.sha256(evidence).hexdigest()
                if digest != row['sha256']:
                    raise ValueError(question['id'] + ': evidence changed during publication')
                relative = Path('evidence') / (digest + '.json')
                target = output / relative
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    target.write_bytes(evidence)
                copied[key] = relative.as_posix()
            row['path'] = copied[key]
    validate_review_files(questions, output)
    write(output / filename, questions)


def build(catalog, papers, reader, out, *, originals=None, profile=None, additions=None, media=None,
          source_media_questions=()):
    catalog,papers,reader,out=map(lambda p:Path(p).resolve(),(catalog,papers,reader,out))
    if out==reader or out.is_relative_to(reader) or reader.is_relative_to(out):
        raise ValueError('input reader and output overlap')
    if out.exists() and any(out.iterdir()):raise ValueError('Use a new empty output folder')
    questions=read(catalog);validate_catalog(questions);validate_review_files(questions,catalog.parent)
    if additions and not profile:raise ValueError('teacher additions require a profile')
    extra=read(additions) if additions else []
    if extra:
        validate_catalog(extra);validate_review_files(extra,Path(additions).resolve().parent)
    editions={'basic':select_edition(questions,'basic'),'full':select_edition(questions,'full')}
    if profile:
        teacher_profile = read(profile)
        editions['custom'] = select_edition(questions, 'custom', teacher_profile, extra)
    if any(not rows for rows in editions.values()):raise ValueError('edition has no accepted questions')
    text=(reader/'data/data.js').read_text(encoding='utf-8')
    reader_data=json.loads(text[text.find('{'):text.rfind('}')+1])
    for rows in editions.values():
        for q in rows:
            if q.get('mount_unit_id',q['unit_id']) not in reader_data['units']:
                raise ValueError(q['id']+': unknown reader unit')
            if not isinstance(q.get('anchor_text'),str) or not q['anchor_text'].strip():raise ValueError(q['id']+': compile an exact reader anchor before publication')
    edition_questions = {name: {q['id']: q for q in rows} for name, rows in editions.items()}
    allquestions = questions + extra
    managed_media = {'practice-media', 'practice-media-data.js', 'textbook-animation-data.js'}
    if media:
        media = Path(media).resolve()
        manifest_bytes = media.read_bytes()
        media_manifest = json.loads(manifest_bytes)
        published_questions = {q['id']: q for rows in editions.values() for q in rows}
        # A rebuild can omit previously published teacher questions. Validate
        # the saved bindings before filtering, using the current edition's
        # review when a question is going to be published again.
        binding_questions = {q['id']: q for q in source_media_questions}
        binding_questions.update(published_questions)
        validate_media_manifest(media_manifest, binding_questions.values(), media.parent)
        retained = []
        for item in media_manifest['items']:
            if item['question_id'] in published_questions:
                retained.append(item)
            elif item.get('static_existing_in_solution') is True and 'svg' in item['files']:
                # A saved reader can still have the omitted answer figure at
                # its assets root, independently of the runtime media tree.
                managed_media.add(Path(item['files']['svg']['path']).name)
        if len(retained) != len(media_manifest['items']):
            media_manifest = {**media_manifest, 'items': retained}
            manifest_bytes = json.dumps(media_manifest, ensure_ascii=False, indent=2).encode('utf-8')
    ps=read(papers);validated=validate_papers(allquestions,ps)
    for paper in validated:
        edition=paper.get('edition','full')
        if edition not in editions:raise ValueError('paper refers to unknown edition')
        allowed = edition_questions[edition]
        if any(x['question_id'] not in allowed for x in paper['items']):raise ValueError('paper is outside its edition')
    if not originals and any(q['source']['kind']=='past_exam' for rows in editions.values() for q in rows):
        raise ValueError('past-exam questions require an originals manifest')
    source_rows=read(originals) if originals else []
    # Validate all original source files before creating any output.
    for row in source_rows:
        src=Path(row['path'])
        if not src.is_file() or (row.get('sha256') and hashlib.sha256(src.read_bytes()).hexdigest()!=row['sha256']):
            raise ValueError('original source missing or changed')
    out.mkdir(parents=True,exist_ok=True)
    def discard_old_media(directory, names):
        if Path(directory) != reader / 'assets':
            return ()
        return managed_media.intersection(names)

    book=out/'教材';shutil.copytree(reader,book,ignore=discard_old_media)
    copy_assets(reader/'assets',out/'assets')
    original_dir=out/'试卷包/往年原卷';original_dir.mkdir(parents=True,exist_ok=True)
    originals_out=[]
    for row in source_rows:
        p=Path(row['path']);digest=hashlib.sha256(p.read_bytes()).hexdigest()
        if row.get('sha256') and row['sha256']!=digest:raise ValueError('Original PDF hash changed: '+p.name)
        name=p.name
        course=row.get('course',row.get('title',''))
        group='复变函数A-B' if '复变函数' in course else ('数学专业复分析' if '复分析' in course else '其他原卷')
        dest=original_dir/group;dest.mkdir(exist_ok=True)
        target=dest/name
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:target=dest/(digest[:8]+'-'+name)
        if not target.exists():shutil.copy2(p,target)
        originals_out.append({**row,'path':str(target.relative_to(out)),'sha256':digest})
    original_paths={str(Path(row['path']).resolve()):output['path'] for row,output in zip(source_rows,originals_out)}
    hash_paths={row['sha256']:row['path'] for row in originals_out}
    source_hash_cache={}
    for rows in editions.values():
        for q in rows:
            if q['source']['kind']=='past_exam':
                source_path=str(Path(q['source'].get('pdf_path','')).resolve())
                if source_path not in original_paths:
                    digest=q.get('source_file_sha256')
                    if Path(source_path).is_file():
                        if source_path not in source_hash_cache:source_hash_cache[source_path]=hashlib.sha256(Path(source_path).read_bytes()).hexdigest()
                        digest=source_hash_cache[source_path]
                    if digest not in hash_paths:raise ValueError(q['id']+': original PDF omitted from manifest')
                    q['public_source_path']=hash_paths[digest]
                else:q['public_source_path']=original_paths[source_path]
            elif q['source']['kind']=='textbook_exercise':
                pdf=book/'assets/textbook.pdf';source=Path(q['source'].get('pdf_path',''))
                expected=q.get('source_file_sha256')
                if source.is_file():
                    key=str(source.resolve())
                    if key not in source_hash_cache:source_hash_cache[key]=hashlib.sha256(source.read_bytes()).hexdigest()
                    expected=source_hash_cache[key]
                if not pdf.is_file() or not expected or hashlib.sha256(pdf.read_bytes()).hexdigest()!=expected:
                    raise ValueError(q['id']+': textbook source mismatch')
                q['public_source_path']='教材/assets/textbook.pdf'
    for rows in editions.values():
        for q in rows:
            if q['source']['kind']=='generated_variant' and q.get('source_file_sha256') in hash_paths:q['public_source_path']=hash_paths[q['source_file_sha256']]
    # The same question records power reader folds and standalone editions.
    attach_reader(book,editions)
    if media:
        media_root=media.parent
        for assets in (out/'assets',book/'assets'):
            runtime=copy_media_assets(media_manifest,media_root,assets)
            (assets/'practice-media-data.js').write_text('window.PRACTICE_MEDIA='+js_data(runtime)+';\n',encoding='utf-8')
        source=out/'媒体源';source.mkdir()
        for item in media_manifest['items']:
            for row in [*item['files'].values(),item['review_evidence']]:
                dest=source/row['path'];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(media_root/row['path'],dest)
        (source/'manifest.json').write_bytes(manifest_bytes)
        validate_media_manifest(media_manifest, published_questions.values(), source)
    names={'basic':'基础版','full':'完整版','custom':'定制版'};summary={};links=[]
    for key,qs in editions.items():
        name=names[key];base=f'{name}-习题集.html'
        description=f'收录 {len(qs)} 道已完成独立求解与对照审核的题目。按教材单元、必会/可能考/罕见及难度排列。教材题标为作业候选；实际布置题号未知。'
        for q in qs:q['public_textbook_path']='教材/index.html?practice='+key+'#/'+q.get('mount_unit_id',q['unit_id'])
        (out/base).write_text(build_page('复变函数 A · '+name+'习题集',qs,description=description),encoding='utf-8')
        (out/f'{name}-习题题册.html').write_text(build_page('复变函数 A · '+name+'习题题册',qs,question_only=True,description=description),encoding='utf-8')
        summary[key]={'questions':len(qs),'topics':sorted({q['topic'] for q in qs}),'source_kinds':{s:sum(q['source']['kind']==s for q in qs) for s in sorted({q['source']['kind'] for q in qs})}}
        links.append((name+'：教材内折叠练习','教材/index.html?practice='+key))
        links.append((name+'：独立习题与解析',base));links.append((name+'：纯题册',f'{name}-习题题册.html'))
    papers_dir=out/'试卷包/生成试卷';papers_dir.mkdir(parents=True,exist_ok=True)
    for paper in validated:
        byid = edition_questions[paper.get('edition', 'full')]
        qs=[{**byid[x['question_id']],'points':x['points'],'section':x.get('section','')} for x in paper['items']]
        desc=f"{paper['purpose']} 本卷 {len(qs)} 题，训练分值 {paper['total_points']} 分。"+paper.get('time_basis','')
        if paper.get('training_minutes'):desc+=' 建议训练时间 '+str(paper['training_minutes'])+' 分钟（估计，非官方时长或学生实测）。'
        for suffix,only in [('题卷',True),('解析',False)]:
            file=paper['id']+'-'+suffix+'.html'
            (papers_dir/file).write_text(build_page(paper['title']+' · '+suffix,qs,question_only=only,paper=paper,description=(f"本卷 {len(qs)} 题，训练分值 {paper['total_points']} 分。独立作答，条件以题面及共用约定为准。"+paper.get('time_basis','')+((' 建议训练时间 '+str(paper['training_minutes'])+' 分钟（估计）。') if paper.get('training_minutes') else '') if only else desc),assets='../../assets',home='../../index.html'),encoding='utf-8')
            links.append((paper['title']+' · '+suffix,'试卷包/生成试卷/'+file))
    original_links=[(r.get('title') or Path(r['path']).name,r['path']) for r in originals_out]
    paper_links=links[len(editions)*3:]
    trial_ids={p['id'] for p in validated if p.get('publication_status')=='reference_trial'}
    trial_links=[row for row in paper_links if any(Path(row[1]).name.startswith(i+'-') for i in trial_ids)]
    formal_links=[row for row in paper_links if row not in trial_links]
    base_papers=sum(p.get('edition')=='basic' and p.get('publication_status')!='reference_trial' for p in validated)
    full_papers=sum(p.get('edition') in ('basic','full') and p.get('publication_status')!='reference_trial' for p in validated)
    def linklist(rows):return '<ul>'+''.join('<li><a href="'+html.escape(href)+'">'+html.escape(label)+'</a></li>' for label,href in rows)+'</ul>'
    index=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>复变函数 A · 习题与试卷</title><link rel="stylesheet" href="assets/practice.css"><body class="practice-page"><main class="practice-shell"><header class="practice-hero"><h1>复变函数 A<br>习题与试卷</h1><p>基础版用于建立共同核心，完整版扩展不同考法与难度。已保存的原始试卷与新编试卷集中归档。</p><p>2026-11-20 · 本地离线阅读 · 题目与解析同源</p></header><section class="practice-q"><h2>开始学习</h2>{linklist(links[:len(editions)*3])}<p class="practice-note">展开习题后可以单独展开解析。必会/可能考/罕见是有依据的备考优先级，不表示下一次考试的确定概率。</p></section><section class="practice-q"><h2>新编与重组试卷</h2><p>基础版正式训练卷 {base_papers} 张；完整版含基础卷及扩展卷，共 {full_papers} 张。定制卷按实际配置单列。</p>{linklist(formal_links)}<h3>保留的结构参照试制卷</h3><p>以下试制卷的题目经过数学核对，但任务链不与母卷逐位完全对应，不计入正式完全拟合式卷。</p>{linklist(trial_links)}<p>试卷中的训练分值、限时建议与原卷官方信息分开；目前没有真人学习收益或未来押题率证明。</p></section><section class="practice-q"><h2>往年原卷</h2>{linklist(original_links)}<p>复变函数A是本次选题主体；B卷与数学专业复分析分别存档，未混入A卷频次依据。</p></section><section class="practice-q"><h2>制作与核验</h2><p><a href="制作说明.html">阅读制作说明、来源与已知边界</a></p></section></main></body></html>'''
    (out/'index.html').write_text(index,encoding='utf-8')
    papers_index='<html lang="zh-CN"><meta charset="utf-8"><title>统一试卷包</title><body><h1>复变函数试卷总目录</h1>'+linklist([(a,b.removeprefix('试卷包/')) for a,b in links[len(editions)*3:]+original_links])+'</body></html>'
    (out/'试卷包/index.html').write_text(papers_index,encoding='utf-8')
    guide='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>制作说明</title><body><h1>制作说明与适用范围</h1><p>本包采用有来源的往年题、教材课后题及针对考法的新编题。教材课后题只标为作业候选，实际布置题号未知。</p><p>独立求解与最终解析对照证据保存在 evidence 目录；这不等于真人学习效果或未来押题率已被验证。原教材正文的历史审核状态不因新增练习而改变。</p><p>基础版卷少，完整版为基础卷加扩展卷。训练分值/限时按页面说明，与原卷官方信息区分。</p><p>教师定制通过 profile JSON 选择题目、调整优先级及导入有完整审核证据的新题；新题的正文单元与挂载位置必须核对。</p><p><a href="index.html">返回目录</a></p></body></html>'
    (out/'制作说明.html').write_text(guide,encoding='utf-8')
    # Keep the common catalog's portable links bound to the full edition.
    for question in questions:
        published = edition_questions['full'][question['id']]
        for field in ('public_source_path', 'public_textbook_path'):
            if field in published:
                question[field] = published[field]
    export_reviewed_catalog(questions, catalog.parent, out, 'catalog.json')
    if profile:
        write(out / 'teacher-profile.json', teacher_profile)
        if additions:
            export_reviewed_catalog(extra, Path(additions).resolve().parent, out, 'teacher-additions.json')
    write(out / 'papers.json', validated)
    report={'schema_version':'practice-delivery-v1','editions':summary,'papers':len(validated),'formal_papers':full_papers,'basic_papers':base_papers,'reference_trial_papers':len(trial_ids),'original_files':len(originals_out),'originals':originals_out,'semantic_scope':'Questions have recorded independent solutions and final comparison; no future-hit/student-effect claim.','source_reader':str(reader),'readiness':'candidate_pending_render_checks'}
    write(out/'build-report.json',report)
    from tools.build_practice_animation_gallery import build_gallery
    build_gallery(out)
    page=(out/'index.html').read_text(encoding='utf-8')
    page=page.replace('<h2>开始学习</h2>','<h2>开始学习</h2><p><a href="动画目录.html">打开动画目录：正文动画与习题动画</a></p>')
    (out/'index.html').write_text(page,encoding='utf-8')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ['catalog','papers','reader','out']:p.add_argument('--'+k,type=Path,required=True)
    for k in ['originals','profile','additions','media']:p.add_argument('--'+k,type=Path)
    a=p.parse_args();r=build(a.catalog,a.papers,a.reader,a.out,originals=a.originals,profile=a.profile,additions=a.additions,media=a.media);print(json.dumps(r['editions'],ensure_ascii=False))
if __name__=='__main__':main()
