// Run copied production assets against the generated page in an offline DOM.
// No browser navigation, network requests or file:// URL loading.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(process.argv[2]);
const dom = new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),
  {url:'https://reader.invalid/',runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;
w.scrollTo=function(a,b){w.scrollY=typeof a==='object'?a.top:b;};
w.HTMLElement.prototype.scrollIntoView=function(){this.dataset.scrolled='true';};
w.console={warn(){},error(){},log(){}};
try {
  for (const script of w.document.querySelectorAll('script[src]')) {
    const relative=script.getAttribute('src');
    assert.match(relative,/^(?:assets|data)\/[A-Za-z0-9._/-]+\.js$/);
    w.eval(fs.readFileSync(path.join(root,relative),'utf8'));
  }
  const body=w.document.querySelector('[data-reader-pointer="/body_markdown"]');
  assert.ok(body,'actual teaching body is mounted');
  if (process.argv[3]==='unscored') {
    const statement=w.document.querySelector('[data-reader-pointer="/learning_checks/0/statement"]');
    const check=statement&&statement.closest('details');
    assert.ok(check,'question remains in its normal expandable reader group');
    check.querySelector('summary').click();
    assert.ok(check.open);
    assert.match(check.textContent,/本题暂不支持自动评分，请对照答案自查。/);
    assert.match(check.textContent,/实部不变，虚部变号/);
    assert.equal(check.querySelector('[data-check-action="submit"]'),null);
    assert.equal(check.hasAttribute('data-check-config'),false);
    process.stdout.write(JSON.stringify({unscored_answer_visible:true}));
  } else if (process.argv[3]==='scored') {
    const checks=[...w.document.querySelectorAll('[data-learning-check]')];
    assert.equal(checks.length,2);
    for (const check of checks) {
      assert.doesNotMatch(check.textContent,/暂不支持自动评分/);
      const expected=JSON.parse(check.dataset.checkConfig).scoring.accepted_inputs[0];
      check.querySelector('[data-check-input][value="'+expected+'"]').click();
      check.querySelector('[data-check-action="submit"]').click();
      assert.equal(check.querySelector('[data-check-result]').dataset.status,'correct');
    }
    process.stdout.write(JSON.stringify({automatic_scoring:'correct',questions:checks.length}));
  } else if (process.argv[3]==='duplicate_questions') {
    const groups=[0,1].map(i=>w.document.querySelector('[data-reader-pointer="/learning_checks/'+i+'/statement"]').closest('details'));
    assert.notEqual(groups[0],groups[1],'different array positions must remain separate questions');
    assert.notEqual(groups[0].id,groups[1].id,'separate questions have separate DOM anchors');
    groups.forEach((group,i)=>{
      group.querySelector('summary').click();
      assert.ok(group.open);
      assert.match(group.textContent,/本题暂不支持自动评分，请对照答案自查。/);
      assert.ok(group.textContent.includes(i===0?'第一题答案：正确。':'第二题答案：错误。'));
      assert.ok(!group.textContent.includes(i===0?'第二题答案':'第一题答案'));
      assert.equal(group.querySelector('[data-check-action="submit"]'),null);
      assert.equal(group.hasAttribute('data-check-config'),false);
    });
    process.stdout.write(JSON.stringify({separate_manual_questions:groups.length}));
  } else if (process.argv[3]==='duplicate_examples') {
    for (const [i,name] of ['第一','第二'].entries()) {
      assert.ok(w.document.querySelector('[data-reader-pointer="/examples/'+i+'/statement"]').textContent.includes(name+'例题题目。'));
      assert.ok(w.document.querySelector('[data-reader-pointer="/examples/'+i+'/conclusion"]').textContent.includes(name+'例题结论。'));
    }
    process.stdout.write(JSON.stringify({retained_examples:2}));
  } else {
  const links=[...body.querySelectorAll('a[href^="#reader-"]')];
  assert.equal(links.length,4);
  let opened=0;
  for (const link of links) {
    const target=w.document.getElementById(link.getAttribute('href').slice(1));
    assert.ok(target,'target exists for '+link.textContent);
    const home=target.parentNode;
    if (target.tagName==='DETAILS') { target.open=false;opened++; }
    w.scrollY=345;link.click();
    const popup=w.document.querySelector('.reader-popup[data-reader-target="'+target.id+'"]');
    assert.ok(popup,'reference opens a reading window');
    assert.equal(popup.getAttribute('role'),'dialog');
    assert.equal(target.parentNode,home,'source content stays at its original home');
    assert.ok(target.tagName!=='DETAILS'||!target.open,'source details stays collapsed');
    assert.equal(w.scrollY,345);assert.equal(target.dataset.scrolled,undefined);
    link.click();assert.equal(w.document.querySelector('.reader-popup[data-reader-target="'+target.id+'"]'),popup);
    if (link.textContent==='看解释') {
      assert.match(popup.textContent,/作者解释：横坐标仍是 x/);
      assert.doesNotMatch(popup.textContent,/冻结定义/,'catalog formal must not replace author explanation');
    }
    if (link.textContent==='看例题') assert.match(popup.textContent,/保持 2，把 3 改为 -3/);
    if (link.textContent==='看易错点') assert.match(popup.textContent,/不要同时改变实部符号/);
    const ids=[...w.document.querySelectorAll('[id]')].map(node=>node.id);
    assert.equal(new Set(ids).size,ids.length,'source anchors remain unique');
    const close=popup.querySelector('[data-reader-popup-close]');assert.ok(close);close.click();
    assert.equal(w.document.querySelector('.reader-popup'),null);assert.equal(w.document.activeElement,link);
    assert.equal(w.scrollY,345);assert.equal(target.parentNode,home);
    assert.equal(w.document.getElementById(target.id),target);
  }
  assert.equal(Object.keys(w.__DATA__.concepts).length,0,'links use current reader projection');
  process.stdout.write(JSON.stringify({links:links.length,opened,author_explanation:true}));
  }
} finally { w.close(); }
