"""Explicit legacy import: candidate content plus preserved migration attachments."""
from copy import deepcopy
from pathlib import Path
import re
from ..core.digest import digest_file
from ..core.jsonio import read_json, write_json_atomic
from ..errors import ContractError
from ..blocks.content.content_contracts import LEGACY_MEDIA_FIELDS as MEDIA_FIELDS

RECEIPT_FIELDS={'approval','content_approval','media_approval','accepted','quality_state','final_checks','final_quality','checks','usage'}

def import_legacy_candidate(source: Path, work: Path, *, book_id: str, source_run_id: str='', v4_fields: dict | None=None, expected_unit_id: str | None=None, dry_run: bool=False) -> dict:
    if not isinstance(v4_fields,dict):
        raise ContractError('必须显式提供 schema 4 教学/读者字段 v4_fields；旧候选不可直接执行')
    source,work=Path(source),Path(work)
    raw=read_json(source)
    candidate=deepcopy(raw.get('payload',{}).get('content') or raw.get('payload',{}).get('lesson') or raw)
    if not isinstance(candidate,dict) or not candidate.get('unit_id'):
        raise ContractError('旧内容必须有 unit_id')
    unit_id=candidate['unit_id']
    if not re.fullmatch(r'CH\d+(?:-S\d+)?(?:-SS\d+)?',unit_id):
        raise ContractError('旧内容 unit_id 无效，禁止路径推测')
    if expected_unit_id is not None and unit_id != expected_unit_id:
        raise ContractError('导入单元与目标范围不一致')
    path=work/'products/imported_content'/f'{unit_id}.json'
    if path.exists() or (work/'products/lesson'/f'{unit_id}.json').exists():
        raise ContractError('目标已有候选或内容，不能覆盖；请创建新 run')
    attachments={}
    def clean(value,pointer=''):
        if isinstance(value,dict):
            for key in list(value):
                child=pointer+'/'+key
                if key in MEDIA_FIELDS:
                    attachments[child]=value.pop(key)
                elif key in RECEIPT_FIELDS:
                    attachments[child]=value.pop(key)
                else:
                    value[key]=clean(value[key],child)
        elif isinstance(value,list):
            value=[clean(item,pointer+'/'+str(i)) for i,item in enumerate(value)]
        elif isinstance(value,str):
            markers=re.findall(r'\[\[(?:FIGURE|MEDIA|DIAGRAM):[^\]]*\]\]',value)
            if markers:
                attachments[pointer+'/markers']=markers
                value=re.sub(r'\[\[(?:FIGURE|MEDIA|DIAGRAM):[^\]]*\]\]','',value)
        return value
    clean(candidate)
    from ..blocks.content.content_contracts import import_candidate_v4
    converted=import_candidate_v4(candidate,v4_fields=v4_fields)
    candidate=converted['content']
    attachment=work/'migration'/f'{unit_id}-media.json'
    report={'schema_version':'1.0','decision':'candidate','book_id':book_id,'unit_id':unit_id,
            'source_run_id':source_run_id,'source_path':str(source.resolve()),'source_sha256':digest_file(source),
            'candidate_path':str(path.resolve()),'media_attachment':str(attachment.resolve()),
            'content_approval_inherited':False,'media_approval_inherited':False,
            'historical_costs':'来源 run 历史费用保留在原账本；新 run 费用单独统计',
            'next_stage':'lesson','required_review':'text_audit',
            'schema_conversion':converted['migration_report']}
    if dry_run: return report
    write_json_atomic(attachment,attachments)
    write_json_atomic(path,candidate)
    write_json_atomic(work/'migration'/f'{unit_id}-report.json',report)
    return report
