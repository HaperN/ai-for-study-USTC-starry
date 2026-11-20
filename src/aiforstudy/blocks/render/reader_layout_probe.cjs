'use strict';
const fs = require('node:fs');
const [parserPath, markedPath] = process.argv.slice(2);
const parser = require(parserPath), marked = require(markedPath);

function quotedMarkdown(token) {
  if (token.type === 'link' && /^#reader-[a-f0-9]{20}$/.test(token.href)) return token.text;
  let out = '', cursor = 0;
  for (const child of token.tokens || token.items || []) {
    const start = token.raw.indexOf(child.raw, cursor);
    if (start < 0) return token.raw;
    out += token.raw.slice(cursor, start) + quotedMarkdown(child);
    cursor = start + child.raw.length;
  }
  return out + token.raw.slice(cursor);
}

function excerpt(parsed, block) {
  // Use the lexer's token boundaries so literal link syntax inside code is
  // preserved even if an identical reader reference appears later in the text.
  let text = quotedMarkdown(parsed.tokens[block.token_index]);
  for (const math of parsed.math) text = text.split(math.slot).join(math.raw);
  return text.trim();
}

try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const tokenize = fields => fields.flatMap(field => {
    const parsed = parser.parseReaderField(field.markdown, {marked, field});
    return parsed.blocks.map(block => input.excerpts ? {...block, excerpt:excerpt(parsed,block)} : block);
  });
  const result = input.documents ? {documents:input.documents.map(tokenize)} : {blocks:tokenize(input.blocks)};
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stderr.write(error.message);
  process.exitCode = 1;
}
