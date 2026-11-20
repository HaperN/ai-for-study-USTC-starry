"""Explicit offline integration: no providers, real isolated Manim draft/final."""
import json
from pathlib import Path
from aiforstudy.blocks.visual.compiler import compile_scene
from aiforstudy.blocks.visual.manim_adapter import default_services,limits_for_profile,render_scene
from aiforstudy.blocks.audit.evidence import resolve_json_pointer
from aiforstudy.core.digest import digest_of
from test_animation_teaching_bindings import derivative_animation_case,ROOT

import datetime
out=ROOT.parent/'.superpowers/sdd/教学质量内核重构方案-20261008/Q08-render-evidence'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
out.mkdir(parents=True,exist_ok=True)
context,spec=derivative_animation_case()
spec['objects'].extend([
    {'object_id':'original','kind':'formula','model_id':'M','math_object_id':'f'},
    {'object_id':'label','kind':'label','text':'左侧是求导结果，右侧保留原式','space':'math2d','at':[0,-2]}])
spec['steps'][0]['object_ids'].extend(['original','label'])
spec['steps'][0]['actions'].insert(1,{'action':'reveal','object_id':'original'})
spec['steps'][0]['actions'].append({'action':'reveal','object_id':'label'})
for profile in ('draft','final'):
    scene=compile_scene(spec,profile,math_context=context)
    services=default_services(workspace_root=ROOT,scratch_root=out/'scratch')
    result=render_scene(scene,limits_for_profile(services.worker_dir,profile),services,spec=spec,profile=profile,
                        output_dir=out/profile,math_context=context)
    (out/(profile+'-result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/(profile+'-compiled.py')).write_text(scene.source,encoding='utf-8')
    print(profile,result['status'],result['technical_report'].get('frame_checks',{}).get('failed'),flush=True)
    if result['status'] not in ('needs_review','fulfilled'):
        print(json.dumps(result['technical_report'],ensure_ascii=False,indent=2),flush=True)
        raise SystemExit(1)
    from aiforstudy.blocks.visual.result import parse_animation_result
    from aiforstudy.blocks.visual.frame_check import select_frames
    parsed=parse_animation_result(result)
    selected,dropped=select_frames(spec,parsed.as_dict(),max_images=8)
    assert {f['phase'] for f in selected} >= {'before','after'}
    assert '\ns1\n' in (out/profile/'captions.vtt').read_text()
    assert result['math_report']['status']=='pass'
    assert result['technical_report']['ffmpeg_decode_ok'] is True
    assert result['technical_report']['size_ok'] is True
    assert result['technical_report']['duration_ok'] is True
    if profile=='final':
        request=context.request;content=context._data()['content']
        row={'state':'needs_review','request_id':request['request_id'],'request_hash':digest_of(request),
             'criteria_digest':request['criteria_digest'],'kind':'animation','source_dir':str(out/profile),
             'resources':result['resources'],'animation':result,'visual_spec':spec,
             'approved_dependencies':[{**dep,'value':resolve_json_pointer(content,dep['pointer'])[1]} for dep in request['dependency_paths']]}
        (out/'final-validation-input.json').write_text(json.dumps({'request':request,'result':row},ensure_ascii=False,indent=2),encoding='utf-8')
print(out,flush=True)
