const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const {JSDOM}=require('jsdom');
const notes=fs.readFileSync(path.resolve(__dirname,'../../blocks/render/assets/notes.js'),'utf8');
function reader(t,responsive,width){const dom=new JSDOM(`<html data-reader-ready="ok" data-reader-unit="U"><head>${responsive?'<link rel="stylesheet" href="assets/study-reader.css">':''}</head><body class="book-page"><header class="bookbar"><div class="book-controls"></div></header><div class="layout"><main id="content"><p data-reader-pointer="/body_markdown">正文</p></main></div></body></html>`,{url:'https://offline.test',runScripts:'outside-only',pretendToBeVisual:true});t.after(()=>dom.window.close());const win=dom.window;win.indexedDB={open(){throw Error('test storage disabled');}};win.document.elementsFromPoint=()=>[];Object.defineProperty(win.document.querySelector('.layout'),'clientWidth',{get(){return width;}});win.__DATA__={book:'book',scope:['U'],units:{U:{}}};win.eval(notes);return win;}
test('study reader reflows without CSS zoom and annotation coordinates stay at actual scale',async t=>{const win=reader(t,true,390);await win.DSHNotes.ready;assert.equal(win.document.documentElement.hasAttribute('data-fixed-layout'),false);assert.equal(win.DSHNotes.state.scale,1);assert.equal(win.document.documentElement.style.getPropertyValue('--reader-page-scale'),'1');win.dispatchEvent(new win.Event('resize'));assert.equal(win.DSHNotes.state.scale,1);});
test('legacy reader without the study theme keeps its saved logical annotation scale',async t=>{const win=reader(t,false,390);await win.DSHNotes.ready;assert.equal(win.document.documentElement.hasAttribute('data-fixed-layout'),true);assert.ok(Math.abs(win.DSHNotes.state.scale-390/1026)<1e-10);});
test('content-hashed theme URLs still use actual font sizes and responsive annotation coordinates',async t=>{
 const dom=new JSDOM('<html data-reader-ready="ok" data-reader-unit="U"><head><link rel="stylesheet" href="assets/study-reader.css?v=123abc"></head><body class="book-page"><header class="bookbar"><div class="book-controls"></div></header><div class="layout"><main id="content"><p>正文</p></main></div></body></html>',{url:'https://offline.test',runScripts:'outside-only',pretendToBeVisual:true});t.after(()=>dom.window.close());
 const win=dom.window;win.indexedDB={open(){throw Error('test storage disabled');}};win.document.elementsFromPoint=()=>[];Object.defineProperty(win.document.querySelector('.layout'),'clientWidth',{get(){return 390;}});win.__DATA__={book:'book',scope:['U'],units:{U:{}}};win.eval(notes);await win.DSHNotes.ready;
 assert.equal(win.document.documentElement.hasAttribute('data-fixed-layout'),false);assert.equal(win.DSHNotes.state.scale,1);
});
test('responsive annotation overlay shrinks to the reader instead of preserving its own previous overflow',async t=>{
 const w=reader(t,true,390);await w.DSHNotes.ready;const main=w.document.querySelector('main');
 Object.defineProperty(main,'clientWidth',{get(){return 390;}});
 Object.defineProperty(main,'scrollWidth',{get(){return 880;}});
 w.dispatchEvent(new w.Event('resize'));
 assert.equal(main.querySelector('.notes-layer').getAttribute('width'),'390','a stale wide annotation SVG must not keep the page wider than the phone');
});
