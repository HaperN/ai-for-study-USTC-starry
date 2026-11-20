const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '../../src/aiforstudy/resources');
const wait = () => new Promise(r => setTimeout(r, 25));
const read = p => fs.readFileSync(path.join(root, p), 'utf8');

async function workbench() {
  const home = new JSDOM(read('control-ui/index.html'));
  assert.equal(home.window.document.querySelector('a[href="/video"]')?.textContent.includes('视频解析'), true);
  home.window.close();
  const dom = new JSDOM(read('control-ui/video.html'), {url: 'http://127.0.0.1:18916/video', runScripts:'outside-only'});
  const w = dom.window, calls = [];
  let state = 'running', revision=null;
  w.fetch = async (url, opts = {}) => {
    calls.push({url, ...opts});
    let data;
    if (url.endsWith('/capabilities')) data = {token:'local-token', available:true, sample_available:true, max_upload_bytes:100};
    else if (url.endsWith('/sample')) data = {id:'sample-1', filename:'第一讲.mp4'};
    else if (url.endsWith('/upload')) data = {id:'upload-1', filename:'本地视频.mp4'};
    else if (url.endsWith('/cancel')) {state = 'cancelled'; data = {};}
    else if (url.endsWith('/resume')) {state = 'failed'; data = {};}
    else if (url.endsWith('/reconcile')) {state='running';revision={state:'running',stage:'reconcile',parent_result:'result.json'};data={};}
    else if (url === '/api/videos' && opts.method === 'POST') data = {job_id:'job-1',state:'running'};
    else if (url === '/api/videos') data = {jobs:[{job_id:'job-1',title:'第一讲',state,revision,stage:revision?'reconcile':'asr',progress:0.4,error:state === 'failed' ? '转写失败，请检查本地依赖' : null}]};
    else throw new Error(url);
    return {ok:true,json:async()=>data};
  };
  w.eval(read('control-ui/video.js'));
  await wait();
  assert.equal(w.document.querySelector('#submit-video').disabled, true, '必须先有source');
  assert.match(w.document.querySelector('#video-jobs').textContent, /40%/);
  assert.equal(w.document.querySelector('#video-jobs a'), null, '未完成任务不能有结果下载');
  w.document.querySelector('#sample-video').click(); await wait();
  w.document.querySelector('#submit-video').click(); await wait();
  const submit = calls.find(c=>c.url === '/api/videos' && c.method === 'POST');
  assert.equal(JSON.parse(submit.body).source_id, 'sample-1');
  assert.equal(submit.headers['X-Aifs-Token'], 'local-token');
  w.document.querySelector('[data-action="cancel"]').click(); await wait();
  assert.match(w.document.querySelector('#video-jobs').textContent, /已取消/);
  w.document.querySelector('[data-action="resume"]').click(); await wait();
  assert.match(w.document.querySelector('#video-jobs').textContent, /转写失败/);
  const file = new w.File(['video'], '本地视频.mp4', {type:'video/mp4'});
  Object.defineProperty(w.document.querySelector('#video-file'), 'files', {value:[file]});
  w.document.querySelector('#video-file').dispatchEvent(new w.Event('change')); await wait();
  const upload = calls.find(c=>c.url.endsWith('/upload'));
  assert.equal(upload.body, file, 'File作为body，不base64或整段读取');
  assert.equal(upload.headers['X-Video-Filename'], encodeURIComponent('本地视频.mp4'));
  assert.equal(upload.headers['X-Aifs-Token'], 'local-token');
  state='completed';w.document.querySelector('#refresh-videos').click();await wait();
  w.document.querySelector('[data-action="reconcile"]').click();await wait();
  assert.ok(calls.some(c=>c.url.endsWith('/reconcile')&&c.method==='POST'));
  assert.match(w.document.querySelector('#video-jobs').textContent,/全片统一校订/);
  assert.ok(w.document.querySelector('#video-jobs a[href*="video-result"]'),'校订时保留先前结果入口');
  w.close();
}

