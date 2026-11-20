/* One isolated SDK attempt; stdout is only sanitized response JSON. */
const fs = require('node:fs');
const {createRequire} = require('node:module');
async function main() {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'));
  const root = request.harness_app + '/Contents/Resources/app.asar/dsh/node_modules/';
  const load = name => import(root + '@deepseek-ai/' + name + '/lib/index.js');
  const {Context} = await load('cordis');
  const llm = await load('dsh-llm');
  const ctx = new Context();
  await ctx.plugin(llm.default);
  await ctx.plugin((await load('dsh-attachment-local')).default, {dshHome:request.isolated_home});
  await ctx.plugin(await load('dsh-llm-deepseek-account'), {});
  const abort = new AbortController();
  process.on('SIGTERM', () => abort.abort());
  // Validate images and persist only in the disposable home BEFORE reading credentials.
  const messages = [];
  for (const message of request.messages) {
    const content = [];
    for (const part of typeof message.content === 'string' ? [{type:'text',text:message.content}] : message.content) {
      if (part.type === 'text') content.push(part);
      else if (part.type === 'image_url') {
        const match = /^data:(image\/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)$/.exec(part.image_url.url);
        if (!match) throw Object.assign(new Error(), {code:'UNSUPPORTED_CONTENT'});
        const ref = await ctx.attachments.saveImage({data:new Uint8Array(Buffer.from(match[2],'base64')),mediaType:match[1]});
        content.push({type:'image', attachment:ref});
      } else throw Object.assign(new Error(), {code:'UNSUPPORTED_CONTENT'});
    }
    const source = message.role === 'assistant' ? {kind:'model',provider:'deepseek-account',model:request.model} : message.role === 'system' ? {kind:'system-prompt'} : {kind:'user'};
    messages.push(llm.createMessage({role:message.role,content,source}));
  }
  const initialInfo = await ctx.llm.resolveModelInfo('deepseek-account',request.model,abort.signal);
  if (messages.some(m=>m.content.some(p=>p.type==='image')) && !initialInfo.inputModalities?.includes('image')) throw Object.assign(new Error(),{code:'UNSUPPORTED_IMAGE_MODEL'});
  const yaml = createRequire(root+'@deepseek-ai/dsh-credentials-local/lib/index.js')('yaml');
  if (!fs.existsSync(request.account_home+'/.credentials.yaml')) throw Object.assign(new Error(),{code:'ACCOUNT_SIGN_IN_REQUIRED'});
  const document = yaml.parse(fs.readFileSync(request.account_home+'/.credentials.yaml','utf8'));
  const records = {};
  for (const key of ['deepseek-account-platform/default','deepseek-account-platform/device']) {
    if (document.records?.[key]) records[key] = document.records[key];
  }
  if (!records['deepseek-account-platform/default']) throw Object.assign(new Error(),{code:'ACCOUNT_SIGN_IN_REQUIRED'});
  const filename = request.isolated_home+'/.credentials.yaml';
  fs.writeFileSync(filename,yaml.stringify({version:1,refs:{},records}),{mode:0o600,flag:'wx'});
  await ctx.plugin((await load('dsh-credentials-local')).default,{path:filename,watch:false});
  await ctx.plugin((await load('dsh-authorization')).default);
  await ctx.plugin((await load('dsh-deepseek-account-platform')).default,{});
  const prepared = await ctx.llm.prepareCall({provider:'deepseek-account',model:request.model,maxTokens:request.max_tokens,reasoningEffort:request.reasoning_effort === 'none' ? 'off' : request.reasoning_effort},abort.signal);
  const info = prepared;
  if (messages.some(m=>m.content.some(p=>p.type==='image')) && !info.inputModalities?.includes('image')) throw Object.assign(new Error(),{code:'UNSUPPORTED_IMAGE_MODEL'});
  let text='',usage={},finish;
  for await (const chunk of prepared.stream({...prepared.config,messages,signal:abort.signal})) {
    if(chunk.type==='text-delta') text+=chunk.text;
    if(chunk.type==='usage') usage=chunk.usage;
    if(chunk.type==='finish') finish=chunk;
  }
  if(!finish) throw Object.assign(new Error(),{code:'MISSING_TERMINAL_EVENT'});
  if(finish.reason.kind==='error' || finish.reason.kind==='aborted') throw Object.assign(new Error(),{code:finish.reason.failure?.code || 'ABORTED'});
  if (!['stop','max-tokens'].includes(finish.reason.kind)) throw Object.assign(new Error(),{code:'UNSUPPORTED_TERMINAL_EVENT'});
  return {text,usage,finish_reason:finish.reason.kind==='max-tokens'?'length':'stop'};
}
main().then(result=>{process.stdout.write(JSON.stringify(result));process.exit(0);}).catch(error=>{process.stdout.write(JSON.stringify({error:{code:/^[A-Z_]{1,80}$/.test(error.code)?error.code:'ACCOUNT_BRIDGE_FAILED'}}));process.exit(0);});
