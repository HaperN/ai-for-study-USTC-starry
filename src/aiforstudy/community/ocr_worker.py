"""OCR-only child. Paths/config come from the host; no accounts or network tools."""
from pathlib import Path
import argparse,json,os


def transcribe(args):
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    from PIL import Image
    import torch
    from transformers import AutoModelForImageTextToText,AutoProcessor
    device=args.device
    if device=='auto':device='cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    dtype=torch.bfloat16 if device in ('mps','cuda') else torch.float32
    model=AutoModelForImageTextToText.from_pretrained(args.model,dtype=dtype,trust_remote_code=False,local_files_only=True,attn_implementation='sdpa').to(device).eval()
    processor=AutoProcessor.from_pretrained(args.model,trust_remote_code=False,local_files_only=True)
    with Image.open(args.image) as original:
        rgba=original.convert('RGBA');image=Image.new('RGB',rgba.size,'white');image.paste(rgba,mask=rgba.getchannel('A'))
    messages=[{'role':'user','content':[{'type':'image','image':image},{'type':'text','text':'OCR:'}]}]
    # Keep the validated PaddleOCR-VL processor pixel budget; never feed full
    # camera-resolution grids into the language model.
    inputs=processor.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,return_dict=True,return_tensors='pt',
        images_kwargs={'size':{'shortest_edge':processor.image_processor.size['shortest_edge'],'longest_edge':1280*28*28}}).to(device)
    with torch.inference_mode():output=model.generate(**inputs,max_new_tokens=2048,do_sample=False)[0,inputs['input_ids'].shape[-1]:]
    text=processor.decode(output,skip_special_tokens=True)
    return {'text':text,'truncated':len(output)>=2048,'tokens':len(output),'device':device}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);parser.add_argument('--image',required=True);parser.add_argument('--output',required=True);parser.add_argument('--device',default='auto',choices=['auto','cpu','mps','cuda']);args=parser.parse_args()
    try:result=transcribe(args)
    except Exception as exc:result={'error':type(exc).__name__}
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')

if __name__=='__main__':main()
