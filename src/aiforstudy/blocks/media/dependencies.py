"""Explicit, separate generation and review component identities for each resource."""
import importlib.metadata
import json
from functools import lru_cache
from pathlib import Path
import shutil
import subprocess
from ...core.digest import digest_file,digest_of

@lru_cache(maxsize=128)
def _fingerprint(path,mtime,size): return digest_file(Path(path))

def file_identity(path):
    if not path or not Path(path).is_file(): return 'missing'
    p=Path(path);stat=p.stat();return _fingerprint(str(p.resolve()),stat.st_mtime_ns,stat.st_size)

def component_identity(paths):
    root=Path(__file__).resolve().parents[2]
    result={}
    for name in paths:
        path=root/name
        result[name]=({p.relative_to(path).as_posix():file_identity(p)
                       for p in sorted(path.rglob('*')) if p.is_file() and p.suffix in ('.py','.md','.json','.js','.cjs','.css') and '__pycache__' not in p.parts}
                      if path.is_dir() else file_identity(path))
    return result

def model_role_identity(ctx, role):
    from ...providers import gateway,transport
    from ...providers.local_compatible import endpoint_identity
    from ...pipeline import gateway_client
    from ...pipeline.runtime_dependencies import TRANSPORT_COMPONENTS
    config=ctx.workspace.config_json('models.json') if hasattr(ctx.workspace,'config_json') else {}
    provider=getattr(getattr(ctx.services,'providers',None),'llm',None)
    if callable(getattr(provider,'for_role',None)):
        provider=provider.for_role(role)
    selected=getattr(provider,'cache_identity',None)
    role_config=(config.get('roles') or {}).get(role)
    settings=role_config or {}
    endpoint=(selected or {}).get('endpoint') or getattr(provider,'base_url',None) or settings.get('base_url') or config.get('base_url')
    return {'role':role_config,'provider':(selected or {}).get('provider') or settings.get('provider',config.get('provider')),
            'endpoint':endpoint_identity(endpoint) if endpoint else None,
            'transport_identity':selected,
            'transport_type':type(provider).__module__+'.'+type(provider).__qualname__,
            'gateway':file_identity(gateway.__file__),'transport':file_identity(transport.__file__),
            'transport_components':component_identity(TRANSPORT_COMPONENTS),
            'client':file_identity(gateway_client.__file__)}

def resource_dependencies(ctx,request,capabilities):
    from ...checks.offline import _browser_executable
    from ...pipeline.runtime_dependencies import (
        STATIC_GENERATION_COMPONENTS, STATIC_REVIEW_COMPONENTS,
        CODE_GENERATION_COMPONENTS, CODE_REVIEW_COMPONENTS, CODE_WORKER_COMPONENTS, CODE_NARRATION_COMPONENTS,
        media_prompt_components, LIBRARY_ADAPTER_COMPONENTS,
    )
    code_backend=(capabilities.get('code_generation')=='GeneratedMediaCodeV1'
                  and request['kind'] in ('drawing','animation'))
    browser=_browser_executable(Path(ctx.workspace.root));font='missing'
    if shutil.which('fc-match'):
        try:
            paths=subprocess.check_output(['fc-match','-s','-f','%{file}\n','sans-serif'],timeout=5,text=True)
            font={path:file_identity(path) for path in paths.splitlines() if path}
        except (OSError,subprocess.SubprocessError): pass
    selected=set() if code_backend else {ref['asset_id'] for ref in request.get('source_refs') or [] if ref.get('kind')=='asset'}
    assets=[row for rows in (capabilities.get('source_assets') or {}).values() if isinstance(rows,list)
            for row in rows if row.get('asset_id') in selected]
    adapters={}
    from ...pipeline.stage_io import _repo_root
    if request['kind']=='source_image':
        adapters['source_crop']=file_identity(_repo_root()/'blocks/render/build.py')
    if not code_backend and request.get('library_id'):
        root=_repo_root()
        paths=[]
        for name in LIBRARY_ADAPTER_COMPONENTS:
            path=root/name
            paths.extend(sorted(path.rglob('*.py')) if path.is_dir() else [path])
        adapters['library']={p.relative_to(root).as_posix():file_identity(p) for p in paths}
    if code_backend:
        from ..visual.worker import resolve_worker_dir
        from ...providers.capabilities import resolve_manim_interpreter
        worker=resolve_worker_dir(workspace_root=ctx.workspace.root)
        # Both Manim stills and animations use the same pinned-font helpers.
        worker_components=CODE_WORKER_COMPONENTS+('scene_builder.py',)
        adapters['code_worker']={name:file_identity(worker/name) for name in worker_components}
        adapters['render_lock']=file_identity(_repo_root()/'requirements-manim.lock')
        interpreter=resolve_manim_interpreter(Path(ctx.workspace.root))
        adapters['interpreter']=file_identity(interpreter)
        if interpreter:
            try:
                output=subprocess.check_output([str(interpreter),'-c',
                    'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ("manim","sympy","numpy","Pillow")}))'],
                    timeout=10,text=True)
                adapters['installed_render_packages']=json.loads(output)
            except (OSError,ValueError,subprocess.SubprocessError):
                adapters['installed_render_packages']='unavailable'
    if code_backend and request['kind'] == 'animation':
        # Config contains references/settings only; never read referenced secrets.
        path=Path(ctx.workspace.root)/'config'/'narration.json'
        adapters['narration_config'] = file_identity(path)
        from .local_narration import narration_dependency_identity
        adapters['narration_effective'] = narration_dependency_identity(json.loads(path.read_text()) if path.is_file() else None)
    generation_components=CODE_GENERATION_COMPONENTS if code_backend else STATIC_GENERATION_COMPONENTS
    if code_backend and request['kind']=='animation': generation_components+=CODE_NARRATION_COMPONENTS
    review_components=CODE_REVIEW_COMPONENTS if code_backend else STATIC_REVIEW_COMPONENTS
    if code_backend: generation_components+=media_prompt_components(request['kind'],'code')
    review_components+=media_prompt_components(request['kind'],'review')
    generation={'components':component_identity(generation_components),'adapters':adapters,
        'request_hash':digest_of(request),'browser':file_identity(browser),'font':font,
        'dependencies':{name:importlib.metadata.version(name) for name in
                        (('Pillow','sympy') if code_backend else ('Pillow','matplotlib','sympy'))},
        'source_assets':assets,'planner_role':model_role_identity(ctx,'figures')}
    review={'components':component_identity(review_components),'judge_role':model_role_identity(ctx,'judge'),
        'reader_surface':file_identity(_repo_root()/'blocks/render/assets/media-surface.css'),
        'request_criteria':request.get('acceptance_criteria'),'image_input':'actual-image-bytes-v1',
        'browser':file_identity(browser),'font':font}
    return {'generation_dependency_hash':digest_of(generation),'review_dependency_hash':digest_of(review)}
