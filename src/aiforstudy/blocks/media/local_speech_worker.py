"""Offline Qwen3 CustomVoice worker. Model files are verified before loading.

Run with the pinned mlx-audio environment; CPU media packaging uses local-cache.
Raw float WAV and unmodified PCM16 are retained separately. No voice cloning.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
from .local_narration import digest, cache_key, LocalCacheBackend, worker_identity


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('model-dir','model-manifest','corpus','cache-dir','output-config'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')
    manifest=json.loads(a.model_manifest.read_text())
    if manifest['status']!='verified_complete':raise ValueError('Model manifest not verified')
    hashes={}
    for f in manifest['files']:
        path=a.model_dir/f['file']
        if digest(path)!=f['sha256']:raise ValueError('Model file hash mismatch: '+f['file'])
        hashes[f['file']]=f['sha256']
    version=importlib.metadata.version('mlx-audio')
    if version!='0.6.5':raise ValueError('This worker requires mlx-audio 0.6.5')
    identity=worker_identity(manifest)
    config={'backend':'local-cache','identity':identity,'cache_dir':str(a.cache_dir.resolve())}
    a.output_config.parent.mkdir(parents=True,exist_ok=True)
    a.output_config.write_text(json.dumps(config,ensure_ascii=False,indent=2))
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from mlx_audio.tts.utils import load
    mx.set_memory_limit(32*1024**3);mx.set_cache_limit(1024**3)
    model=load(a.model_dir,lazy=True,strict=True,trust_remote_code=False)
    if model.config.tts_model_type!='custom_voice':raise ValueError('Expected custom_voice architecture')
    records=json.loads(a.corpus.read_text())['records']
    for index,r in enumerate(records):
        text=r['comparison_spoken_text'];key=cache_key(text,identity);folder=a.cache_dir/key;folder.mkdir(parents=True,exist_ok=True)
        if (folder/'result.json').exists():
            LocalCacheBackend(config).synthesize(text,folder/'verified.wav');(folder/'verified.wav').unlink()
            print(json.dumps({'index':index+1,'id':r['id'],'cached':True}),flush=True);continue
        mx.random.seed(identity['seed']);start=time.monotonic()
        results=list(model.generate(text=text,voice='serena',lang_code='chinese',instruct=identity['instruction'],max_tokens=2304,stream=False,verbose=False))
        if len(results)!=1:raise ValueError('Expected one complete nonstreaming result')
        result=results[0];pcm=np.asarray(result.audio,dtype=np.float32).squeeze();rate=int(result.sample_rate)
        if pcm.ndim!=1 or not len(pcm) or not np.isfinite(pcm).all() or result.token_count>=2304:raise ValueError('Invalid/capped generation')
        sf.write(folder/'raw.wav',pcm,rate,subtype='FLOAT');sf.write(folder/'audio.wav',pcm,rate,subtype='PCM_16')
        data={'cache_key':key,'identity':identity,'text':text,'corpus_id':r['id'],'audio_sha256':digest(folder/'audio.wav'),'raw_sha256':digest(folder/'raw.wav'),'sample_rate':rate,'sample_count':len(pcm),'duration_seconds':len(pcm)/rate,'generation_seconds':time.monotonic()-start,'token_count':result.token_count,'budget_reached':False,'human_listening_completed':False,'naturalness_proven':False,'peak':float(np.abs(pcm).max()),'rms':float(np.sqrt(np.mean(pcm**2)))}
        pending=folder/'result.tmp';pending.write_text(json.dumps(data,ensure_ascii=False,indent=2));pending.replace(folder/'result.json')
        print(json.dumps({'index':index+1,'id':r['id'],'duration':data['duration_seconds'],'elapsed':data['generation_seconds']}),flush=True)
        mx.clear_cache()

if __name__=='__main__':main()
