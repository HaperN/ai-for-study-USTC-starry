const {spawnSync} = require('node:child_process');
const path = require('node:path');
const assert = require('node:assert/strict');
for (const args of [['--delayed'],['--current-reader'],['--current-reader','--delayed']]) {
  const result = spawnSync(process.execPath,[path.join(__dirname,'reader_media_lifecycle.test.cjs'),...args],{encoding:'utf8'});
  assert.equal(result.status,0,args.join(' ')+': '+result.stdout+result.stderr);
}
