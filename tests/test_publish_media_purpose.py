import hashlib,json
from copy import deepcopy
import pytest
from tools.publish_media_purpose import bind_purposes, safe_asset

def fixture(tmp_path):
    plan={'id':'V','kind':'conjugate','title':'镜像'}
    sha=lambda b:hashlib.sha256(b).hexdigest()
    row={'unit_id':'U','id':'V','kind':'visual','purpose_markdown':'观察镜像。','spoken_text':'观察镜像。',
         'source_basis':{'plan_sha256':sha(json.dumps(plan,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())}}
    authored=tmp_path/'authored.json';authored.write_text(json.dumps({'schema_version':1,'items':[row]},ensure_ascii=False))
    root=tmp_path/'audio';(root/'assets/purpose-audio').mkdir(parents=True)
    for name in ['a.mp3','a.wav']:(root/'assets/purpose-audio'/name).write_bytes(b'fixture-'+name.encode())
    receipt={'text':row['spoken_text'],'identity':{'model':'fixture'},'audio_sha256':sha((root/'assets/purpose-audio/a.wav').read_bytes()),'cache_key':'test'}
    (root/'assets/purpose-audio/a.json').write_text(json.dumps(receipt))
    audio={'src':'assets/purpose-audio/a.mp3','wav_src':'assets/purpose-audio/a.wav','receipt_src':'assets/purpose-audio/a.json',
           'sha256':sha((root/'assets/purpose-audio/a.mp3').read_bytes()),'wav_sha256':receipt['audio_sha256'],
           'source_identity':receipt['identity'],'cache_key':'test','spoken_text_sha256':sha(row['spoken_text'].encode()),
           'duration_seconds':1,'decoded_rms':.1,'decoded_peak':.2}
    enriched={'schema_version':1,'items':[{**row,'audio':audio}],'audio_build':{'input_sha256':sha(authored.read_bytes())}}
    data={'units':{'U':{'study_lesson':{'visuals':[plan],'media':[]}}}}
    return data,enriched,root,authored

def test_complete_binding_embeds_exact_audio_and_preserves_input(tmp_path):
    data,enriched,root,authored=fixture(tmp_path);before=deepcopy(data)
    result=bind_purposes(data,enriched,root,authored)
    assert data==before and 'media_purposes' in result
    import base64
    assert base64.b64decode(result['media_purposes']['items'][0]['audio']['data_base64'])==(root/'assets/purpose-audio/a.mp3').read_bytes()

@pytest.mark.parametrize('change',['missing','duplicate','stale_plan','spoken','bytes','purpose'])
def test_missing_stale_or_mismatched_material_cannot_publish(tmp_path,change):
    data,enriched,root,authored=fixture(tmp_path)
    if change=='missing':enriched['items']=[]
    elif change=='duplicate':enriched['items']*=2
    elif change=='stale_plan':data['units']['U']['study_lesson']['visuals'][0]['title']='另一个图'
    elif change=='spoken':enriched['items'][0]['audio']['spoken_text_sha256']='wrong'
    elif change=='bytes':(root/'assets/purpose-audio/a.mp3').write_bytes(b'changed')
    elif change=='purpose':enriched['items'][0]['purpose_markdown']='错配用途'
    with pytest.raises(ValueError):bind_purposes(data,enriched,root,authored)

def test_asset_alias_and_symlink_escape_rejected(tmp_path):
    outside=tmp_path/'outside';outside.write_text('outside');root=tmp_path/'assets';root.mkdir();(root/'escape').symlink_to(outside)
    for path in ['../outside','escape','./thing','https://example.com/a.mp3','a/../thing']:
        with pytest.raises(ValueError):safe_asset(root,path)