async function reader(state='completed') {
  const dom = new JSDOM(read('video-analysis/index.html'), {url:'http://127.0.0.1:18916/video-result.html?job=job-1&t=25',runScripts:'outside-only'});
  const w = dom.window;
  const result = {job_id:'job-1',title:'视频讲义',duration:80,
    source:{path:'source.mp4',playback:{video_src:'playback.mp4',audio:{src:'speech.mp3',data_base64:'SUQz',mime:'audio/mpeg'}}},
    sections:[{id:'s1',title:'复数',start:20,end:40,markdown:'# 复数\n\n<img src=x onerror=alert(1)>\n\n公式 $z=x+iy$。[asr-1]',evidence_ids:['asr-1']}],
    transcript:{segments:[{id:'asr-1',start:20,end:40,text:'原始转写 <script>bad()</script>',quality_warnings:['尾段可能截断']}],coverage:{full_duration:true}},
    frames:[{id:'frame-1',path:'frames/001.jpg',start:30,end:31,sampled_time:30,text:'原始 OCR',visual_text:'模型画面理解',explanation:'本帧说明复数的几何意义',ocr_quality_warnings:['文字置信度低'],uncertainties:['板书不清晰']}],
    formulas:[{latex:'z=x+iy',source_ids:['frame-1'],uncertainty:'符号待核实'}],uncertainties:[],coverage:{frame_sampling_seconds:30},provenance:{asr:'local'},review:{state:'completed',decision:'issues_found',issues:[{severity:'error',description:'指数负号与原始转写冲突',source_ids:['asr-1'],suggestion:'回看片段核对指数'}],limitations:['仅做文本交叉检查'],automatic_approval:false}};
  result.exports={json:'revisions/r1/result.json',markdown:'revisions/r1/lesson.md',original_json:'result.json',original_markdown:'lesson.md',corrections:'revisions/r1/corrections.json'};
  result.editorial_revision={id:state==='running'?'r1':'old-editor'};
  result.uncertainties=['待听审 [asr-1]']; result.exports.review_evidence='revisions/r1/evidence/review-evidence.json';
  result.corrections=[{before:'识别错数',after:'按画面核对后的值',reason:'依据原帧校订',source_ids:['frame-1']}];result.reconciliation={input_digest:'digest'};
  result.formulas[0].note='这里的虚部是实数。';
  result.formulas.push({latex:'\\sum_{k=1}^n k',source_ids:['frame-1']});w.DSHStudyMath=require('../../blocks/render/assets/study-math.js');
  w.fetch=async()=>({ok:true,json:async()=>({state,revision:state==='running'?{state:'running',parent_result:'result.json'}:undefined,result})});
  let played=0, attached;
  w.DSHAudibleAudio={attachVideo:(video, playback)=>{attached=playback; video.src=playback.video_src; return {play:()=>{played++; return Promise.resolve();},pause:()=>{},dispose:()=>{}};}};
  const formulaCalls=[];
  w.katex={render:(latex,el,opts)=>{formulaCalls.push({latex,opts}); el.textContent=latex;}};
  w.eval(read('video-analysis/result.js')); await wait();
  assert.equal(w.document.querySelector('#source-video').currentTime,25,'导出讲义时间链接应定位兼容播放器');
  assert.match(w.document.querySelector('#transcript').textContent, /原始转写 <script>bad\(\)<\/script>/);
  assert.equal(w.document.querySelector('#lesson img'), null, '模型HTML不能成为DOM');
  assert.equal(w.document.querySelector('#lesson script'), null);
  assert.match(attached.video_src, /artifacts\/playback.mp4$/);
  assert.match(attached.audio.src, /artifacts\/speech.mp3$/);
  w.document.querySelector('#play-video').click(); await wait();
  assert.equal(played,1,'显式播放按钮调用兼容driver');
  w.document.querySelector('#transcript [data-start]').click();
  assert.equal(w.document.querySelector('#source-video').currentTime,20);
  assert.match(w.document.querySelector('#player-status').textContent,/00:20/);
  const inlineSource=w.document.querySelector('#lesson p [data-evidence-id="asr-1"]');
  assert.ok(inlineSource,'正文引用应直接跳回原片');inlineSource.click();
  assert.equal(w.document.querySelector('#source-video').currentTime,20);
  assert.doesNotMatch(w.document.querySelector('#lesson').textContent,/asr-1/,'来源编号不挤进正文');
  assert.ok(formulaCalls.some(c=>c.latex==='z=x+iy' && c.opts.trust===false));
  assert.match(w.document.querySelector('#frames').textContent,/原始 OCR/);
  assert.match(w.document.querySelector('#frames').textContent,/本帧说明复数的几何意义/);
  assert.match(w.document.querySelector('#frames').textContent,/文字置信度低/);
  assert.match(w.document.querySelector('#uncertainties').textContent,/符号待核实/);
  assert.match(w.document.querySelector('#uncertainties').textContent,/指数负号与原始转写冲突/);
  assert.match(w.document.querySelector('#uncertainties').textContent,/回看片段核对指数/);
  assert.match(w.document.querySelector('#uncertainties').textContent,/仅做文本交叉检查/);
  assert.equal(w.document.querySelector('#uncertainties').textContent.includes('未列出额外疑问'),false);
  assert.match(w.document.querySelector('#transcript').textContent,/尾段可能截断/);
  assert.ok(w.document.querySelector('#downloads a[download]'));
  assert.ok(w.document.querySelector('#downloads a[href$="revisions/r1/lesson.md"]'));
  assert.ok(w.document.querySelector('#downloads a[href$="review-evidence.json"]'));
  const audioQuestion=w.document.querySelector('#questions li:first-child [data-evidence-id="asr-1"]');
  assert.ok(audioQuestion);audioQuestion.click();assert.equal(w.document.querySelector('#source-video').currentTime,20);
  assert.match(w.document.querySelector('#corrections').textContent,/按画面核对后的值/);
  assert.equal(w.document.querySelector('#corrections details').open,false);
  assert.match(w.document.querySelector('#formulas').textContent,/这里的虚部是实数/);
  assert.ok(formulaCalls.some(c=>c.latex.includes('sum\\limits')),'视频讲义也使用上下限排版');
  const status=w.document.querySelector('#result-status').textContent;
  if(state==='running'){assert.match(status,/先前已发布版本/);assert.match(status,/来源核对修订/);}
  else assert.doesNotMatch(status,/来源核对修订/);
  w.close();
}

