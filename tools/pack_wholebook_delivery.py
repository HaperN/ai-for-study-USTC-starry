"""Package the offline textbook and rebuildable source with their local inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED={'node_modules','__pycache__','.pytest_cache','.git','.devtools','.venv','.venv-linux','.DS_Store'}

def eligible(path):
    return not any(p in EXCLUDED or p.startswith('.env') for p in path.parts) and path.suffix not in {'.pyc','.zip','.pending'}

def write_zip(target,entries):
    pending=target.with_suffix('.zip.pending')
    with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        names=set()
        for source,name in entries:
            if name in names:raise ValueError('Duplicate archive entry: '+name)
            names.add(name);archive.write(source,name)
    with zipfile.ZipFile(pending) as archive:
        bad=archive.testzip()
        if bad:raise ValueError('ZIP CRC failure: '+bad)
        count=len(archive.infolist())
    pending.replace(target)
    return {'file':target.name,'bytes':target.stat().st_size,'files':count,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'zip_integrity':'passed'}

def package(delivery,source_reader):
    delivery,source_reader=Path(delivery).resolve(),Path(source_reader).resolve()
    offline=[]
    for path in delivery.rglob('*'):
        rel=path.relative_to(delivery)
        if not path.is_file() or rel.name=='打包校验.json' or not eligible(rel) or any(p in {'archive','media-corrections','rebuild-source'} for p in rel.parts):continue
        offline.append((path,str(rel)))
    print('Packaging offline reader',flush=True)
    reports=[write_zip(delivery/'复变函数-全书重讲版-离线阅读包.zip',offline)]
    source=[];prefix='AIforStudy-全书版源码/'
    for name in ['src','lib','blocks','config','tools','tests','docs','examples','figures','data/raw','data/parse','data/study-revision-20261029']:
        for path in (ROOT/name).rglob('*'):
            rel=path.relative_to(ROOT)
            if path.is_file() and eligible(rel):source.append((path,prefix+str(rel)))
    for path in ROOT.iterdir():
        if path.is_file() and eligible(path.relative_to(ROOT)) and not path.name.startswith('.'):
            source.append((path,prefix+path.name))
    source.append((source_reader/'data/data.js',prefix+'source-reader/data/data.js'))
    if (source_reader/'provenance.json').is_file():source.append((source_reader/'provenance.json',prefix+'source-reader/provenance.json'))
    for path in (source_reader/'assets/source').rglob('*'):
        if path.is_file():source.append((path,prefix+'source-reader/'+str(path.relative_to(source_reader))))
    source.append((delivery/'neural-media.json',prefix+'media-cache/neural-media.json'))
    for path in (delivery/'book/assets/narrated').rglob('*'):
        if path.is_file():source.append((path,prefix+'media-cache/book/'+str(path.relative_to(delivery/'book'))))
    source.append((delivery/'源码重建说明.txt',prefix+'本版重建说明.txt'))
    for name in ['verification-summary.json','build-report.json']:
        source.append((delivery/name,prefix+name))
    for path in (delivery/'media-corrections').rglob('*'):
        if path.is_file() and path.suffix in {'.py','.json','.png'}:
            source.append((path,prefix+'docs/media-pixel-corrections/'+str(path.relative_to(delivery/'media-corrections'))))
    print('Packaging source and verified media cache',flush=True)
    reports.append(write_zip(delivery/'AIforStudy-全书重讲版-完整源码.zip',source))
    (delivery/'打包校验.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(reports,ensure_ascii=False,indent=2),flush=True)
    return reports

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--delivery',required=True);p.add_argument('--source-reader',required=True)
    a=p.parse_args();package(a.delivery,a.source_reader)
