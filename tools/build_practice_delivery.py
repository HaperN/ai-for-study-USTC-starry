"""Build basic/full/custom practice from reviewed records and an existing reader.

No model requests occur during publication. Use practice_model.py for authoring
and independent review; preserve its original exchanges before accepting records.
"""
from pathlib import Path
from copy import deepcopy
from datetime import date
import argparse,hashlib,html,json,re,shutil,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aiforstudy.practice.catalog import validate_catalog,select_edition,validate_review_files
from aiforstudy.practice.assembly import validate_papers
from aiforstudy.practice.render import build_page,copy_assets,attach_reader,js_data
from aiforstudy.practice.media import validate_media_manifest,copy_media_assets

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')


def validate_paper_view_parents(questions, views):
    """A separately reviewed view is a scoped reuse, never a new main item."""
    parents = {q['id']: q for q in questions}
    for view in views:
        parent = parents.get(view.get('source_parent_question_id'))
        if (view.get('paper_only') is not True or view['id'] in parents or not parent
                or parent.get('paper_only') is True):
            raise ValueError(view['id'] + ': paper view needs a distinct main-catalog parent')
        expected = parent['review']['content_sha256']
        if (view.get('parent_content_sha256') != expected
                or view['source'].get('source_parent_question_id') != parent['id']
                or view['source'].get('parent_content_sha256') != expected):
            raise ValueError(view['id'] + ': paper view parent content binding mismatch')
        numbers = view.get('source_subquestion_numbers')
        if (not isinstance(numbers, list) or not numbers or len(set(map(str, numbers))) != len(numbers)
                or type(view.get('subquestion_count')) is not int or not 1 <= view['subquestion_count'] <= 4
                or view.get('new_source_materials') != 0):
            raise ValueError(view['id'] + ': invalid paper view scope or material count')
    return True


def validate_paper_parent_exclusion(questions, papers):
    byid = {q['id']: q for q in questions}
    for paper in papers:
        ids = {item['question_id'] for item in paper['items']}
        for ident in ids:
            if byid[ident].get('paper_only') and byid[ident].get('source_parent_question_id') in ids:
                raise ValueError(paper['id'] + ': parent and its paper view cannot share a paper')
    return True


