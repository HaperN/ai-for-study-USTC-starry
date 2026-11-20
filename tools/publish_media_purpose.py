"""Build a separate reader with source-bound media purposes and audible playback."""
from __future__ import annotations
import argparse,base64,hashlib,json,shutil,subprocess,sys,wave
from copy import deepcopy
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.upgrade_study_reader import load_reader_data
from blocks.render.build import write_reader_site

def digest(value):return hashlib.sha256(value).hexdigest()
def identity(value):return digest(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())

def safe_asset(root,path):
    if not isinstance(path,str) or not path or '\\' in path or ':' in path or path.startswith('/') or PurePosixPath(path).as_posix()!=path or '..' in PurePosixPath(path).parts:
        raise ValueError('Noncanonical local media path')
    target=(Path(root)/path).resolve()
    if not target.is_relative_to(Path(root).resolve()) or not target.is_file():raise ValueError('Missing or escaping media asset: '+path)
    return target

def bind_purposes(data,enriched,audio_root,authored_path):
    authored_path=Path(authored_path);authored=json.loads(authored_path.read_text())
    if enriched.get('partial') or enriched.get('audio_build',{}).get('input_sha256')!=digest(authored_path.read_bytes()):raise ValueError('Incomplete or changed narration input')
    expected={}
    for uid,unit in data['units'].items():
        for group,kind in [('visuals','visual'),('media','animation')]:
            for plan in unit.get('study_lesson',{}).get(group,[]):
                key=(uid,plan['id'])
                if key in expected:raise ValueError('Duplicate media binding')
                expected[key]=(plan,kind)
    originals={(i['unit_id'],i['id']):i for i in authored['items']}
    if len(originals)!=len(authored['items']) or set(originals)!=set(expected):raise ValueError('Authored purposes must cover exactly every teaching media')
    result=deepcopy(data);manifest=deepcopy(enriched);seen=set()
    for item in manifest['items']:
        key=(item['unit_id'],item['id'])
        if key not in expected or key in seen:raise ValueError('Unexpected or duplicate purpose')
        seen.add(key);plan,kind=expected[key]
        if {k:v for k,v in item.items() if k!='audio'}!=originals[key]:raise ValueError('Purpose copy changed after audio generation')
        if item['kind']!=kind or item.get('source_basis',{}).get('plan_sha256')!=identity(plan):raise ValueError('Purpose does not match current media plan')
        if not item.get('purpose_markdown','').strip() or not item.get('spoken_text','').strip():raise ValueError('Empty media purpose')
        audio=item['audio'];mp3=safe_asset(audio_root,audio['src']);wav=safe_asset(audio_root,audio['wav_src']);receipt=json.loads(safe_asset(audio_root,audio['receipt_src']).read_text())
        if digest(mp3.read_bytes())!=audio['sha256'] or digest(wav.read_bytes())!=audio['wav_sha256']:raise ValueError('Purpose audio bytes changed')
        if audio['spoken_text_sha256']!=digest(item['spoken_text'].encode()) or receipt.get('text')!=item['spoken_text'] or receipt.get('identity')!=audio['source_identity'] or receipt.get('audio_sha256')!=audio['wav_sha256'] or receipt.get('cache_key')!=audio['cache_key']:raise ValueError('Purpose audio identity mismatch')
        if not audio['duration_seconds']>0 or not audio['decoded_rms']>0 or not 0<audio['decoded_peak']<=1:raise ValueError('No valid decoded purpose audio')
        audio.update(mime='audio/mpeg',data_base64=base64.b64encode(mp3.read_bytes()).decode())
    if seen!=set(expected):raise ValueError('Missing teaching media purposes')
    result['media_purposes']=manifest
    return result

def packet_hash(ffmpeg,path):
    return subprocess.check_output([ffmpeg,'-v','error','-i',str(path),'-map','0:v:0','-c','copy','-f','hash','-hash','sha256','-'],text=True).strip()

