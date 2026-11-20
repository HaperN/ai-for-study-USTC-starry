// The real reader renderer emits one source coordinate for each layout block.
function sourceUnit(paragraphs) {
  const blocks=paragraphs.map((raw_text,i)=>({block_id:'field'+i,json_pointer:'/paragraphs/'+i,raw_text,markdown:raw_text}));
  return {reader_document:{blocks},reader_layout:{blocks:blocks.map((f,i)=>({
    block_id:f.block_id+':0',field_id:f.block_id,target_pointer:f.json_pointer,token_index:0,owner_id:'',token_type:'paragraph',source_start_cp:0,source_end_cp:Array.from(f.raw_text).length
  }))}};
}
module.exports={sourceUnit};