def validate_paper_view_excerpts(questions, views, evidence_root):
    """Verify host excerpts against the parent's actual, hash-bound blind row."""
    root = Path(evidence_root).resolve(); parents = {q['id']: q for q in questions}; cache = {}
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    def objects(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():yield from objects(child)
        elif isinstance(value, list):
            for child in value:yield from objects(child)
    def load(ref):
        path = (root / ref['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != ref['sha256']:
            raise ValueError('paper view excerpt raw evidence mismatch')
        if str(path) not in cache:cache[str(path)] = read(path)
        return cache[str(path)]
    def pointer(value, path):
        if not path.startswith('/'):raise ValueError('paper view excerpt pointer invalid')
        for token in path[1:].split('/'):
            token=token.replace('~1','/').replace('~0','~')
            if isinstance(value,list):
                if not token.isdigit():raise ValueError('paper view excerpt index invalid')
                value=value[int(token)]
            else:value=value[token]
        return value
    for view in views:
        files = view['review']['evidence_files']
        records = [row for ref in files for row in objects(load(ref))
                   if row.get('id') == view['id'] and digest(row) == view['review']['blind_solution_sha256']]
        if not records:raise ValueError(view['id'] + ': paper view independent row missing')
        blind = records[0]
        if blind.get('engine') != 'host_excerpt_of_actual_independent_solution':continue
        parent = parents[view['source_parent_question_id']]; provenance = blind.get('provenance', {})
        historical_hash = view['source'].get('excerpt_parent_content_sha256', view['parent_content_sha256'])
        if (provenance.get('source_parent_question_id') != parent['id']
                or provenance.get('parent_content_sha256') != historical_hash
                or provenance.get('parent_blind_solution_sha256') != parent['review']['blind_solution_sha256']
                or provenance.get('parent_blind_solver') != parent['review']['blind_solver']
                or provenance.get('new_model_call') is not False):
            raise ValueError(view['id'] + ': paper view excerpt parent B binding mismatch')
        raw_ref = provenance.get('raw_evidence', {})
        if (not any(r.get('sha256') == raw_ref.get('sha256') for r in files)
                or not any(r.get('sha256') == raw_ref.get('sha256') for r in parent['review']['evidence_files'])):
            raise ValueError(view['id'] + ': parent raw B must remain in review evidence')
        # The immutable provenance path records its historical location. The
        # verified evidence list supplies its current, portable location by SHA.
        raw = load(next(r for r in files if r.get('sha256') == raw_ref['sha256']))
        if isinstance(raw,dict) and raw.get('mode') and raw['mode'] != 'blind':
            raise ValueError(view['id'] + ': author evidence cannot be used as blind excerpt')
        if (isinstance(raw,dict) and parent['review']['blind_solver'].startswith('blind:')
                and raw.get('request_sha256') != parent['review']['blind_solver'].removeprefix('blind:')):
            raise ValueError(view['id'] + ': parent raw independent request mismatch')
        parent_pointer = provenance.get('parent_blind_row_pointer', '')
        original = pointer(raw,parent_pointer)
        if (not isinstance(original,dict) or original.get('id') != parent['id']
                or original.get('status') != 'solved' or digest(original) != parent['review']['blind_solution_sha256']):
            raise ValueError(view['id'] + ': excerpt did not identify actual independent B')
        extracted = {'answer': [], 'solution': []}
        for part in provenance.get('excerpts', []):
            field = next((f for f in extracted if part.get('json_pointer') == parent_pointer + '/' + f), None)
            if not field:raise ValueError(view['id'] + ': excerpt is outside parent B answer/solution')
            text = original[field]; bounds = part.get('char_range_0_based_half_open', [])
            if (len(bounds) != 2 or any(type(n) is not int for n in bounds)
                    or not 0 <= bounds[0] < bounds[1] <= len(text)):
                raise ValueError(view['id'] + ': excerpt range invalid')
            fragment = text[bounds[0]:bounds[1]]
            if fragment != part.get('text') or hashlib.sha256(fragment.encode()).hexdigest() != part.get('excerpt_sha256'):
                raise ValueError(view['id'] + ': excerpt text does not match raw B')
            if 'utf8_byte_range_0_based_half_open' in part and part['utf8_byte_range_0_based_half_open'] != [len(text[:n].encode()) for n in bounds]:
                raise ValueError(view['id'] + ': excerpt byte range mismatch')
            extracted[field].append(fragment.strip())
        if any(not parts or '\n\n'.join(parts) != blind[field].strip() for field,parts in extracted.items()):
            raise ValueError(view['id'] + ': blind excerpt contains untraced text')
        if 'condition_indices_0_based' in provenance:
            indices=provenance['condition_indices_0_based']
            if any(type(n) is not int or not 0 <= n < len(original.get('conditions',[])) for n in indices):
                raise ValueError(view['id'] + ': excerpt condition index invalid')
            if blind.get('conditions') != [original['conditions'][n] for n in indices]:
                raise ValueError(view['id'] + ': excerpt conditions differ from raw B')
            for condition in blind['conditions']:
                label=re.match(r'^[（(](\d+)[）)]',condition)
                if label and label[1] not in {str(n) for n in view['source_subquestion_numbers']}:
                    raise ValueError(view['id'] + ': condition belongs to another parent subquestion')
    return True


def study_profile(profile, view_ids):
    """Paper-only include/override entries must not enter the study workbook."""
    result = deepcopy(profile)
    if set(profile.get('include_ids', [])) & set(profile.get('exclude_ids', [])):
        raise ValueError('teacher includes and excludes the same question')
    for key in ('include_ids', 'exclude_ids'):
        if key in result:
            result[key] = [ident for ident in result[key] if ident not in view_ids]
    if 'priority_overrides' in result:
        result['priority_overrides'] = {ident: value for ident, value in result['priority_overrides'].items()
                                        if ident not in view_ids}
    return result


def paper_edition_questions(editions, views, profile=None):
    result = {name: {q['id']: q for q in rows} for name, rows in editions.items()}
    profile = profile or {}
    for name, mapping in result.items():
        for view in views:
            if name == 'basic' and view.get('basic') is not True:
                continue
            if name == 'custom' and (view['id'] not in profile.get('include_ids', [])
                                     or view['id'] in profile.get('exclude_ids', [])):
                continue
            chosen = deepcopy(view)
            if name == 'custom' and view['id'] in profile.get('priority_overrides', {}):
                override = profile['priority_overrides'][view['id']]
                if override.get('priority') not in ('必会', '可能考', '罕见') or not override.get('reason'):
                    raise ValueError('paper view priority override requires reason')
                chosen.update(priority=override['priority'], priority_reason=override['reason'])
            mapping[chosen['id']] = chosen
    return result

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
          source_media_questions=(), spatial=None, paper_catalog=None, release_date=None):
    catalog,papers,reader,out=map(lambda p:Path(p).resolve(),(catalog,papers,reader,out))
    if out==reader or out.is_relative_to(reader) or reader.is_relative_to(out):
        raise ValueError('input reader and output overlap')
    if out.exists() and any(out.iterdir()):raise ValueError('Use a new empty output folder')
    if release_date is not None:
        try:release_date=date.fromisoformat(release_date).isoformat()
        except (TypeError,ValueError) as error:raise ValueError('release_date must be YYYY-MM-DD') from error
    questions=read(catalog);validate_catalog(questions);validate_review_files(questions,catalog.parent)
    if additions and not profile:raise ValueError('teacher additions require a profile')
    extra=read(additions) if additions else []
    if extra:
        validate_catalog(extra);validate_review_files(extra,Path(additions).resolve().parent)
    if any(q.get('paper_only') is True for q in questions+extra):
        raise ValueError('paper-only views must not enter main or teacher study catalogs')
    paper_catalog = Path(paper_catalog).resolve() if paper_catalog else None
    views = read(paper_catalog) if paper_catalog else []
    if views:
        validate_catalog(views);validate_review_files(views,paper_catalog.parent)
        validate_paper_view_parents(questions+extra,views)
        validate_paper_view_excerpts(questions+extra,views,paper_catalog.parent)
    view_ids = {q['id'] for q in views}
    if spatial:
        requested=read(spatial)
        if set(requested)-{'sphere','log'} or any(not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) for ids in requested.values()):raise ValueError('invalid spatial bindings')
        byid={q['id']:q for q in questions+extra}
        for scene,ids in requested.items():
            if set(ids)-set(byid):raise ValueError('spatial binding names an unknown question')
            for ident in ids:byid[ident].setdefault('spatial_scenes',[]).append(scene)
    for q in questions+extra:
        if 'spatial_scenes' in q:
            if not isinstance(q['spatial_scenes'],list) or any(s not in ('sphere','log') for s in q['spatial_scenes']):raise ValueError('invalid question spatial scene')
            q['spatial_scenes']=list(dict.fromkeys(q['spatial_scenes']))
    editions={'basic':select_edition(questions,'basic'),'full':select_edition(questions,'full')}
    teacher_profile = {}
    if profile:
        teacher_profile = read(profile)
        editions['custom'] = select_edition(questions, 'custom', study_profile(teacher_profile,view_ids), extra)
    explicit_custom_views = view_ids.intersection(teacher_profile.get('include_ids',[])) - set(teacher_profile.get('exclude_ids',[]))
    if any(not rows and not (name=='custom' and explicit_custom_views) for name,rows in editions.items()):
        raise ValueError('edition has no accepted questions')
    text=(reader/'data/data.js').read_text(encoding='utf-8')
    reader_data=json.loads(text[text.find('{'):text.rfind('}')+1])
    for q in [*questions,*extra,*[q for rows in editions.values() for q in rows]]:
        chapter=q['unit_id'].split('-')[0]
        if chapter in reader_data['units'] and chapter[2:].isdigit():
            q['section']='第 '+str(int(chapter[2:]))+' 章 · '+reader_data['units'][chapter].get('title',chapter)
    for rows in editions.values():
        for q in rows:
            if q.get('mount_unit_id',q['unit_id']) not in reader_data['units']:
                raise ValueError(q['id']+': unknown reader unit')
            if not isinstance(q.get('anchor_text'),str) or not q['anchor_text'].strip():raise ValueError(q['id']+': compile an exact reader anchor before publication')
            seen={q.get('mount_unit_id',q['unit_id'])}
            for site in q.get('additional_mounts',[]):
                uid=site.get('unit_id')
                if uid not in reader_data['units'] or uid in seen:raise ValueError(q['id']+': invalid related unit')
                if not site.get('anchor_text') or site.get('placement',{}).get('unit_id')!=uid:raise ValueError(q['id']+': compile related anchor')
                seen.add(uid)
    edition_questions = {name: {q['id']: q for q in rows} for name, rows in editions.items()}
    paper_questions = paper_edition_questions(editions,views,teacher_profile)
    allquestions = questions + extra + views
    for view in views:
        if view['unit_id'] not in reader_data['units']:
            raise ValueError(view['id'] + ': unknown paper view reader unit')
    ps=read(papers);validated=validate_papers(allquestions,ps)
    validate_paper_parent_exclusion(allquestions,validated)
    for paper in validated:
        edition=paper.get('edition','full')
        if edition not in paper_questions:raise ValueError('paper refers to unknown edition')
        if any(x['question_id'] not in paper_questions[edition] for x in paper['items']):
            raise ValueError('paper is outside its edition')
    used_view_ids = {x['question_id'] for paper in validated for x in paper['items']} & view_ids
    managed_media = {'practice-media', 'practice-media-data.js', 'textbook-animation-data.js'}
    if media:
        media = Path(media).resolve()
        manifest_bytes = media.read_bytes()
        media_manifest = json.loads(manifest_bytes)
        published_questions = {q['id']: q for rows in editions.values() for q in rows}
        published_questions.update({q['id']:q for q in views if q['id'] in used_view_ids})
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
    publication_scopes = [*editions.values(), *[list(rows.values()) for rows in paper_questions.values()]]
    if not originals and any(q['source']['kind']=='past_exam' for rows in publication_scopes for q in rows):
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
    for rows in publication_scopes:
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
    textbook_path=book/'assets/textbook.pdf'
    textbook_hash=hashlib.sha256(textbook_path.read_bytes()).hexdigest() if textbook_path.is_file() else None
    for rows in publication_scopes:
        for q in rows:
            if q['source']['kind']=='generated_variant' and q.get('source_file_sha256') in hash_paths:q['public_source_path']=hash_paths[q['source_file_sha256']]
            elif q['source']['kind']=='generated_variant' and textbook_hash and q.get('source_file_sha256')==textbook_hash:q['public_source_path']='教材/assets/textbook.pdf'
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
        edition = paper.get('edition', 'full')
        byid = paper_questions[edition]
        qs=[{**byid[x['question_id']],'points':x['points'],'section':x.get('section','')} for x in paper['items']]
        for q in qs:
            q['public_textbook_path']='教材/index.html?practice='+edition+'#/'+q.get('mount_unit_id',q['unit_id'])
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
    date_label=(html.escape(release_date)+' · ') if release_date else ''
    index=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>复变函数 A · 习题与试卷</title><link rel="stylesheet" href="assets/practice.css"><body class="practice-page"><main class="practice-shell"><header class="practice-hero"><h1>复变函数 A<br>习题与试卷</h1><p>基础版用于建立共同核心，完整版扩展不同考法与难度。已保存的原始试卷与新编试卷集中归档。</p><p>{date_label}本地离线阅读 · 题目与解析同源</p></header><section class="practice-q"><h2>开始学习</h2>{linklist(links[:len(editions)*3])}<p class="practice-note">展开习题后可以单独展开解析。必会/可能考/罕见是有依据的备考优先级，不表示下一次考试的确定概率。</p></section><section class="practice-q"><h2>新编与重组试卷</h2><p>基础版正式训练卷 {base_papers} 张；完整版含基础卷及扩展卷，共 {full_papers} 张。定制卷按实际配置单列。</p>{linklist(formal_links)}<h3>保留的结构参照试制卷</h3><p>以下试制卷的题目经过数学核对，但任务链不与母卷逐位完全对应，不计入正式完全拟合式卷。</p>{linklist(trial_links)}<p>试卷中的训练分值、限时建议与原卷官方信息分开；目前没有真人学习收益或未来押题率证明。</p></section><section class="practice-q"><h2>往年原卷</h2>{linklist(original_links)}<p>复变函数A是本次选题主体；B卷与数学专业复分析分别存档，未混入A卷频次依据。</p></section><section class="practice-q"><h2>制作与核验</h2><p><a href="制作说明.html">阅读制作说明、来源与已知边界</a></p></section></main></body></html>'''
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
    if paper_catalog:
        for view in views:
            published = paper_questions['full'][view['id']]
            if 'public_source_path' in published:view['public_source_path']=published['public_source_path']
            view['public_textbook_path']='教材/index.html?practice=full#/'+view['unit_id']
        export_reviewed_catalog(views,paper_catalog.parent,out,'paper-catalog.json')
        validate_paper_view_excerpts(questions+extra,views,out)
    if profile:
        write(out / 'teacher-profile.json', teacher_profile)
        if additions:
            export_reviewed_catalog(extra, Path(additions).resolve().parent, out, 'teacher-additions.json')
    write(out / 'papers.json', validated)
    report={'schema_version':'practice-delivery-v1','editions':summary,'papers':len(validated),'formal_papers':full_papers,'basic_papers':base_papers,'reference_trial_papers':len(trial_ids),'original_files':len(originals_out),'originals':originals_out,'semantic_scope':'Questions have recorded independent solutions and final comparison; no future-hit/student-effect claim.','source_reader':str(reader),'readiness':'candidate_pending_render_checks'}
    report.update(paper_views=len(views),paper_views_used=len(used_view_ids),paper_view_new_source_materials=0,release_date=release_date)
    write(out/'build-report.json',report)
    from tools.build_practice_animation_gallery import build_gallery
    build_gallery(out)
    from tools.build_study_hubs import build_hubs
    from tools.build_study_entry import build_entry
    units=[]
    for uid,unit in reader_data['units'].items():
        chapter=uid.split('-')[0]
        kind='chapter_overview' if uid==chapter else ('section_overview' if any(other.startswith(uid+'-') for other in reader_data['units']) else 'knowledge_unit')
        units.append({'unit_id':uid,'title':unit.get('title',uid),'chapter_id':chapter,'chapter_title':reader_data['units'].get(chapter,{}).get('title',chapter),'kind':kind})
    write(out/'course-units.json',units)
    build_hubs(out,units,list(edition_questions['full'].values()),validated)
    build_entry(out,course_prefix='')
    visible_questions={q['id']:q for rows in editions.values() for q in rows}
    bindings={scene:[q['id'] for q in visible_questions.values() if scene in q.get('spatial_scenes',[])] for scene in ('sphere','log')}
    if spatial or any(bindings.values()):
        from tools.build_practice_spatial import build_spatial
        build_spatial(out,bindings)
        page=(out/'三维图解.html').read_text(encoding='utf-8')
        for ident in set(visible_questions)-set(edition_questions['full']):
            page=page.replace('完整版-习题集.html#practice-'+ident,'定制版-习题集.html#practice-'+ident)
        (out/'三维图解.html').write_text(page,encoding='utf-8')
        for scene in bindings:shutil.copy2(out/'assets'/('practice-spatial-'+scene+'.svg'),book/'assets'/('practice-spatial-'+scene+'.svg'))
        write(out/'spatial-bindings.json',bindings)
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ['catalog','papers','reader','out']:p.add_argument('--'+k,type=Path,required=True)
    for k in ['originals','profile','additions','media','spatial','paper-catalog']:p.add_argument('--'+k,type=Path)
    p.add_argument('--release-date',help='Explicit release date (YYYY-MM-DD); omit rather than invent a date.')
    a=p.parse_args();r=build(a.catalog,a.papers,a.reader,a.out,originals=a.originals,profile=a.profile,additions=a.additions,media=a.media,spatial=a.spatial,paper_catalog=a.paper_catalog,release_date=a.release_date);print(json.dumps(r['editions'],ensure_ascii=False))
if __name__=='__main__':main()
