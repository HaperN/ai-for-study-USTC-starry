"""Publication gate for question-bound animation and SVG evidence."""
from pathlib import Path
import hashlib,json,shutil,re,math,base64
from .catalog import content_digest

PUBLIC_MEDIA_SUFFIXES = {
    'svg': {'.svg'},
    'mp4': {'.mp4'},
    'picture': {'.mp4'},
    'audio': {'.mp3', '.wav', '.m4a', '.ogg', '.aac'},
    'poster': {'.png', '.jpg', '.jpeg', '.webp'},
    'captions': {'.vtt'},
    'transcript': {'.txt'},
}
_DISPLAY_FIELDS = (
    'id', 'kind', 'question_id', 'unit_id', 'request_id', 'title', 'purpose',
    'captions_text', 'transcript_text', 'svg_alt', 'static_caption',
)
_STEP_TEXT_FIELDS = (
    'step_id', 'display_title', 'title', 'label', 'display_markdown',
    'caption_tex', 'caption', 'text',
)


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def media_spec_digest(item):
    """Bind the reviewed teaching text, timeline and playback, as well as bytes."""
    value={k:v for k,v in item.items() if k not in ('review','review_evidence')}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def _file(root,row):
    relative = row.get('path')
    if (not isinstance(relative, str) or '\\' in relative
            or any(part in ('', '.', '..') for part in relative.split('/'))):
        raise ValueError('media path escape')
    if relative == 'manifest.json':
        raise ValueError('media resource collides with the private manifest')
    path=(root/relative).resolve()
    if not path.is_relative_to(root):raise ValueError('media path escape')
    if not path.is_file() or _sha(path)!=row.get('sha256'):raise ValueError('media file hash mismatch')
    return path

def validate_media_manifest(manifest, questions, root):
    root=Path(root).resolve();byid={q['id']:q for q in questions};seen=set()
    if manifest.get('schema_version')!='PracticeMediaV1':raise ValueError('unsupported media manifest')
    if not isinstance(manifest.get('items'), list):raise ValueError('media manifest needs an item list')
    for item in manifest['items']:
        ident=item.get('id');qid=item.get('question_id')
        if not isinstance(ident,str) or not re.fullmatch('[A-Za-z0-9_-]+',ident) or ident in seen:raise ValueError('invalid or duplicate media identity')
        seen.add(ident)
        if qid not in byid or item.get('question_content_sha256')!=content_digest(byid[qid]):raise ValueError('media question binding mismatch')
        if item.get('kind') not in ('svg','animation') or not all(
            isinstance(item.get(key), str) and item[key].strip() for key in ('title', 'purpose')
        ):
            raise ValueError('media needs an explicit teaching purpose')
        review=item.get('review',{})
        if review.get('status')!='accepted' or review.get('question_content_sha256')!=item['question_content_sha256'] or not review.get('math_checks') or not review.get('visual_checks'):raise ValueError('media review incomplete')
        files=item.get('files',{});hashes={};names=set()
        for kind,row in files.items():
            path=_file(root,row)
            name=Path(row['path']).name
            if name in names:raise ValueError('media resource filename collision')
            if kind in PUBLIC_MEDIA_SUFFIXES and not public_media_path(name,kind):raise ValueError('unsupported public media filename')
            names.add(name)
            hashes[kind]=row['sha256']
        if review.get('asset_hashes')!=hashes:raise ValueError('media review asset hash mismatch')
        if item['kind']=='svg' and 'svg' not in files:raise ValueError('SVG attachment missing vector source')
        if item['kind']=='animation':
            if item.get('engine')!='manim' or not {'mp4','poster','captions','transcript'}<=set(files) or not item.get('steps'):raise ValueError('animation missing Manim video or timeline')
            prev=0
            for step in item['steps']:
                start,end=step.get('start'),step.get('end')
                if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (start,end)) or start<prev-.05 or end<=start:raise ValueError('animation timeline invalid')
                prev=end
            if not review.get('motion_verified') or not {'render_receipt','picture','audio'}<=set(files):raise ValueError('animation motion/render evidence missing')
            if item.get('render_receipt_sha256')!=files['render_receipt']['sha256']:raise ValueError('animation render receipt mismatch')
            receipt=json.loads(_file(root,files['render_receipt']).read_text())
            videos=[r for r in receipt.get('resources',[]) if r.get('kind')=='mp4']
            if len(videos)!=1 or videos[0]['sha256'].removeprefix('sha256:')!=files['mp4']['sha256']:raise ValueError('animation render video mismatch')
            timeline=lambda rows:[(s.get('step_id'),s.get('start'),s.get('end')) for s in rows]
            if timeline(receipt.get('steps',[]))!=timeline(item['steps']):raise ValueError('animation render timeline mismatch')
            playback=item.get('playback',{});audio=playback.get('audio',{})
            expected='practice-media/'+ident+'/'
            if playback.get('video_src')!=expected+Path(files['picture']['path']).name or audio.get('src')!=expected+Path(files['audio']['path']).name:raise ValueError('animation playback path mismatch')
            try:raw=base64.b64decode(audio.get('data_base64',''),validate=True)
            except ValueError:raise ValueError('animation audio encoding invalid')
            if hashlib.sha256(raw).hexdigest()!=files['audio']['sha256']:raise ValueError('animation playback audio hash mismatch')
        evidence=item.get('review_evidence')
        if not evidence:raise ValueError('media review evidence missing')
        rows=json.loads(_file(root,evidence).read_text()).get('items',[])
        matches=[r for r in rows if r.get('id')==ident]
        if len(matches)!=1 or matches[0].get('spec_sha256')!=media_spec_digest(item) or matches[0].get('review')!=review:raise ValueError('media review evidence or specification mismatch')
    return manifest

