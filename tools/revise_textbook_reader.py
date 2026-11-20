"""Project authored, textbook-grounded lessons without losing their sources."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.upgrade_study_reader import load_reader_data,restore_empty_units
from aiforstudy.blocks.render.layout import layout_from_reader_media
from aiforstudy.core.digest import digest_of
from aiforstudy.blocks.content.teaching_projection import compile_reader_roles, bind_teaching_presentation
from blocks.render.build import write_reader_site
from tools.prepare_support_corrections import correct_support_document,correct_global_concepts
from tools.prepare_textbook_display import project_textbook_display,correct_display_document,correct_display_concepts
from tools.prepare_material_display import correct_material_display

MARKER=re.compile(r'\[\[TEACHING_(VISUAL|MEDIA):([^\]]+)\]\]')
RESULT_MARKER='[[TEXTBOOK_RESULT:{id}]]'


def numbered_statements(source_text):
    statements=[]
    for number,line in enumerate(source_text.splitlines(),1):
        match=re.match(r'^\s*((?:定义|定理|推论|命题)\s*\d+)\s*(.*)',line)
        if match and not re.match(r'(?:由|提供|的)',match[2]):
            statements.append({'line':number,'label':match[1]})
    return statements


def lesson_with_conclusions(lesson):
    """Place full statements in the reading flow; never hide them with proofs."""
    lesson=deepcopy(lesson)
    body=lesson['body_markdown'];placements={}
    for item in lesson.get('knowledge_coverage',[]):item['excerpts'].sort(key=body.index)
    coverage={c['id']:c['excerpts'] for c in lesson.get('knowledge_coverage',[])}
    paragraph_ids={}
    for ident,excerpts in coverage.items():
        for excerpt in excerpts:paragraph_ids.setdefault(excerpt,[]).append(ident)
    for result in lesson.get('conclusions',[]):
        if result['id'] in coverage:continue
        anchor=result.get('after','')
        if not anchor or body.split('\n\n').count(anchor)!=1:
            raise ValueError('结论缺少唯一正文位置：'+str(result.get('id')))
        placements.setdefault(anchor,[]).append(RESULT_MARKER.format(id=result['id']))
    if placements or paragraph_ids:
        parts=[]
        for paragraph in body.split('\n\n'):
            if paragraph in paragraph_ids:
                parts.extend(['[[TEXTBOOK_INTEGRATED:'+ '|'.join(paragraph_ids[paragraph])+']]',paragraph,'[[TEXTBOOK_END_INTEGRATED]]'])
            else:parts.append(paragraph)
            parts.extend(placements.get(paragraph,[]))
        lesson['body_markdown']='\n\n'.join(parts)
    return lesson


def source_file(parse_root,relative):
    parse_root=Path(parse_root).resolve()
    for path in [(parse_root/relative).resolve(),(ROOT/relative).resolve()]:
        if path.is_relative_to(parse_root) and path.is_file():return path
    raise ValueError('教材来源无效：'+str(relative))


def recover_local_sources(data,archive=None):
    if archive and Path(archive).is_file():
        restore_empty_units(data,archive);return
    # The imported original run is also a valid source if the downloaded ZIP
    # has since been moved. Read actual revisions; never manufacture a source.
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.core.review_mode import review_mode
    run_root=ROOT/'data/books'/data.get('book','')/'runs'
    for uid,unit in data['units'].items():
        reader=unit.get('reader_document',{})
        if any(b.get('group')=='body_markdown' and b.get('markdown','').strip() for b in reader.get('blocks',[])):continue
        paths=sorted(run_root.glob('*/work/products/content/'+uid+'/revisions/*.json'),reverse=True)
        for path in paths:
            c=json.loads(path.read_text())
            if not c.get('body_markdown','').strip() or any(k in c for k in ['role_task','original_response','errors']):continue
            with review_mode('balanced'):
                unit['reader_document']=project_reader(c,reader.get('reference_catalog',{'entries':[]})).as_dict()
            unit['study_recovery']={'unit_id':uid,'revision_path':str(path.relative_to(ROOT)),
                'source_sha256':'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest(),
                'body_characters':len(c['body_markdown'])}
            break


def validate_lessons(data,lessons,parse_root):
    parse_root=Path(parse_root).resolve();errors=[]
    for uid,lesson in lessons.items():
        if uid not in data['units'] or lesson.get('unit_id')!=uid:
            errors.append(uid+': 单元身份不匹配');continue
        try:source_text=source_file(parse_root,lesson.get('source_path','')).read_text()
        except ValueError:source_text='';errors.append(uid+': 教材来源无效')
        body=lesson.get('body_markdown','')
        if not isinstance(body,str) or not body.strip():errors.append(uid+': 正文为空');continue
        matches=MARKER.findall(body);declared=[]
        for kind,key in [('VISUAL','visuals'),('MEDIA','media')]:
            for plan in lesson.get(key,[]):
                ident=plan.get('id');declared.append((kind,ident))
                marker='[[TEACHING_'+kind+':'+str(ident)+']]'
                if body.count(marker)!=1:errors.append(uid+': 标记不唯一 '+marker)
                if not plan.get('purpose') or not plan.get('conclusion'):errors.append(uid+': 缺少教学任务或观察解释 '+str(ident))
                if kind=='VISUAL':
                    title_key='title' if plan.get('presentation')=='text-led-figure' else 'model_title'
                    if not all(plan.get(x) for x in ['kind',title_key,'action','observation']):errors.append(uid+': 图像任务不完整 '+str(ident))
                    if plan.get('presentation')=='text-led-figure' and plan.get('kind') not in {'conjugate','polar','vectors','distance','multiplication'}:errors.append(uid+': 正文图解类型不存在 '+str(ident))
                if kind=='MEDIA':
                    ids={a['request_id'] for a in data.get('animations',{}).get(uid,[])}
                    if plan.get('request_id') not in ids:errors.append(uid+': 动画身份无效 '+str(ident))
        if sorted(matches)!=sorted(declared):errors.append(uid+': 正文标记与教学计划不一致')
        result_ids={c.get('id') for c in lesson.get('conclusions',[])+lesson.get('supplementary_conclusions',[])}
        covered_ids=set()
        for coverage in lesson.get('knowledge_coverage',[]):
            ident=coverage.get('id');excerpts=coverage.get('excerpts')
            if ident not in result_ids or ident in covered_ids or not isinstance(excerpts,list) or not excerpts:
                errors.append(uid+': 教材覆盖身份或段落无效 '+str(ident));continue
            covered_ids.add(ident)
            if any(not isinstance(e,str) or not e.strip() or body.split('\n\n').count(e)!=1 or e.startswith('[[') for e in excerpts) or len(set(excerpts))!=len(excerpts):
                errors.append(uid+': 教材覆盖段落缺失或不唯一 '+str(ident))
        ids=set();lines=source_text.splitlines(keepends=True)
        for result in lesson.get('conclusions',[])+lesson.get('supplementary_conclusions',[]):
            ident=result.get('id')
            if not ident or ident in ids:errors.append(uid+': 结论身份缺失或重复')
            ids.add(ident)
            if not result.get('title') or not result.get('statement_markdown','').strip():errors.append(uid+': 结论内容为空 '+str(ident))
            start,end=result.get('source_start_line',0),result.get('source_end_line',0)
            quote=result.get('source_quote','')
            if not isinstance(start,int) or not isinstance(end,int) or not 1<=start<=end<=len(lines) or not quote or quote not in ''.join(lines[start-1:end]):
                errors.append(uid+': 结论教材依据无效 '+str(ident))
            if ident not in covered_ids and (body.split('\n\n').count(result.get('after',''))!=1 or MARKER.fullmatch(result.get('after',''))):
                errors.append(uid+': 结论正文位置无效 '+str(ident))
        if 'conclusions' in lesson:
            for statement in numbered_statements(source_text):
                if not any(isinstance(c.get('source_start_line'),int) and isinstance(c.get('source_end_line'),int)
                    and c['source_start_line']<=statement['line']<=c['source_end_line'] for c in lesson['conclusions']):
                    errors.append(uid+': 教材结论遗漏 '+statement['label']+'（原文第'+str(statement['line'])+'行）')
    return errors


def authored_presentation(document, lesson):
    declarations = lesson.get('reading_roles', [])
    seen = set()
    for row in declarations:
        key = (row.get('reader_pointer'), row.get('quote'))
        if key in seen or key[0] != '/body_markdown' or not key[1] or row.get('reader_role') not in ('proof', 'worked_example'):
            raise ValueError('正文折叠声明缺失、重复或无效')
        seen.add(key)
    optional, _ = compile_reader_roles(document, declarations)
    main, _ = compile_reader_roles(document, [{'reader_pointer':'/body_markdown', 'reader_role':'main'}])
    for coverage in lesson.get('knowledge_coverage', []):
        for excerpt in coverage['excerpts']:
            _, selected = compile_reader_roles(document, [{'reader_pointer':'/body_markdown','quote':excerpt}])
            if any(key in optional['roles'] for key in selected[0]):
                raise ValueError('教材覆盖段落不可折叠：' + coverage['id'])
    main['roles'].update(optional['roles'])
    for region in main['regions']:
        region['reader_role'] = main['roles'][region['block_id']]
    return bind_teaching_presentation(document, main)


def apply_lessons(data,lessons,parse_root=None):
    result=deepcopy(data)
    result['study_original_concepts']=deepcopy(data.get('concepts',{}))
    result['concepts'],global_report=correct_global_concepts(data.get('concepts',{}))
    result['study_global_support_corrections']=global_report
    result['concepts'],format_report=correct_display_concepts(result['concepts'])
    result['study_global_display_corrections']=format_report
    result['source_materials'],result['study_material_display_corrections']=correct_material_display(data.get('source_materials',{}))
    result['study_reader']={'version':'2.0','theme':'light','visual_policy':'authored','mode':'textbook_coherent'}
    for uid,lesson in lessons.items():
        lesson=lesson_with_conclusions(lesson)
        unit=result['units'][uid]
        unit['title']=lesson.get('title',unit.get('title',''))
        original=deepcopy(unit.get('study_source_document') or unit['reader_document'])
        unit['study_source_document']=original
        unit['study_source_media']=deepcopy(unit.get('reader_media',[]))
        unit['study_lesson']=deepcopy(lesson)
        document=deepcopy(original);body=lesson['body_markdown'];found=False
        for block in document['blocks']:
            if block.get('group')=='examples':block['collapsed']=True
            if block.get('group')=='title':
                title=unit['title'];block.update(raw_text=title,visible_text=title,markdown=title,source_map=list(range(len(title))),marker_spans=[])
            if block.get('group')!='body_markdown':continue
            found=True;block.update(raw_text=body,visible_text=body,markdown=body,
                source_map=list(range(len(body))),marker_spans=[],
                block_id=digest_of({'unit':uid,'revision':'textbook-coherent-2','body':body}))
        if not found:
            document['blocks'].append({'block_id':digest_of({'unit':uid,'body':body}),
                'owner_id':uid,'json_pointer':'/body_markdown','raw_text':body,'visible_text':body,
                'markdown':body,'source_map':list(range(len(body))),'marker_spans':[],
                'group':'body_markdown','field_label':'讲解','anchor':'study-body-'+uid,'collapsed':False})
        document['content_hash']=digest_of({'original':original.get('content_hash'),'lesson':lesson})
        document['required_block_ids']=[b['block_id'] for b in document['blocks']]
        document,support_report=correct_support_document(document,uid)
        unit['study_support_corrections']=support_report
        document,display_report=correct_display_document(document,uid)
        unit['study_display_corrections']=display_report
        if support_report.get('signature_mismatches') or display_report.get('signature_mismatches'):raise ValueError('查阅资料订正签名不符：'+uid)
        unit['reader_document']=document;unit['reader_media']=[]
        unit['teaching_presentation']=authored_presentation(document,lesson)
        unit['reader_layout']=layout_from_reader_media(document,[])
        unit['study_provenance']={'source_path':lesson['source_path'],
            'original_document_hash':original.get('content_hash'),
            'lesson_sha256':hashlib.sha256(body.encode()).hexdigest(),
            'method':'按教材顺序重新讲解；原解析与证明例题作为查阅资料保留'}
        if parse_root:
            unit['study_textbook_original']=source_file(parse_root,lesson['source_path']).read_text()
            unit['study_textbook_display'],unit['study_textbook_format_corrections']=project_textbook_display(unit['study_textbook_original'],uid)
            if (ROOT/'data/raw/复变函数.pdf').is_file():unit['study_textbook_pdf']='assets/textbook.pdf'
        unit['review_outcome']={'status':'unverified','reasons':['正文已按教材重讲，未冒充旧版审核结论']}
    def update_titles(value):
        if isinstance(value,list):
            for child in value:update_titles(child)
        elif isinstance(value,dict):
            ident=value.get('id') or value.get('unit_id')
            if ident in lessons and 'title' in value:value['title']=lessons[ident]['title']
            for key,child in value.items():
                if isinstance(child,(list,dict)):update_titles(child)
    if 'toc' in result:update_titles(result['toc'])
    return result


def revise(source,output,manifest_paths,*,parse_root,archive=None,neural_results=None,conclusion_paths=None,transform_tables=None,course_library=None):
    source,output=Path(source).resolve(),Path(output).resolve()
    if source==output:raise ValueError('必须输出独立版本')
    data=load_reader_data(source);recover_local_sources(data,archive)
    lessons={}
    for path in manifest_paths:
        incoming=json.loads(Path(path).read_text())['units']
        if set(incoming)&set(lessons):raise ValueError('重复单元')
        lessons.update(incoming)
    inventory={}
    if conclusion_paths is None:
        conclusion_paths=sorted((ROOT/'data/study-revision-20261029').glob('conclusions-*.json'))
    for path in conclusion_paths or []:
        incoming=json.loads(Path(path).read_text())['units']
        if set(incoming)&set(inventory):raise ValueError('重复结论单元')
        inventory.update(incoming)
    if conclusion_paths:
        if set(inventory)!=set(lessons):raise ValueError('教材结论核对范围不完整')
        for uid,entry in inventory.items():
            if source_file(parse_root,entry['source_path'])!=source_file(parse_root,lessons[uid]['source_path']):raise ValueError('结论教材身份不匹配：'+uid)
            lessons[uid]['conclusions']=entry['conclusions']
            lessons[uid]['supplementary_conclusions']=entry.get('supplementary_conclusions',[])
    table_path=Path(transform_tables) if transform_tables else ROOT/'data/study-revision-20261029/transform-tables.json'
    table_images=[]
    if table_path.is_file():
        table_data=json.loads(table_path.read_text());target=lessons['CH09-S03-SS03']
        target['transform_tables']=table_data['tables'];target['table_conventions_markdown']=table_data.get('conventions_markdown','');target['table_source_pages']=[];row_ids=set()
        for table in table_data['tables']:
            for row in table['rows']:
                if not row.get('id') or row['id'] in row_ids or not row.get('statement_markdown') or not row.get('source_pdf_page'):
                    raise ValueError('教材原表行内容或来源无效')
                row_ids.add(row['id'])
        for page in table_data.get('page_images',[]):
            path=(ROOT/page['source_path']).resolve()
            if not path.is_relative_to(ROOT) or not path.is_file():raise ValueError('原表页图像无效')
            relative='assets/textbook-tables/'+path.name
            table_images.append((path,output/relative))
            target['table_source_pages'].append({**page,'url':relative})
    errors=validate_lessons(data,lessons,parse_root)
    if errors:raise ValueError('\n'.join(errors))
    if set(lessons)!=set(data['units']):raise ValueError('重写单元范围不完整')
    revised=apply_lessons(data,lessons,parse_root)
    ready={}
    if neural_results and Path(neural_results).is_file():
        for item in json.loads(Path(neural_results).read_text()).get('animations',[]):
            ready[(item['unit_id'],item['request_id'])]=item['animation']
    selected={uid:{p['request_id'] for p in lesson.get('media',[])} for uid,lesson in lessons.items()}
    revised['animations']={};missing=[]
    for uid,ids in selected.items():
        for ident in ids:
            row=ready.get((uid,ident))
            if not row:missing.append(uid+'/'+ident);continue
            revised['animations'].setdefault(uid,[]).append(row)
    if missing:raise ValueError('选中的自然配音尚未完成：'+','.join(missing))
    from aiforstudy.core.course_library import project_library
    from aiforstudy.blocks.render.source_materials import copy_source_assets, source_asset_names
    bundle = Path(course_library) if course_library else Path(parse_root) / 'course-library'
    if course_library or (bundle / 'current.json').exists():
        course_paths = project_library(revised, bundle)
        course_names = source_asset_names(course_paths)
        copy_source_assets(course_paths, course_names, output)
        revised.setdefault('source_assets', {}).update(course_names)
    write_reader_site(revised,output,book='复变函数 · 跟着教材学')
    if (ROOT/'data/raw/复变函数.pdf').is_file():shutil.copy2(ROOT/'data/raw/复变函数.pdf',output/'assets/textbook.pdf')
    for original,destination in table_images:
        destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,destination)
    report={'schema_version':'CoherentTextbookDeliveryV1','units':len(lessons),
        'source':str(source),'rewritten_body_characters':sum(len(l['body_markdown']) for l in lessons.values()),
        'planned_visuals':sum(len(l.get('visuals',[])) for l in lessons.values()),
        'planned_animations':sum(len(l.get('media',[])) for l in lessons.values()),
        'neural_animations':sum(len(x) for x in revised['animations'].values()),
        'visible_textbook_conclusions':sum(len(l.get('conclusions',[])) for l in lessons.values()),
        'folded_exercise_conclusions':0,
        'visible_reusable_conclusions':sum(len(l.get('supplementary_conclusions',[])) for l in lessons.values()),
        'restored_transform_table_rows':sum(len(t['rows']) for l in lessons.values() for t in l.get('transform_tables',[])),
        'automatic_keyword_figures':False,'source_preserved':True,'errors':[]}
    if revised.get('course_library'):
        report['course_library'] = revised['course_library']['summary']
        report['course_library_snapshot'] = revised['course_library']['snapshot_id']
    (output/'revision-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--manifest',nargs='+',required=True,type=Path)
    p.add_argument('--parse-root',required=True,type=Path);p.add_argument('--archive',type=Path)
    p.add_argument('--neural-results',type=Path);p.add_argument('--conclusions',nargs='+',type=Path);p.add_argument('--transform-tables',type=Path);a=p.parse_args()
    print(json.dumps(revise(a.source,a.output,a.manifest,parse_root=a.parse_root,archive=a.archive,
        neural_results=a.neural_results,conclusion_paths=a.conclusions,transform_tables=a.transform_tables),ensure_ascii=False,indent=2))
