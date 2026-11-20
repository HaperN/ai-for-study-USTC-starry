"""Source and embedding contracts, without claiming readability from word counts."""
import json
import re
import unittest
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'data/study-revision-20261029/lessons-CH01-04.json'
SOURCE = ROOT / 'data/parse/complex-analysis-v2'
OLD = ROOT.parent / 'delivery/复变函数-交互有声版-20261028/book/data/data.js'

def load_old():
    return json.loads(re.split(r'window\.__DATA__\s*=\s*', OLD.read_text(), maxsplit=1)[1].strip().rstrip(';'))

class TextbookLessonsFirstFourChapters(unittest.TestCase):
    def test_rewritten_lessons_exist_and_cover_the_actual_source_units(self):
        self.assertTrue(TARGET.is_file(), 'The authored textbook revision does not exist yet')
        data = json.loads(TARGET.read_text())
        self.assertEqual(data['schema_version'], 'TextbookLessonRevisionV1')
        expected = {u['unit_id']:u for u in json.loads((SOURCE/'chapters.json').read_text())['lesson_units'] if u['chapter'] in {'CH01','CH02','CH03','CH04'}}
        self.assertEqual(set(data['units']), set(expected))
        old = load_old()
        for uid,u in data['units'].items():
            with self.subTest(unit=uid):
                self.assertEqual(u['unit_id'], uid)
                self.assertEqual(u['title'], expected[uid]['title'])
                self.assertEqual(u['source_path'], 'data/parse/complex-analysis-v2/'+expected[uid]['original_text'])
                self.assertTrue((ROOT/u['source_path']).is_file())
                body=u['body_markdown']
                self.assertTrue(body.strip(), 'A lesson must contain actual teaching text')
                self.assertTrue(u['next_bridge'].strip())
                self.assertIn(u['next_bridge'], body)
                anchors={b['anchor'] for b in old['units'][uid]['reader_document']['blocks']}
                for a in re.findall(r'\]\(#(reader-[^)]+)\)',body):
                    self.assertIn(a, anchors)
                markers=[]
                for v in u['visuals']:
                    self.assertEqual(v['marker'], '[[TEACHING_VISUAL:'+v['id']+']]')
                    for key in ('kind','model_title','title','purpose','action','observation','conclusion'):
                        self.assertTrue(v[key].strip(), key)
                    self.assertNotEqual(v['kind'],'unmatched')
                    self.assertTrue(v.get('formula'), 'Every visual needs a LaTex formula for its actual model')
                    markers.append(v['marker'])
                media_ids={a['request_id'] for a in old['animations'].get(uid,[])}
                for m in u['media']:
                    self.assertIn(m['request_id'],media_ids)
                    self.assertEqual(m['marker'], '[[TEACHING_MEDIA:'+m['id']+']]')
                    for key in ('purpose','watch_for','conclusion'):
                        self.assertTrue(m[key].strip())
                    authored=m.get('narration_steps',[])
                    original=next(a for a in old['animations'][uid] if a['request_id']==m['request_id'])['steps']
                    self.assertEqual([x['step_id'] for x in authored], [x['step_id'] for x in original])
                    for step in authored:
                        self.assertTrue(step['narration_text'].strip())
                        self.assertTrue(step['caption_tex'].strip())
                    markers.append(m['marker'])
                self.assertEqual(sorted(re.findall(r'\[\[TEACHING_(?:VISUAL|MEDIA):[^\]]+\]\]',body)), sorted(markers))
                for marker in markers:
                    self.assertEqual(body.count(marker),1)
                    before,after=body.split(marker)
                    self.assertTrue(before.strip(), 'A visual needs preceding reading context')
                    self.assertTrue(after.strip(), 'Reading must continue after the visual')
                self.assertNotRegex(body,r'待补充|此处插图|TODO|你可以修改图像下方的参数')
                self.assertNotIn('```',body)
                self.assertEqual(body.count('$') % 2, 0, 'LaTex delimiters must balance')
                self.assertNotRegex(body, r'^#{1,2} ', 'Unit title must not be repeated')
                for marker in markers:
                    self.assertIn('\n\n'+marker+'\n\n', body)


    def test_visual_titles_really_select_the_declared_scene(self):
        self.assertTrue(TARGET.is_file())
        script = r"""
const fs=require('fs');
const api=require('./blocks/render/assets/study-visuals.js');
api.registerScenes(require('./blocks/render/assets/study-foundation-scenes.js'));
api.registerScenes(require('./blocks/render/assets/study-special-scenes.js'));
const data=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
const failures=[];
for(const u of Object.values(data.units))for(const v of u.visuals){
 const actual=api.chooseScene(v.model_title,'').kind;
 if(actual!==v.kind)failures.push(u.unit_id+': '+v.model_title+' => '+actual+' expected '+v.kind);
}
if(failures.length){process.stderr.write(failures.join('\n'));process.exit(1);}
"""
        result=subprocess.run(['node','-e',script,str(TARGET)],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__ == '__main__':
    unittest.main()
