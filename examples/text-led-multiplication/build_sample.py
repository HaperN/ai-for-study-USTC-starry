"""Build an independent offline sample; do not rewrite the book."""
from pathlib import Path
import json,shutil,hashlib,re
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
OUT=ROOT.parent/'delivery/复变函数-教材连贯版-20261029/text-led-multiplication'
BOOK=OUT.parent/'book'
lesson=json.loads((HERE/'lesson.json').read_text());manifest=json.loads((HERE/'audio-manifest.json').read_text())
spoken={s['id']:s['text'] for sec in lesson['sections'] for s in sec['narration']}
spoken.update({s['id']:s['text'] for s in lesson.get('conclusion_narration',[])})
assert set(spoken)=={s['id'] for s in manifest}
OUT.mkdir(parents=True,exist_ok=True);(OUT/'assets').mkdir(exist_ok=True);(OUT/'audio').mkdir(exist_ok=True)
for name in ['lesson.css','multiplication.js','lesson-player.js','start.js']:
 shutil.copy2(HERE/name,OUT/name)
for name in ['katex.min.js','katex.min.css','marked.min.js','study-math.js','reader-markdown.js','render-core.js']:
 shutil.copy2(BOOK/'assets'/name,OUT/'assets'/name)
shutil.copytree(BOOK/'assets/fonts',OUT/'assets/fonts',dirs_exist_ok=True)
for row in manifest:
 assert row['text']==spoken[row['id']],row['id']
 source=HERE/row['file'];assert hashlib.sha256(source.read_bytes()).hexdigest()==row['sha256']
 shutil.copy2(source,OUT/row['file'])
payload=OUT/'sample-data.js.tmp'
payload.write_text('window.TextLedSampleData='+json.dumps({'lesson':lesson,'manifest':manifest},ensure_ascii=False)+';\n');payload.replace(OUT/'sample-data.js')
html=(HERE/'index.html').read_text()
def version(match):
 name=match[2];file=OUT/name
 return match[1]+name+'?v='+hashlib.sha256(file.read_bytes()).hexdigest()[:12]+match[3] if file.is_file() else match[0]
html=re.sub(r'(\b(?:src|href)=")([^"?#]+\.(?:js|css))(")',version,html)
pending=OUT/'index.html.tmp';pending.write_text(html);pending.replace(OUT/'index.html')
report={'title':lesson['title'],'sections':len(lesson['sections']),'audio_segments':len(manifest),'decoded_audio_seconds':sum(r['duration'] for r in manifest),'single_shared_text':True,'source_unit':lesson['source']['unit_id']}
(OUT/'build-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'output':str(OUT),**report},ensure_ascii=False))
