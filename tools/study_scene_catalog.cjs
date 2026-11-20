const fs=require('node:fs');
const path=require('node:path');
const visual=require('../blocks/render/assets/study-visuals.js');
for(const name of ['study-foundation-scenes','study-special-scenes']) visual.registerScenes(require('../blocks/render/assets/'+name+'.js'));
const file=path.resolve(process.argv[2]);
const source=fs.readFileSync(file,'utf8').split(/window\.__DATA__\s*=\s*/)[1];
const data=JSON.parse(source.trim().replace(/;\s*$/,''));
const catalog={};
for(const unit of Object.values(data.units)) {
  const groups=new Map();
  for(const block of unit.reader_document?.blocks||[]) {
    const key=block.owner_id||block.block_id;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(block);
  }
  for(const blocks of groups.values()) {
    const name=blocks.find(b=>/\/(?:name|title|label)$/.test(b.json_pointer||''));
    const title=name?.markdown||unit.title;
    const body=blocks.map(b=>b.markdown||'').join('\n');
    const scene=visual.chooseScene(title,body);
    if(scene.kind!=='unmatched')catalog[scene.kind]||={formula:scene.formula,explanation:scene.explanation};
    for(const heading of body.matchAll(/^#{1,6}\s+(.+)$/gm)) {
      const extra=visual.chooseScene(heading[1],body);
      if(extra.kind!=='unmatched')catalog[extra.kind]||={formula:extra.formula,explanation:extra.explanation};
    }
  }
}
fs.writeFileSync(process.argv[3],JSON.stringify(catalog,null,2));
console.log('Scene listening guides:',Object.keys(catalog).length);