async function incomplete() {
  const dom = new JSDOM(read('video-analysis/index.html'),{url:'http://127.0.0.1/video-result.html?job=bad',runScripts:'outside-only'});
  const w=dom.window;
  w.fetch=async()=>({ok:true,json:async()=>({state:'failed',error:'本地识别失败'})});
  w.eval(read('video-analysis/result.js')); await wait();
  assert.match(w.document.querySelector('#result-status').textContent,/本地识别失败/);
  assert.equal(w.document.querySelector('#downloads a'),null);
  assert.equal(w.document.querySelector('#reader').hidden,true);
  w.close();
}
async function deferredWork() {
  const dom = new JSDOM(read('video-analysis/index.html'), {url:'http://127.0.0.1/video-result.html?job=fixture',runScripts:'outside-only'});
  const w=dom.window;
  w.fetch=async()=>({ok:true,json:async()=>({state:'completed',result:{title:'局部继续测试',duration:20,
    source:{path:'source.mp4'},sections:[{id:'gap',title:'第一段',start:0,end:10,markdown:'未核的局部说明',evidence_ids:['asr-1'],processing_status:'partial'},
      {id:'good',title:'第二段',start:10,end:20,markdown:'后续有效讲义',evidence_ids:['asr-2']}],
    transcript:{segments:[{id:'asr-1',start:0,end:10,text:''},{id:'asr-2',start:10,end:20,text:'后续原文'}]},frames:[],formulas:[],uncertainties:[],
    reconciliation:{state:'deferred'},review:{state:'deferred',decision:'not_completed',automatic_approval:false,limitations:['审核尚未完成']},
    deferred_work:[{id:'gap-1',stage:'lesson',state:'deferred',start:0,end:10,source_ids:['asr-1'],message:'本段待核，其他内容继续'}]}})});
  w.eval(read('video-analysis/result.js'));await wait();
  assert.equal(w.document.querySelector('#reader').hidden,false);
  assert.match(w.document.querySelector('#result-status').textContent,/局部待核/);
  assert.doesNotMatch(w.document.querySelector('#result-status').textContent,/已完成解析与全片校订/);
  const box=w.document.querySelector('#deferred-work');assert.ok(box&&!box.hidden);
  assert.match(box.textContent,/本段待核，其他内容继续/);
  assert.ok(box.querySelector('[data-start="0"]'));
  assert.match(w.document.querySelector('#lesson article').textContent,/未核实来源|未完成/);
  assert.match(w.document.querySelector('#lesson').textContent,/后续有效讲义/);
  assert.match(w.document.querySelector('#questions').textContent,/审核未完成|审核尚未完成/);
  w.close();
}
async function browserAcceptance() {
  // Explicit opt-in. Synthetic media and a private browser profile; never uses models or a user's browser session.
  const {spawn,execFileSync}=require('node:child_process');
  const os=require('node:os');
  const {chromium}=require('playwright');
  const work=path.resolve(__dirname,'../..');
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'aifs-video-ui-'));
  const fixture={job_id:'fixture',title:'浏览器验收用合成片段',duration:4,source:{path:'source.mp4',playback:{video_src:'playback.mp4',audio:{src:'speech.mp3',mime:'audio/mpeg'}}},sections:[{id:'s1',title:'复数与原片核对',start:1,end:3,markdown:'这是**合成测试素材**。\n\n$$z=x+iy$$\n\n<script>window.__injected=1</script>',evidence_ids:['asr-1']}],transcript:{segments:[{id:'asr-1',start:1,end:3,text:'浏览器回看测试原始转写'}],coverage:{duration_seconds:4}},frames:[{id:'frame-1',path:'frame.jpg',start:1,end:2,sampled_time:1,text:'OCR 测试文字',visual_text:'$z=x+iy$',explanation:'测试帧解释',uncertainties:['合成测试']}],formulas:[{latex:'z=x+iy',source_ids:['frame-1'],uncertainty:'测试'}],uncertainties:['合成素材，非真实解析结果'],coverage:{sampled_frames:1},provenance:{fixture:true},review:{status:'fixture'}};
  execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc=size=640x360:rate=25','-t','4','-pix_fmt','yuv420p','-c:v','libx264','-an',path.join(tmp,'playback.mp4')]);
  execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=330:duration=4','-c:a','libmp3lame',path.join(tmp,'speech.mp3')]);
  execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-i',path.join(tmp,'playback.mp4'),'-frames:v','1',path.join(tmp,'frame.jpg')]);
  fs.copyFileSync(path.join(tmp,'playback.mp4'),path.join(tmp,'source.mp4'));
  fixture.source.playback.audio.data_base64=fs.readFileSync(path.join(tmp,'speech.mp3')).toString('base64');
  fs.writeFileSync(path.join(tmp,'result.json'),JSON.stringify(fixture));fs.writeFileSync(path.join(tmp,'lesson.md'),'# 合成测试素材');
  const python=process.env.AIFS_TEST_PYTHON||path.resolve(work,'../../../.venv-linux/bin/python');
  const server=spawn(python,['-u','-c',`
import json, pathlib, sys
from aiforstudy.core.workspace import Workspace
from aiforstudy.ui.server import ControlServer
root=pathlib.Path(sys.argv[1])
class FixtureService:
    def capabilities(self): return {'available':True,'sample_available':True,'max_upload_bytes':99999999}
    def list_jobs(self): return [{'job_id':'fixture','title':'合成浏览器验收素材','state':'completed','stage':'export','progress':1}]
    def get(self,job): return dict(self.list_jobs()[0],result=json.loads((root/'result.json').read_text()))
    def artifact(self,job,name): return root/name if job=='fixture' and name in {'source.mp4','playback.mp4','speech.mp3','frame.jpg','lesson.md','result.json'} else None
control=ControlServer(Workspace(root=root));control._video_service=FixtureService();http=control.make_server()
print(control.origin,flush=True);http.serve_forever()
`,tmp],{cwd:work,env:{...process.env,PYTHONPATH:path.join(work,'src')},stdio:['ignore','pipe','pipe']});
  let browser;
  try{
    const base=await new Promise((resolve,reject)=>{let output='';server.stdout.on('data',data=>{output+=data;const found=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(found)resolve(found[0]);});server.on('error',reject);server.on('exit',code=>reject(new Error('fixture server exit '+code)));});
    browser=await chromium.launch({executablePath:process.env.AIFS_TEST_BROWSER||'/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(()=>{
      window.__audioProbe={starts:0,peak:0,state:null};
      const Context=window.AudioContext||window.webkitAudioContext,original=Context.prototype.createBufferSource;
      Context.prototype.createBufferSource=function(...args){const context=this,source=original.apply(this,args),start=source.start;source.start=function(...startArgs){const probe=window.__audioProbe;probe.starts++;probe.state=context.state;if(source.buffer){for(const value of source.buffer.getChannelData(0).slice(0,10000))probe.peak=Math.max(probe.peak,Math.abs(value));}return start.apply(source,startArgs);};return source;};
    });
    await page.goto(base);await page.locator('a[href="/video"]').click();await page.locator('#video-jobs a').waitFor();
    await page.screenshot({path:path.join(tmp,'workbench.png'),fullPage:true});
    await page.locator('#video-jobs a').click();await page.locator('#reader:not([hidden])').waitFor();
    await page.locator('.katex').first().waitFor();
    await page.waitForFunction(()=>document.querySelector('#frames img')?.naturalWidth>0);
    assert.equal(await page.evaluate(()=>window.__injected),undefined,'模型HTML不能执行');
    await page.locator('#transcript [data-start]').click();
    assert.ok(Math.abs(await page.locator('#source-video').evaluate(v=>v.currentTime)-1)<.2);
    await page.locator('#play-video').click();
    await page.waitForFunction(()=>document.querySelector('#source-video').currentTime>1.5,{},{timeout:15000});
    const pcm=await page.evaluate(()=>window.__audioProbe);
    assert.ok(pcm.starts>0 && pcm.peak>.01 && pcm.state==='running','非零PCM需在运行的AudioContext播放');
    await page.locator('#pause-video').click();
    assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(tmp,'result.png'),fullPage:true});
    const mobile=await browser.newPage({viewport:{width:390,height:844}});mobile.on('pageerror',error=>errors.push(error.message));
    await mobile.goto(base+'/video-result.html?job=fixture');await mobile.locator('#reader:not([hidden])').waitFor();
    assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'窄屏不应横向溢出');
    await mobile.screenshot({path:path.join(tmp,'mobile.png')});
    await mobile.locator('#lesson').scrollIntoViewIfNeeded();await mobile.screenshot({path:path.join(tmp,'mobile-lesson.png')});
    console.log(JSON.stringify({browser:'isolated headless Edge',fixture:'synthetic, not model output',videoClockAdvanced:true,pcm,screenshots:tmp,errors}));
  }finally{if(browser)await browser.close();server.kill('SIGTERM');}
}
(async()=>{await workbench();await reader();await reader('running');await incomplete();await deferredWork();if(process.argv.includes('--browser'))await browserAcceptance();console.log('video analysis UI: PASS');})().catch(e=>{console.error(e);process.exit(1);});