def public_media_path(path, kind=None):
    """Accept only local URL segments and the file type appropriate to each role."""
    if not isinstance(path, str) or not all(
        re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', part) for part in path.split('/')
    ):
        return False
    return kind is None or Path(path).suffix.lower() in PUBLIC_MEDIA_SUFFIXES[kind]


def _text_fields(item, keys):
    return {key: item[key] for key in keys if isinstance(item.get(key), str)}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def public_media_display(item):
    """Keep the display and player timeline fields used by learning pages."""
    row = _text_fields(item, _DISPLAY_FIELDS)
    if type(item.get('static_existing_in_solution')) is bool:
        row['static_existing_in_solution'] = item['static_existing_in_solution']
    if _finite(item.get('duration')):
        row['duration'] = item['duration']
    if isinstance(item.get('steps'), list):
        row['steps'] = [
            {**_text_fields(step, _STEP_TEXT_FIELDS),
             **{key: step[key] for key in ('start', 'end') if _finite(step.get(key))}}
            for step in item['steps'] if isinstance(step, dict)
        ]
    return row


def public_media_resources(resources):
    """Project resources into the path and cache identity used by the player."""
    public = {}
    for kind in PUBLIC_MEDIA_SUFFIXES:
        row = resources.get(kind)
        if isinstance(row, dict) and public_media_path(row.get('relative_path'), kind):
            public[kind] = {'relative_path': row['relative_path']}
            digest = row.get('sha256')
            if isinstance(digest, str) and re.fullmatch(r'(sha256:)?[a-f0-9]{64}', digest):
                public[kind]['sha256'] = digest
    return public


def public_media_playback(playback):
    """Keep the compatible file and inline audio paths for offline WebAudio."""
    if not isinstance(playback, dict):
        return None
    audio = playback.get('audio')
    if (not public_media_path(playback.get('video_src'), 'picture')
            or not isinstance(audio, dict) or not public_media_path(audio.get('src'), 'audio')):
        return None
    row = {'src': audio['src']}
    encoded = audio.get('data_base64')
    if isinstance(encoded, str) and re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', encoded):
        row['data_base64'] = encoded
    mime = audio.get('mime')
    if isinstance(mime, str) and re.fullmatch(r'audio/[A-Za-z0-9.+-]+', mime):
        row['mime'] = mime
    return {'video_src': playback['video_src'], 'audio': row}


def copy_media_assets(manifest, media_root, assets):
    """Copy reviewed learning assets and emit the browser-only projection."""
    media_root, assets = Path(media_root).resolve(), Path(assets)
    runtime = []
    for item in manifest['items']:
        base = 'practice-media/' + item['id'] + '/'
        dest = assets / base
        dest.mkdir(parents=True)
        resources = {}
        for kind in PUBLIC_MEDIA_SUFFIXES:
            if kind not in item['files']:
                continue
            resource = item['files'][kind]
            name = Path(resource['path']).name
            if not public_media_path(name, kind):
                raise ValueError(f'unsupported public media filename: {name}')
            src = _file(media_root, resource)
            target = dest / name
            shutil.copy2(src, target)
            if _sha(target) != resource['sha256']:
                raise ValueError(f'media changed during publication: {name}')
            resources[kind] = {'relative_path': name, 'sha256': resource['sha256']}
            if kind == 'svg' and item.get('static_existing_in_solution') is True:
                # Keep the exact reviewed figure used by existing answer Markdown.
                alias = assets / name
                if alias.exists() and _sha(alias) != resource['sha256']:
                    raise ValueError(f'answer SVG differs from reviewed media: {name}')
                if not alias.exists():
                    shutil.copy2(target, alias)
        row = {**public_media_display(item), 'base': base,
               'resources': public_media_resources(resources)}
        if item['kind'] == 'animation':
            playback = public_media_playback(item['playback'])
            if not playback or 'data_base64' not in playback['audio']:
                raise ValueError('invalid public playback data')
            row['playback'] = playback
        runtime.append(row)
    return runtime