def add_audible_videos(data,book_root,ffmpeg):
    rows=[]
    for uid,unit in data['units'].items():
        wanted={p['request_id'] for p in unit.get('study_lesson',{}).get('media',[])}
        for animation in data.get('animations',{}).get(uid,[]):
            if animation.get('request_id') not in wanted:continue
            base=animation.get('base','');resources=animation['resources'];video=safe_asset(book_root,base+resources['mp4']['relative_path']);wav=safe_asset(book_root,base+resources['audio']['relative_path'])
            for source,key in [(video,'mp4'),(wav,'audio')]:
                if digest(source.read_bytes())!=resources[key]['sha256'].removeprefix('sha256:'):raise ValueError('Original animation asset mismatch')
            folder=Path(book_root)/'assets/audible-video'/digest(video.read_bytes())[:24];folder.mkdir(parents=True,exist_ok=True)
            visual=folder/'picture.mp4';audio=folder/'voice.mp3'
            subprocess.run([ffmpeg,'-v','error','-y','-i',str(video),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(visual)],check=True)
            subprocess.run([ffmpeg,'-v','error','-y','-i',str(wav),'-map_metadata','-1','-ac','1','-ar','48000','-c:a','libmp3lame','-b:a','128k',str(audio)],check=True)
            original_packets=packet_hash(ffmpeg,video)
            if packet_hash(ffmpeg,visual)!=original_packets:raise ValueError('Picture track changed during split')
            with wave.open(str(wav)) as stream:duration=stream.getnframes()/stream.getframerate()
            animation['playback']={'version':1,'video_src':visual.relative_to(book_root).as_posix(),
                'audio':{'src':audio.relative_to(book_root).as_posix(),'mime':'audio/mpeg','data_base64':base64.b64encode(audio.read_bytes()).decode(),'sha256':digest(audio.read_bytes()),'duration_seconds':duration},
                'original_video_sha256':digest(video.read_bytes()),'original_audio_sha256':digest(wav.read_bytes()),'visual_packet_hash':original_packets}
            rows.append({'unit_id':uid,'request_id':animation['request_id'],'duration_seconds':duration,'playback':{k:v for k,v in animation['playback'].items() if k!='audio'},'audio_sha256':digest(audio.read_bytes())})
    expected=sum(len(u.get('study_lesson',{}).get('media',[])) for u in data['units'].values())
    if len(rows)!=expected:raise ValueError('Missing playable animation')
    return rows

def publish(source,output,purpose_path,audio_root,authored_path,*,ffmpeg='ffmpeg'):
    source=Path(source).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):raise ValueError('New separate output required')
    data=load_reader_data(source/'book');enriched=json.loads(Path(purpose_path).read_text());data=bind_purposes(data,enriched,audio_root,authored_path)
    shutil.copytree(source,output);book=output/'book'
    for item in enriched['items']:
        for key in ['src','wav_src','receipt_src']:
            rel=item['audio'][key];target=book/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(safe_asset(audio_root,rel),target)
    videos=add_audible_videos(data,book,ffmpeg)
    write_reader_site(data,book,book=data.get('display_title','复变函数 · 跟着教材学'))
    report={'schema_version':1,'source':str(source),'purposes':len(enriched['items']),'visuals':sum(i['kind']=='visual' for i in enriched['items']),
            'animations':len(videos),'purpose_audio_seconds':sum(i['audio']['duration_seconds'] for i in enriched['items']),
            'purpose_source_sha256':digest(Path(authored_path).read_bytes()),'audio_manifest_sha256':digest(Path(purpose_path).read_bytes()),
            'method':'Picture track retained packet-for-packet; same PCM re-encoded as MP3 for independent Web Audio playback; no new content approval',
            'video_playback':videos}
    (output/'media-purpose-build.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['source','output','purpose','audio-root','authored']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--ffmpeg',default='ffmpeg');a=p.parse_args()
    print(json.dumps(publish(a.source,a.output,a.purpose,a.audio_root,a.authored,ffmpeg=a.ffmpeg),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
