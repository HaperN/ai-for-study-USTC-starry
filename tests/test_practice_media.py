import json,hashlib,base64
from pathlib import Path
import pytest
from aiforstudy.practice.media import validate_media_manifest,media_spec_digest
from test_practice_catalog import approved

def sample(tmp_path):
 q=approved();p=tmp_path/'scene.svg';p.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>');h=hashlib.sha256(p.read_bytes()).hexdigest()
 entry={'id':'scene','question_id':q['id'],'question_content_sha256':q['review']['content_sha256'],'title':'观察','purpose':'解释变化','kind':'svg','files':{'svg':{'path':'scene.svg','sha256':h}},'review':{'status':'accepted','question_content_sha256':q['review']['content_sha256'],'asset_hashes':{'svg':h},'math_checks':'点与公式对应','visual_checks':'实际画面已查看'}}
 evidence=tmp_path/'review.json';evidence.write_text(json.dumps({'items':[{'id':entry['id'],'spec_sha256':media_spec_digest(entry),'review':entry['review']}]}))
 entry['review_evidence']={'path':'review.json','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}
 return q,{'schema_version':'PracticeMediaV1','items':[entry]}

def test_media_cannot_follow_changed_question_or_modified_bytes(tmp_path):
 q,m=sample(tmp_path);validate_media_manifest(m,[q],tmp_path)
 q['answer']='changed'
 with pytest.raises(ValueError,match='question'):validate_media_manifest(m,[q],tmp_path)
 q,m=sample(tmp_path);(tmp_path/'scene.svg').write_text('changed')
 with pytest.raises(ValueError,match='hash'):validate_media_manifest(m,[q],tmp_path)

def test_review_and_paths_are_mandatory(tmp_path):
 q,m=sample(tmp_path);m['items'][0]['review']['status']='rendered'
 with pytest.raises(ValueError,match='review'):validate_media_manifest(m,[q],tmp_path)
 q,m=sample(tmp_path);m['items'][0]['files']['svg']['path']='../secret'
 with pytest.raises(ValueError,match='escape'):validate_media_manifest(m,[q],tmp_path)

def test_animation_needs_manim_video_steps_and_verified_motion(tmp_path):
 q,m=sample(tmp_path);x=m['items'][0];x['kind']='animation';x['engine']='manim';x['steps']=[]
 with pytest.raises(ValueError,match='animation'):validate_media_manifest(m,[q],tmp_path)

def test_media_review_binds_teaching_and_actual_evidence(tmp_path):
 q,m=sample(tmp_path);m['items'][0]['purpose']='different explanation'
 with pytest.raises(ValueError,match='specification'):validate_media_manifest(m,[q],tmp_path)
 q,m=sample(tmp_path);(tmp_path/'review.json').write_text('{}')
 with pytest.raises(ValueError,match='hash'):validate_media_manifest(m,[q],tmp_path)

def animation(tmp_path):
 q,m=sample(tmp_path);x=m['items'][0];x.update(kind='animation',engine='manim',steps=[{'step_id':'S1','start':0,'end':1}])
 for kind,name in [('mp4','video.mp4'),('picture','picture.mp4'),('audio','voice.mp3'),('poster','poster.png'),('captions','captions.vtt'),('transcript','transcript.txt')]:
  p=tmp_path/name;p.write_bytes(kind.encode());x['files'][kind]={'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 receipt=tmp_path/'render.json';receipt.write_text(json.dumps({'resources':[{'kind':'mp4','sha256':x['files']['mp4']['sha256']}],'steps':x['steps']}))
 x['render_receipt_sha256']=hashlib.sha256(receipt.read_bytes()).hexdigest();x['files']['render_receipt']={'path':receipt.name,'sha256':x['render_receipt_sha256']}
 x['playback']={'video_src':'practice-media/scene/picture.mp4','audio':{'src':'practice-media/scene/voice.mp3','data_base64':base64.b64encode(b'audio').decode()}}
 x['review']['motion_verified']=True
 bind_media_evidence(tmp_path, x)
 return q,m

def bind_media_evidence(root, item):
 item['review']['asset_hashes']={k:v['sha256'] for k,v in item['files'].items()}
 evidence=root/'review.json'
 evidence.write_text(json.dumps({'items':[{'id':item['id'],'spec_sha256':media_spec_digest(item),'review':item['review']}]}))
 item['review_evidence']={'path':evidence.name,'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}

def test_animation_rejects_fabricated_receipt_or_changed_playback(tmp_path):
 q,m=animation(tmp_path);validate_media_manifest(m,[q],tmp_path)
 m['items'][0]['render_receipt_sha256']='anything'
 with pytest.raises(ValueError,match='receipt'):validate_media_manifest(m,[q],tmp_path)
 q,m=animation(tmp_path);m['items'][0]['steps'][0]['end']=2
 with pytest.raises(ValueError,match='timeline'):validate_media_manifest(m,[q],tmp_path)
 q,m=animation(tmp_path);m['items'][0]['playback']['audio']['data_base64']=base64.b64encode(b'changed').decode()
 with pytest.raises(ValueError,match='audio hash'):validate_media_manifest(m,[q],tmp_path)


def test_gallery_projects_textbook_runtime_without_production_paths(tmp_path):
 from tools.build_practice_animation_gallery import build_gallery

 assets=tmp_path/'assets';assets.mkdir()
 audit=tmp_path/'制作说明.html';audit.write_text('Private production notes')
 book=tmp_path/'教材';base='assets/narrated/unit/request/'
 (book/base).mkdir(parents=True)
 (book/'data').mkdir()
 (book/base/'scene.mp4').write_bytes(b'offline clip fixture')
 (book/base/'poster.png').write_bytes(b'offline image fixture')
 (book/base/'source.py').write_text('private program')
 clip={
  'request_id':'R1','base':base,'title':{'source_program':{'path':'/private/title.py'}},'resources':{
   'mp4':{'relative_path':'scene.mp4','private_path':'/private/source.py'},
   'poster':{'relative_path':'poster.png'},
   'source':{'relative_path':'source.py'},
  },
  'steps':[{'step_id':'S1','start':0,'end':1,'display_markdown':'Visible caption',
            'motion_lineage':{'path':'/private/source.py'}}],
 }
 data={
  'animations':{'U1':clip},
  'units':{'U1':{
   'title':{'source_program':{'path':'/private/unit.py'}},
   'study_lesson':{'media':[{'id':'plan','request_id':'R1','title':'Reviewed title','purpose':' \t '}]},
  }},
  'media_purposes':{'items':[{'unit_id':'U1','id':'plan','title':' ',
                             'purpose_markdown':{'review_evidence':{'path':'/private/review.json'}}}]},
 }
 (book/'data/data.js').write_text('window.__DATA__='+json.dumps(data)+';')
 build_gallery(tmp_path)
 script=(assets/'textbook-animation-data.js').read_text()
 [runtime]=json.loads(script.removeprefix('window.TEXTBOOK_ANIMATIONS=').rstrip().removesuffix(';'))
 assert runtime['base']==base
 assert runtime['resources']=={'mp4':{'relative_path':'scene.mp4'},'poster':{'relative_path':'poster.png'}}
 assert runtime['steps']==[{'step_id':'S1','start':0,'end':1,'display_markdown':'Visible caption'}]
 assert runtime['textbook_href']=='教材/index.html#/U1'
 assert runtime['title']=='Reviewed title'
 assert runtime['purpose']=='结合教材对应段落观察本动画的步骤与变化。'
 assert runtime['unit_title']=='U1'
 assert '/private/' not in script
 assert '制作说明.html' not in (tmp_path/'动画目录.html').read_text()


def test_media_rejects_absolute_private_input_even_under_source_root(tmp_path):
 q,m=sample(tmp_path)
 item=m['items'][0]
 item['review_evidence']['path']=str(tmp_path/'review.json')
 with pytest.raises(ValueError,match='media path escape'):
  validate_media_manifest(m,[q],tmp_path)


def test_media_cannot_publish_source_program_under_a_public_role(tmp_path):
 q,m=sample(tmp_path)
 item=m['items'][0]
 vector=tmp_path/'scene.svg'
 code=vector.with_suffix('.py')
 vector.rename(code)
 item['files']['svg']['path']=code.name
 bind_media_evidence(tmp_path,item)
 with pytest.raises(ValueError,match='unsupported public media filename'):
  validate_media_manifest(m,[q],tmp_path)
