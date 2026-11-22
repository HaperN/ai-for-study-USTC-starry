"""Rebuild the displayed reader after exact, independently reviewed errata."""
from pathlib import Path
from copy import deepcopy
import argparse,hashlib,json,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.prepare_support_corrections import correct_support_document,EXACT_BLOCK_CORRECTIONS
from tools.revise_textbook_reader import authored_presentation
from aiforstudy.blocks.render.layout import layout_from_reader_media
from aiforstudy.practice.render import js_data

def apply(source,output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if source==output or source.is_relative_to(output) or output.is_relative_to(source):raise ValueError('Reader roots overlap')
    if output.exists() and any(output.iterdir()):raise ValueError('Use an empty output folder')
    raw=(source/'data/data.js').read_text();data=json.loads(raw[raw.index('{'):raw.rfind('}')+1]);original=deepcopy(data)
    prefix='SUPPORT-KNOWLEDGE-CONDITIONS-20261122-'
    rules=[r for r in EXACT_BLOCK_CORRECTIONS if r['id'].startswith(prefix)]
    reports=[]
    for uid in sorted({r['unit_id'] for r in rules}):
        unit=data['units'][uid];document,report=correct_support_document(unit['reader_document'],uid)
        if report['signature_mismatches']:raise ValueError('Errata signature mismatch '+uid)
        for rule in (r for r in rules if r['unit_id']==uid):
            matches=[b for b in document['blocks'] if b.get('owner_id')==rule['owner_id'] and b.get('json_pointer')==rule['json_pointer']]
            if len(matches)!=1 or matches[0]['raw_text']!=rule['after']:raise ValueError('Errata missing '+rule['id'])
        if report['changes']:
            unit['reader_document']=document
            unit['reader_layout']=layout_from_reader_media(document,unit.get('reader_media',[]))
            unit['teaching_presentation']=authored_presentation(document,unit['study_lesson'])
            unit['study_support_corrections_20261122']=report
        for field in ('study_source_document','study_textbook_original','study_lesson','reader_media','study_source_media'):
            if unit.get(field)!=original['units'][uid].get(field):raise ValueError('Frozen source or media changed '+uid+'/'+field)
        again,repeated=correct_support_document(document,uid)
        if again!=document or repeated['changes']:raise ValueError('Errata projection is not idempotent')
        reports.append(report)
    shutil.copytree(source,output,dirs_exist_ok=True)
    (output/'data/data.js').write_text('window.__DATA__='+js_data(data)+';\n')
    report={'schema_version':'ReviewedReaderErrataPublicationV1','source_data_sha256':hashlib.sha256((source/'data/data.js').read_bytes()).hexdigest(),
            'output_data_sha256':hashlib.sha256((output/'data/data.js').read_bytes()).hexdigest(),
            'verified_rules':len(rules),'changed_blocks':sum(len(r['changes']) for r in reports),'units':reports,
            'source_and_media_preserved':True,'independent_evidence':'examples/reader_errata/20261122'}
    (output/'reader-errata-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=apply(a.source,a.out);print(json.dumps({k:v for k,v in r.items() if k!='units'},ensure_ascii=False))
