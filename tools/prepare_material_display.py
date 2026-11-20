"""Format the shared answer page without overwriting its raw source."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def correct_material_display(materials):
    result=deepcopy(materials);changes=[]
    errata=ROOT/'data/study-revision-20261029/wholebook/answer-page-display-errata.json'
    extra=json.loads(errata.read_text()) if errata.is_file() else {}
    for ident,material in result.items():
        raw=material.get('raw_markdown','')
        def format_text(text):
            return text.replace('Resinz = sinxcoshy, Imsinz = cosxsinh y,',r'$\operatorname{Re}(\sin z)=\sin x\cosh y$, $\operatorname{Im}(\sin z)=\cos x\sinh y$,').replace('| sinz |',r'$|\sin z|$')
        page=(material.get('source_pages') or [1])[0]
        material['study_pdf_url']='assets/textbook.pdf#page='+str(page)
        # Physically checked errata are applied only to their exact display text.
        if extra.get('match',{}).get('material_id')==ident:
            if material.get('source_sha256')!=extra['match']['source_sha256']:raise ValueError('Answer source signature differs')
            for rule in extra.get('display_replacements',[]):
                text=material.get('display_markdown','');old=rule['old'];new=rule['new']
                if old not in text and new in text:continue
                if text.count(old)!=rule.get('expected_display_match_count',1):raise ValueError('Answer display erratum does not match: '+rule['id'])
                material['display_markdown']=text.replace(old,new)
            if 'formatting_notes_after' in extra:material.setdefault('formatting',{})['notes']=extra['formatting_notes_after']
        material['study_raw_display']=format_text(raw)
        if material.get('display_markdown'):material['display_markdown']=format_text(material['display_markdown'])
        if material['study_raw_display']!=raw or material.get('display_markdown')!=materials[ident].get('display_markdown'):
            changes.append({'id':ident,'raw_source_unchanged':True,'display_sha256':hashlib.sha256(material.get('display_markdown','').encode()).hexdigest()})
    return result,{'changes':changes,'raw_sources_preserved':True}
