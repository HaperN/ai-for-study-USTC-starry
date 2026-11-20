const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto');
const repo=path.resolve(__dirname,'..');
const book=path.resolve(process.argv[2]||path.join(repo,'../delivery/复变函数-教材连贯版-20261029/book'));
const output=path.resolve(book,'../coherent-dom-verification.json');
const {JSDOM,VirtualConsole}=require(path.join(repo,'node_modules/jsdom'));
const source=fs.readFileSync(path.join(book,'data/data.js'),'utf8');
const match=source.match(/^\s*(?:\/\*[\s\S]*?\*\/\s*)?window\.__DATA__\s*=\s*([\s\S]*?)\s*;?\s*$/);
if(!match)throw Error('Unexpected data wrapper');
const data=JSON.parse(match[1].replace(/;\s*$/,''));
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
const errors=[],warnings=[],runtimeErrors=[];
const vc=new VirtualConsole();vc.on('jsdomError',e=>runtimeErrors.push(e.message));vc.on('error',e=>runtimeErrors.push(String(e)));
const template=fs.readFileSync(path.join(book,'index.html'),'utf8');
const dom=new JSDOM(template,{url:'https://offline.test/#/'+data.scope[0],runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const win=dom.window,doc=win.document;
win.scrollTo=()=>{};win.HTMLElement.prototype.scrollIntoView=()=>{};win.requestAnimationFrame=()=>1;win.cancelAnimationFrame=()=>{};
win.IntersectionObserver=class {observe(){}unobserve(){}disconnect(){}};
// Canvas rasterization is a browser-only check; DOM and geometry still execute.
win.HTMLCanvasElement.prototype.getContext=()=>new Proxy({}, {get:(_,key)=>key==='measureText'?text=>({width:String(text).length*7}):()=>{},set:()=>true});
let playCalls=0,pauseCalls=0,loadCalls=0;
win.HTMLMediaElement.prototype.play=function(){playCalls++;return Promise.resolve();};
win.HTMLMediaElement.prototype.pause=function(){pauseCalls++;};win.HTMLMediaElement.prototype.load=function(){loadCalls++;};
win.fetch=()=>{throw Error('Network is prohibited in DOM audit');};
win.__DATA__=data;
const assets=path.join(book,'assets');
for(const name of ['katex.min.js','marked.min.js','study-math.js','reader-markdown.js','render-core.js','animations.js','reader-layout.js','study-foundation-scenes.js','study-special-scenes.js','text-led-multiplication.js','study-text-led-figures.js'])win.eval(fs.readFileSync(path.join(assets,name),'utf8'));
// CommonJS avoids the visual controller boot. All authored factory logic is real.
const visuals=require(path.join(assets,'study-visuals.js'));
for(const name of ['study-foundation-scenes.js','study-special-scenes.js'])visuals.registerScenes(require(path.join(assets,name)));
win.DSHStudyVisuals=visuals;
const lesson=require(path.join(assets,'study-lesson.js'));win.DSHStudyLesson=lesson;
win.eval(fs.readFileSync(path.join(assets,'app.js'),'utf8'));
const report={generatedAt:new Date().toISOString(),book,method:'Read-only JSDOM execution of delivered app.js routes, render-core.js and reader-layout.js, then actual DSHStudyLesson.enhance / createTeachingCard / DSHAnimations.mountAnimations. data.js JSON payload parsed with JSON.parse; no evaluation of data, no browser CLI, network, GPU, or real audio/video playback. This is DOM verification, not real browser acceptance.',dataSha256:sha(source),assets:{},expected:{units:76,visuals:data.scope.reduce((n,id)=>n+(data.units[id].study_lesson?.visuals?.length||0),0),media:12},summary:{units:0,visualPlans:0,mediaPlans:0,mountedVisuals:0,mountedMedia:0,markers:0,remainingMarkers:0,kindMismatches:0,automaticVisuals:0,legacyPositionPrompts:0,sourceDisclosures:0,originalDisclosures:0,openSourceDisclosures:0,bodyExampleLinks:0,unresolvedBodyExampleLinks:0,conceptLinks:0,unresolvedConceptLinks:0,sourceLinks:0,unresolvedSourceLinks:0,allReaderLinks:0,unresolvedReaderLinks:0,bodyInferredTopics:0,transcriptPlayers:0,structuredTranscriptLists:0,transcriptRows:0,rawTranscriptFallbackRows:0,narrationSegments:0,completeDisplayNarrations:0,completeTranscriptNarrations:0,completeStageCaptions:0,strictCaptionMathExpressions:0,strictCaptionMathErrors:0,verifiedTextSidecars:0,textSidecarHashErrors:0,katexOpLimits:0,katexErrors:0},units:[],transcriptSamples:[],referenceClickSamples:[],narrationVerification:[],errors,warnings,runtimeErrors};
for(const name of ['app.js','reader-layout.js','render-core.js','study-lesson.js','study-math.js','study-light.css','study-visuals.js','study-foundation-scenes.js','study-special-scenes.js','animations.js','study-text-led-figures.js','study-text-led-figures.css']){const delivered=fs.readFileSync(path.join(assets,name));const src=fs.readFileSync(path.join(repo,'blocks/render/assets',name));report.assets[name]={sha256:sha(delivered),matchesCurrentSource:delivered.equals(src)};}
function semanticNarration(text){return String(text||'').replace(/\\pi|π/g,'派').replace(/\\alpha|α/g,'阿尔法').replace(/\\beta|β/g,'贝塔').replace(/\\tau|τ/g,'陶').replace(/\$|\s+/g,'');}
function captionCanonical(text){return String(text||'').replace(/\\prime/g,"'").replace(/\$|\s+/g,'');}
function readableText(node){if(!node)return '';const clone=node.cloneNode(true);clone.querySelectorAll('.katex-mathml').forEach(e=>e.remove());return clone.textContent;}
function outsideMathText(node){if(!node)return '';const clone=node.cloneNode(true);clone.querySelectorAll('.katex').forEach(e=>e.remove());return clone.textContent;}
let sampleCount=0;
for(const unitId of data.scope){
  win.history.replaceState(null,'','#/'+unitId);win.dispatchEvent(new win.HashChangeEvent('hashchange'));
  const root=doc.querySelector('#content'),unit=data.units[unitId],plan=unit.study_lesson;
  const item={unitId,title:unit.title,routeRendered:doc.documentElement.dataset.readerUnit===unitId,planVisuals:plan?.visuals?.length||0,planMedia:plan?.media?.length||0,mountedVisuals:0,mountedMedia:0,checks:[],visuals:[],media:[],unresolvedLinks:[]};
  function fail(message){item.checks.push(message);errors.push({unitId,message});}
  if(!item.routeRendered){fail('App route did not render unit');report.units.push(item);continue;}
  if(!plan){fail('Missing study_lesson');report.units.push(item);continue;}
  const expectedMarkers=[...(plan.visuals||[]).map(p=>['VISUAL',p]),...(plan.media||[]).map(p=>['MEDIA',p])];
  const beforeMarkers=[...root.querySelectorAll('p')].map(e=>e.textContent.trim()).filter(t=>/^\[\[TEACHING_/.test(t));
  const expectedByMarker=expectedMarkers.map(([kind,p])=>'[[TEACHING_'+kind+':'+p.id+']]');
  if(JSON.stringify(beforeMarkers.slice().sort())!==JSON.stringify(expectedByMarker.slice().sort()))fail('Authored marker paragraphs do not exactly match lesson plans');
  const enhanced=lesson.enhance(root,{unit,data,unitId});
  if(enhanced.errors.length)fail('Lesson enhancement errors: '+enhanced.errors.join('; '));

  const expectedResults=plan.conclusions||[], actualResults=[...root.querySelectorAll('[data-textbook-result]')];
  item.textbookConclusions={planned:expectedResults.length,visible:actualResults.length,math:0};
  if(!Array.isArray(plan.conclusions))fail('Missing textbook conclusion inventory');
  if(actualResults.length!==expectedResults.length)fail('Required textbook conclusions are missing or duplicated');
  expectedResults.forEach(result=>{
    const matches=actualResults.filter(e=>e.dataset.textbookResult===result.id);
    if(matches.length!==1){fail('Missing or duplicate textbook conclusion '+result.id);return;}
    const section=matches[0],coverage=(plan.knowledge_coverage||[]).find(c=>c.id===result.id);
    if(section.closest('details,[hidden]'))fail('Required conclusion is folded or hidden '+result.id);
    const statements=coverage?[...root.querySelectorAll('.lesson-integrated-knowledge')].filter(n=>n.dataset.knowledgeIds.split(' ').includes(result.id)).map(n=>n.querySelector('.lesson-integrated-statement')):[section.querySelector('.lesson-conclusion-statement')];
    const expectedParagraphs=coverage?coverage.excerpts:[result.statement_markdown];
    if(statements.length!==expectedParagraphs.length)fail('Authored knowledge coverage paragraph count differs '+result.id);
    statements.forEach((statement,index)=>{
      if(!statement){fail('No visible statement for '+result.id);return;}
      const expected=doc.createElement('div');expected.innerHTML=win.DSHRenderCore.createRenderer({marked:win.marked,katex:win.katex}).renderMarkdown(expectedParagraphs[index]||'');
      if(statement.textContent.replace(/\s+/g,'')!==expected.textContent.replace(/\s+/g,'')){fail('Actual authored knowledge text differs '+result.id+'/'+index);(item.knowledgeDifferences||(item.knowledgeDifferences=[])).push({id:result.id,part:index,actual:statement.textContent,expected:expected.textContent});}
      const leaves=[...statement.childNodes,...statement.querySelectorAll('*')].filter(e=>e.nodeType===3||(!e.closest('.katex')&&!e.children?.length));
      if(leaves.some(e=>/\$|\\(?:frac|sum|lim|int|operatorname)/.test(e.textContent)))fail('Raw math delimiter or command in conclusion '+result.id);
      if(statement.querySelector('.katex-error'))fail('Math rendering error in conclusion '+result.id);
      for(const annotation of statement.querySelectorAll('annotation[encoding="application/x-tex"]')){
        try{win.katex.renderToString(annotation.textContent,{throwOnError:true,strict:'error',displayMode:!!annotation.closest('.katex-display')});item.textbookConclusions.math++;}
        catch(error){fail('Strict math error '+result.id+': '+error.message);}
      }
    });
  });
  const extra=root.querySelector('.lesson-exercise-results');
  if((plan.supplementary_conclusions||[]).some(r=>!(plan.knowledge_coverage||[]).some(c=>c.id===r.id))&&(!extra||extra.tagName==='DETAILS'||extra.closest('details,[hidden]')))fail('Reusable exercise conclusions are hidden');
  const extraRows=[...root.querySelectorAll('[data-textbook-supplement]')];
  item.reusableConclusions={planned:(plan.supplementary_conclusions||[]).length,visible:extraRows.length};
  if(extraRows.length!==item.reusableConclusions.planned)fail('Reusable exercise conclusions missing');
  for(const r of plan.supplementary_conclusions||[]){const n=extraRows.find(e=>e.dataset.textbookSupplement===r.id);if(!n||n.closest('details,[hidden]')){fail('Reusable result not visible '+r.id);continue;}if(n.querySelector('.katex-error'))fail('Reusable result math error '+r.id);}

  if(/\[\[TEXTBOOK_(?:RESULT|INTEGRATED|END_INTEGRATED)/.test(root.textContent))fail('Textbook conclusion marker leaked');


  const expectedTableRows=(plan.transform_tables||[]).flatMap(t=>t.rows||[]),tableRows=[...root.querySelectorAll('[data-textbook-table-result]')];
  item.transformTableRows={planned:expectedTableRows.length,visible:tableRows.length,strictMath:0};
  if(tableRows.length!==expectedTableRows.length)fail('Transform table rows missing');
  for(const row of expectedTableRows){
    const actual=tableRows.find(n=>n.dataset.textbookTableResult===row.id);
    if(!actual||actual.closest('details,[hidden]')){fail('Transform table row hidden or missing '+row.id);continue;}
    const expected=doc.createElement('div');expected.innerHTML=win.DSHRenderCore.createRenderer({marked:win.marked,katex:win.katex}).renderMarkdown(row.statement_markdown);
    if(actual.querySelector('.lesson-table-statement').textContent!==expected.textContent)fail('Transform row is truncated '+row.id);
    if(actual.querySelector('.katex-error'))fail('Transform row math error '+row.id);
    for(const tex of actual.querySelectorAll('annotation[encoding="application/x-tex"]')){
      try{win.katex.renderToString(tex.textContent,{throwOnError:true,strict:'error',displayMode:!!tex.closest('.katex-display')});item.transformTableRows.strictMath++;}
      catch(error){fail('Strict transform row math '+row.id+': '+error.message);}
    }
  }

  const addedAutomatic=visuals.enhance(root,{data,unitId});
  if(addedAutomatic.length)fail('Legacy keyword enhancer inserted visuals');
  const blocks=[...root.querySelectorAll('.teaching-block')],cards=[...root.querySelectorAll('.study-visual')],players=[...root.querySelectorAll('.animation-player')];
  item.mountedVisuals=cards.length;item.mountedMedia=players.length;
  if(cards.length!==item.planVisuals||players.length!==item.planMedia||blocks.length!==expectedMarkers.length)fail('Mounted block/visual/media counts differ from explicit plans');
  expectedMarkers.forEach(([kind,p])=>{
    const found=blocks.filter(b=>b.dataset.teachingId===p.id&&b.dataset.teachingKind===kind);
    if(found.length!==1){fail('Marker not mounted exactly once: '+kind+' '+p.id);return;}
    const block=found[0];
    if(kind==='VISUAL'){
      const card=block.querySelector('.study-visual'),model=card?.__studyModel;
      const row={id:p.id,plannedKind:p.kind,actualKind:card?.dataset.studyScene,coverage:card?.dataset.studyCoverage,formula:card?.querySelector('.study-formula annotation')?.textContent,geometryLines:model?visuals.geometry(model).lines.length:0};item.visuals.push(row);
      if(!card||card.dataset.studyScene!==p.kind||(p.presentation!=='text-led-figure'&&model?.scene.kind!==p.kind)){report.summary.kindMismatches++;fail('Visual kind mismatch: '+p.id);}
      if(card?.dataset.studyCoverage!=='authored')fail('Visual not authored: '+p.id);
      if(p.presentation==='text-led-figure'&&!card.querySelector('svg,canvas'))fail('Text-led figure has no actual graphic: '+p.id);
    }else{
      const player=block.querySelector('.animation-player');
      const payload=(data.animations[unitId]||[]).find(a=>a.request_id===p.request_id);
      const steps=win.DSHAnimations.stepsOf(payload),lists=player?.querySelectorAll('.animation-transcript-list')||[],rows=player?.querySelectorAll('.animation-transcript-row')||[],fallback=player?.querySelectorAll('[data-source="transcript"]')||[];
      const textSidecars={};
      for(const kind of ['captions','transcript']){
        const resource=payload.resources[kind],file=path.resolve(book,payload.base,resource.relative_path);
        if(!file.startsWith(book+path.sep))throw Error('Text sidecar path escaped book');
        const bytes=fs.readFileSync(file),actual=sha(bytes),declared=String(resource.sha256).replace(/^sha256:/,'');
        const manifest=(payload.resource_manifest||[]).find(r=>r.kind===kind);
        const matches=actual===declared&&bytes.length===resource.bytes&&actual===String(manifest?.sha256).replace(/^sha256:/,'')&&bytes.length===manifest?.bytes;
        textSidecars[kind]=bytes.toString('utf8');report.summary.verifiedTextSidecars++;
        if(!matches){report.summary.textSidecarHashErrors++;fail('Text sidecar hash/size mismatch: '+p.id+'/'+kind);}
        const embedded=kind==='captions'?payload.captions_text:payload.transcript_text;
        if(textSidecars[kind]!==embedded)fail('Text sidecar does not match embedded payload: '+p.id+'/'+kind);
      }
      const video=player?.querySelector('video');if(video)video.dispatchEvent(new win.Event('play'));
      (payload.steps||[]).forEach((sourceStep,index)=>{
        const narration=String(sourceStep.narration_text||''),display=String(sourceStep.display_markdown||''),spoken=semanticNarration(narration),cn=(narration.match(/[\u3400-\u9fff]/g)||[]).length;
        const stepReport={unitId,requestId:p.request_id,stepId:sourceStep.step_id,chineseNarrationCharacters:cn,displayContainsFullNarration:cn>=10&&semanticNarration(display).includes(spoken),displayEndsWithCompleteFormula:captionCanonical(display).endsWith(captionCanonical(sourceStep.caption_tex)),embeddedCaptionContainsFullNarration:semanticNarration(payload.captions_text).includes(spoken),embeddedTranscriptContainsFullNarration:semanticNarration(payload.transcript_text).includes(spoken),captionSidecarContainsFullNarration:semanticNarration(textSidecars.captions).includes(spoken),transcriptSidecarContainsFullNarration:semanticNarration(textSidecars.transcript).includes(spoken),transcriptRowContainsFullNarration:false,stageCaptionContainsFullNarration:false,strictMathExpressions:0,strictMathErrors:[],literalTexOutsideMath:[]};
        const formulas=[...display.matchAll(/\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g)];
        
        for(const formula of formulas){const tex=formula[1]||formula[2];try{win.katex.renderToString(win.DSHStudyMath.operatorLimits(tex),{displayMode:!!formula[1],throwOnError:true,strict:'error',trust:false});}catch(e){stepReport.strictMathErrors.push(e.message);}stepReport.strictMathExpressions++;}
        const leftover=display.replace(/\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g,'');if(leftover.includes('$')||/\\(?:frac|quad|alpha|beta|pi|tau|mathcal|Delta)\b/.test(leftover))stepReport.strictMathErrors.push('Unmatched delimiter or raw TeX outside formula');
        const transcriptNode=rows[index]?.querySelector('.animation-transcript-text');stepReport.transcriptRowContainsFullNarration=semanticNarration(readableText(transcriptNode)).includes(spoken);
        if(/\$|\\[A-Za-z]+/.test(outsideMathText(transcriptNode)))stepReport.literalTexOutsideMath.push('transcript');
        if(video){video.currentTime=Number(sourceStep.start)+Math.min(.2,(Number(sourceStep.end)-Number(sourceStep.start))/2);video.dispatchEvent(new win.Event('timeupdate'));const layer=player.querySelector('.animation-caption-layer');stepReport.stageCaptionContainsFullNarration=!!layer&&!layer.hidden&&semanticNarration(readableText(layer)).includes(spoken);if(/\$|\\[A-Za-z]+/.test(outsideMathText(layer)))stepReport.literalTexOutsideMath.push('stage');}
        report.summary.narrationSegments++;report.summary.strictCaptionMathExpressions+=stepReport.strictMathExpressions;report.summary.strictCaptionMathErrors+=stepReport.strictMathErrors.length;
        if(stepReport.displayContainsFullNarration)report.summary.completeDisplayNarrations++;
        if(stepReport.transcriptRowContainsFullNarration)report.summary.completeTranscriptNarrations++;
        if(stepReport.stageCaptionContainsFullNarration)report.summary.completeStageCaptions++;
        const booleanChecks=Object.entries(stepReport).filter(([key])=>/ContainsFullNarration|EndsWithCompleteFormula/.test(key));
        if(booleanChecks.some(([,v])=>!v)||stepReport.strictMathErrors.length||stepReport.literalTexOutsideMath.length)fail('Incomplete narration or invalid caption formula: '+p.id+'/'+sourceStep.step_id);
        report.narrationVerification.push(stepReport);
      });
      if(video)video.dispatchEvent(new win.Event('pause'));
      const row={id:p.id,requestId:p.request_id,actualRequest:player?.dataset.request,steps:steps.length,structuredLists:lists.length,transcriptRows:rows.length,rawFallbackRows:fallback.length};item.media.push(row);
      if(!player||player.dataset.request!==p.request_id)fail('Media request mismatch: '+p.id);
      if(lists.length!==1||rows.length!==steps.length||fallback.length)fail('Repeated or unexpected transcript rows: '+p.id);
      if(sampleCount<2&&player){const button=player.querySelector('[data-action="transcript"]'),host=player.querySelector('.animation-transcript');const initiallyHidden=host.hidden;button.click();const opened=!host.hidden;button.click();const closed=host.hidden;const sample={unitId,id:p.id,...row,initiallyHidden,opened,closed,structuredListCountAfterToggles:player.querySelectorAll('.animation-transcript-list').length,rawDumpAbsent:!payload.transcript_text||!player.textContent.includes(payload.transcript_text.trim())};report.transcriptSamples.push(sample);if(!opened||!closed||sample.structuredListCountAfterToggles!==1||!sample.rawDumpAbsent)fail('Transcript sample toggle/raw dump check failed: '+p.id);sampleCount++;}
      report.summary.transcriptPlayers++;report.summary.structuredTranscriptLists+=lists.length;report.summary.transcriptRows+=rows.length;report.summary.rawTranscriptFallbackRows+=fallback.length;
    }
  });
  const markerCount=(root.textContent.match(/\[\[TEACHING_(?:VISUAL|MEDIA):/g)||[]).length;
  const automatic=cards.filter(c=>!c.closest('.teaching-block')||c.dataset.studyCoverage!=='authored');
  const oldPrompts=(root.textContent.match(/教学位置未定位/g)||[]).length;
  if(markerCount)fail('Raw teaching marker remains');if(automatic.length)fail('Unexpected automatic visual');if(oldPrompts)fail('Legacy technical positioning prompt remains');
  const sources=[...root.querySelectorAll('.lesson-sources')],originals=[...root.querySelectorAll('.lesson-textbook-source')],open=[...sources,...originals].filter(e=>e.open);
  if(open.length)fail('Original/supplement source disclosure defaults open');
  if(!sources.length)fail('Missing supplement disclosure');if(unit.study_textbook_original&&!originals.length)fail('Missing original-text disclosure');
  const body=root.querySelector('[data-reader-pointer="/body_markdown"]');
  const links=[...(body||root).querySelectorAll('a[href^="#reader-"]')];
  links.forEach(a=>{const id=decodeURIComponent(a.getAttribute('href').slice(1));if(!doc.getElementById(id)){item.unresolvedLinks.push({kind:'body-reference',href:a.getAttribute('href'),text:a.textContent});report.summary.unresolvedBodyExampleLinks++;fail('Unresolved body source/example link: '+a.getAttribute('href'));}});
  const concepts=[...root.querySelectorAll('a.concept')];concepts.forEach(a=>{const id=a.dataset.cid||decodeURIComponent((a.getAttribute('href')||'').replace('#concept=',''));if(!data.concepts[id]){report.summary.unresolvedConceptLinks++;item.unresolvedLinks.push({kind:'concept',id,href:a.getAttribute('href')});fail('Unresolved concept: '+id);}});
  const allReaderLinks=[...root.querySelectorAll('a[href^="#reader-"]')];
  allReaderLinks.forEach(a=>{const id=decodeURIComponent(a.getAttribute('href').slice(1));if(!doc.getElementById(id)){report.summary.unresolvedReaderLinks++;item.unresolvedLinks.push({kind:'reader-reference',href:a.getAttribute('href'),text:a.textContent.slice(0,100)});fail('Unresolved supplement/source reference: '+a.getAttribute('href'));}});
  report.summary.allReaderLinks+=allReaderLinks.length;
  const inferred=body?.querySelectorAll('[data-reader-topic]').length||0;report.summary.bodyInferredTopics+=inferred;if(inferred)fail('Legacy inferred topics fragmented authored narrative');
  if(report.referenceClickSamples.length<2&&links.length){const a=links[0],href=a.getAttribute('href');a.dispatchEvent(new win.MouseEvent('click',{bubbles:true,cancelable:true}));const popup=[...doc.querySelectorAll('.reader-popup')].find(p=>p.dataset.readerTarget===href.slice(1));const row={unitId,href,popupCreated:!!popup,targetResolves:!!doc.getElementById(href.slice(1)),popupTextLength:popup?.textContent.length||0};report.referenceClickSamples.push(row);if(!popup||!row.popupTextLength)fail('Source/example link did not open content window: '+href);}
  const sourceLinks=[...root.querySelectorAll('[data-source-id]')];sourceLinks.forEach(a=>{const id=a.dataset.sourceId;const catalogs=unit.reader_document?.reference_catalog?.entries||[];if(id&&!catalogs.some(e=>e.id===id)&&!data.source_materials?.[id]&&!data.source_assets?.[id]){report.summary.unresolvedSourceLinks++;item.unresolvedLinks.push({kind:'source-id',id});}});
  const op=root.querySelectorAll('.op-limits').length,mathErrors=root.querySelectorAll('.katex-error').length;
  if(mathErrors)warnings.push({unitId,message:mathErrors+' KaTeX error nodes including preserved supplements'});
  report.summary.units++;report.summary.visualPlans+=item.planVisuals;report.summary.mediaPlans+=item.planMedia;report.summary.mountedVisuals+=cards.length;report.summary.mountedMedia+=players.length;report.summary.markers+=beforeMarkers.length;report.summary.remainingMarkers+=markerCount;report.summary.automaticVisuals+=automatic.length;report.summary.legacyPositionPrompts+=oldPrompts;report.summary.sourceDisclosures+=sources.length;report.summary.originalDisclosures+=originals.length;report.summary.openSourceDisclosures+=open.length;report.summary.bodyExampleLinks+=links.length;report.summary.conceptLinks+=concepts.length;report.summary.sourceLinks+=sourceLinks.length;report.summary.katexOpLimits+=op;report.summary.katexErrors+=mathErrors;
  item.katexOpLimits=op;item.katexErrors=mathErrors;report.units.push(item);
}
report.summary.stubbedPlaybackCalls={play:playCalls,pause:pauseCalls,load:loadCalls};
if(report.summary.units!==76||report.summary.mountedVisuals!==report.expected.visuals||report.summary.mountedMedia!==12)errors.push({message:'Book totals differ from the authored scope and 12 videos'});
if(report.summary.narrationSegments!==57||report.summary.completeDisplayNarrations!==57||report.summary.completeTranscriptNarrations!==57||report.summary.completeStageCaptions!==57)errors.push({message:'Expected 57 complete spoken captions and transcripts'});
if(!report.summary.katexOpLimits)errors.push({message:'No KaTeX op-limits found'});
if(runtimeErrors.length)errors.push({message:'Runtime errors occurred',count:runtimeErrors.length});
const refs=[...template.matchAll(/(?:href|src)="((?:assets|data)\/[^\"]+)"/g)].map(m=>m[1]);
const indexFiles=refs.map(ref=>{const [file,query]=ref.split('?'),bytes=fs.readFileSync(path.join(book,file)),digest=sha(bytes),version=new URLSearchParams(query).get('v');const src=file==='assets/text-led-multiplication.js'?path.join(repo,'examples/text-led-multiplication/multiplication.js'):['blocks/render/assets','blocks/render/vendor','blocks/render/vendor/mathbox'].map(d=>path.join(repo,d,path.basename(file))).find(f=>fs.existsSync(f));return{file,version,sha256:digest,versionMatches:!version||digest.startsWith(version),source:src||null,matchesSource:src?bytes.equals(fs.readFileSync(src)):null};});
report.indexAssetVerification={verifiedAt:new Date().toISOString(),referencedFiles:indexFiles.length,staticSourceFiles:indexFiles.filter(r=>r.source).length,allVersionHashesMatch:indexFiles.every(r=>r.versionMatches),allStaticFilesMatchSource:indexFiles.filter(r=>r.source).every(r=>r.matchesSource),auditedDataStillCurrent:indexFiles.find(r=>r.file==='data/data.js').sha256===report.dataSha256,files:indexFiles};
if(!report.indexAssetVerification.allVersionHashesMatch||!report.indexAssetVerification.allStaticFilesMatchSource||!report.indexAssetVerification.auditedDataStillCurrent)errors.push({message:'Final index asset parity check failed'});
report.conclusionCoverage={planned:report.units.reduce((n,u)=>n+(u.textbookConclusions?.planned||0),0),visible:report.units.reduce((n,u)=>n+(u.textbookConclusions?.visible||0),0),strictMath:report.units.reduce((n,u)=>n+(u.textbookConclusions?.math||0),0)};
if(!report.conclusionCoverage.planned)errors.push({message:'No textbook conclusions were audited'});
report.transformTableCoverage={planned:report.units.reduce((n,u)=>n+(u.transformTableRows?.planned||0),0),visible:report.units.reduce((n,u)=>n+(u.transformTableRows?.visible||0),0),strictMath:report.units.reduce((n,u)=>n+(u.transformTableRows?.strictMath||0),0)};
if(report.transformTableCoverage.planned!==78)errors.push({message:'Expected all 78 PDF table rows'});
report.reusableConclusionCoverage={planned:report.units.reduce((n,u)=>n+(u.reusableConclusions?.planned||0),0),visible:report.units.reduce((n,u)=>n+(u.reusableConclusions?.visible||0),0)};
report.status=errors.length?'failed':'passed';
dom.window.close();fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({output,status:report.status,summary:report.summary,errors:errors.slice(0,30),warnings:warnings.slice(0,8),runtimeErrors},null,2));

process.exitCode=errors.length?1:0;
