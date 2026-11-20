const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('src/aiforstudy/checks/browser_acceptance.cjs','utf8');
const geometry=source.slice(source.indexOf('function verifyMediaGeometry('),source.indexOf('\nfunction verifyReaderContent('));
function measure({columnWidth,video=false,scrollbar=15,imageScale=1}){
 const width=(columnWidth||780)-(video?2:0),height=width*500/800;
 const rect=(w,h=height)=>({left:0,top:0,right:w,bottom:h,width:w,height:h});
 const style={objectFit:'fill',clipPath:'none',overflowX:'visible',overflowY:'visible',borderLeftWidth:'0px',borderRightWidth:'0px',paddingLeft:'0px',paddingRight:'0px'};
 const body={}, column=columnWidth?{getBoundingClientRect:()=>rect(columnWidth),clientWidth:columnWidth}:null;
 const player=video?{}:null;
 const media={tagName:video?'VIDEO':'svg',videoWidth:800,videoHeight:500,viewBox:{baseVal:{width:800,height:500}},
  getBoundingClientRect:()=>rect(width*imageScale,height*imageScale),querySelectorAll:()=>[]};
 const viewport={parentElement:body,querySelector:()=>media,getBoundingClientRect:()=>rect(width),
  closest:selector=>selector==='.reader-media-column'?column:selector==='.animation-player'?player:null};
 const document={body,documentElement:{clientWidth:1280-scrollbar,scrollWidth:1280-scrollbar,scrollHeight:1500},querySelectorAll:()=>[viewport]};
 const fn=vm.runInNewContext('('+geometry+')',{innerWidth:1280,document,getComputedStyle:n=>n===player?{...style,borderLeftWidth:'1px',borderRightWidth:'1px'}:style});
 return fn({width:1280,content_width:450,max_content_width:458,min_font_px:14},{maximum_media_width_px:780,viewport_gutter_px:16});
}
for(const [scrollbar,columnWidth] of [[15,451.9375],[0,459.5]])for(const video of [false,true]){
 test('desktop '+(video?'video':'figure')+' uses its actual comparison column with scrollbar '+scrollbar,()=>{
  const result=measure({columnWidth,scrollbar,video});
  assert.deepEqual(Array.from(result.errors),[]);
  assert.deepEqual(Array.from(result.warnings),[]);
  assert.equal(result.expected_media_width,columnWidth-(video?2:0));
 });
}
test('single-column 780px view retains its own surface size and width overrides stay advisory',()=>{
 assert.equal(measure({}).warnings.length,0);
 const changed=measure({columnWidth:451.9375,imageScale:.9});
 assert.equal(changed.errors.length,0);
 assert.ok(changed.warnings.some(s=>s.includes('reviewed media width changed')));
});
