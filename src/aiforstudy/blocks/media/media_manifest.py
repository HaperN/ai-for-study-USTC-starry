"""Byte-verifiable per-request result ledger and media-only approval."""
from pathlib import Path
from ...core.digest import digest_file,digest_of
from .check_report import check_report
from .review_policy import media_review_policy
from .evidence_digest import manifest_digest,validation_digest
from ..visual.result import parse_animation_result,validate_against_files
from ..content.content_contracts import JsonRecord
from ...core.resource_files import verified_resource_file
from ...errors import ContractError
from ...core.review_mode import reviews_frozen, frozen_review


class MediaApprovalV2(JsonRecord):
    """Host-issued receipt binding content, requested criteria and media evidence."""
    pass


def validate_manifest(requirements,manifest):
    if reviews_frozen():
        return {**frozen_review(),'ok':None,'decision':'unverified','issues':[],'problems':[],'skipped':True}
    problems=[]; issues=[]
    def fail(code,message,rid=None):
        problems.append(message); issues.append({'code':code,'rule_id':code,'request_id':rid,'message':message})
    if manifest.get('schema_version') not in ('MediaManifestV1','MediaManifestV2'): fail('MANIFEST_SCHEMA','媒体清单版本无效')
    if manifest.get('content_hash')!=requirements.get('content_hash') or manifest.get('requirements_hash')!=digest_of(requirements): fail('MANIFEST_BINDING','媒体清单 content/requirements 绑定失效')
    expected={r['request_id']:r for r in requirements.get('requests',[])}
    rows=manifest.get('requests',[]); ids=[r.get('request_id') for r in rows]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected): fail('MANIFEST_COVERAGE','媒体请求遗漏、重复或额外未登记')
    for row in rows:
        rid=row.get('request_id'); request=expected.get(rid)
        if not request: continue
        if row.get('state')!='fulfilled': fail('RESOURCE_INVALID',f'{rid}: 资源未完成 ({row.get("state")})：{row.get("reason","")}'); continue
        if row.get('code_product') and row.get('kind')=='animation' and row.get('render_profile')!='final':
            fail('RESOURCE_INVALID',f'{rid}: 代码动画必须完成 final 渲染与实际画面审核',rid);continue
        if row.get('kind')!=request['kind'] or row.get('request_hash')!=digest_of(request): fail('RESOURCE_INVALID',f'{rid}: 请求规格绑定失效')
        from ...core.resource_files import relocated_source_dir
        base=relocated_source_dir(row.get('source_dir'),row.get('resources') or []).resolve()  # 修改计划 F1
        resources=row.get('resources') or []
        if not resources: fail('RESOURCE_INVALID',f'{rid}: 无实际资源')
        for resource in resources:
            try:
                verified_resource_file(base,resource)
            except (ContractError, OSError, ValueError, TypeError) as exc:
                fail('RESOURCE_INVALID',f'{rid}: 资源路径/字节摘要无效：{exc}',rid)
        if requirements.get('schema_version') in ('MediaRequirementsV2','MediaRequirementsV3'):
            from .validation import validate_media_report
            try: validate_media_report(request,row,row.get('validation'))
            except Exception as exc: fail('MEDIA_VALIDATION_V2',f'{rid}: {exc}',rid)
            continue
        if request['kind']=='animation':
            try:
                parsed=parse_animation_result(row.get('animation') or {})
                for p in validate_against_files(parsed,base): fail('RESOURCE_INVALID',f'{rid}: {p}',rid)
                if parsed.status!='fulfilled' or parsed.frame_report.get('status')!='pass': fail('RESOURCE_INVALID',f'{rid}: 动画真实画面未通过')
                reports=(row.get('media_review') or {}).get('reports') or {}
                if not reports or any(r.get('status')!='pass' for r in reports.values()): fail('RESOURCE_INVALID',f'{rid}: 缺完整数学/帧检查报告')
            except Exception as exc: fail('RESOURCE_INVALID',f'{rid}: 动画结果不合法：{exc}')
        else:
            review=row.get('review') or {}
            screenshot=next((r for r in resources if r.get('kind') in ('screenshot','image')),None)
            if not screenshot or review.get('image_hash')!=screenshot.get('sha256') or review.get('request_hash')!=digest_of(request) or review.get('accepted') is not True or not review.get('evidence') or review.get('problems'): fail('IMAGE_REVIEW_EVIDENCE',f'{rid}: 缺实际图片上的完整通过证据',rid)
    return check_report(inputs={'requirements_hash':digest_of(requirements),'manifest_hash':manifest_digest(manifest)},rules=('MANIFEST_SCHEMA','MANIFEST_BINDING','MANIFEST_COVERAGE','RESOURCE_INVALID','IMAGE_REVIEW_EVIDENCE','MEDIA_VALIDATION_V2'),issues=issues,expected=list(expected),checked=ids,content_hash=requirements.get('content_hash'),requirements_hash=digest_of(requirements),manifest_hash=manifest_digest(manifest),checked_request_ids=ids)


def approve_media(requirements,manifest,content_approval):
    if reviews_frozen(): return None,validate_manifest(requirements,manifest)
    from ...errors import ContractError
    if requirements.get('schema_version') not in ('MediaRequirementsV2','MediaRequirementsV3'): raise ContractError('媒体批准要求当前需求契约')
    report=validate_manifest(requirements,manifest)
    if not report['ok']: raise ContractError('；'.join(report['problems']))
    if requirements.get('schema_version')=='MediaRequirementsV3':
        if (content_approval.get('schema_version')!='ContentIdentityV1'
                or content_approval.get('content_hash')!=requirements['content_hash']
                or requirements.get('content_identity_hash')!=digest_of(content_approval)):
            raise ContractError('媒体验收缺当前内容身份绑定')
        return {'schema_version':'MediaApprovalV3','decision':'accepted','scope':'media_only',
            'content_hash':requirements['content_hash'],'content_identity_hash':digest_of(content_approval),
            'reader_document_hash':content_approval['reader_document_hash'],
            'requirements_hash':digest_of(requirements),'manifest_hash':manifest_digest(manifest),
            'check_report_hash':digest_of(report),'validation_hashes':{r['request_id']:validation_digest(r['validation']) for r in manifest['requests']}},report
    if content_approval.get('decision')!='accepted' or content_approval.get('content_hash')!=requirements['content_hash']: raise ContractError('媒体验收缺当前内容收据')
    if requirements.get('schema_version')=='MediaRequirementsV2':
        if content_approval.get('schema_version')!='ContentApprovalV2': raise ContractError('MediaApprovalV2 requires ContentApprovalV2')
        if requirements.get('approval_ref')!=digest_of(content_approval) or not content_approval.get('reader_document_hash'): raise ContractError('媒体需求绑定另一内容批准')
        approval={'schema_version':'MediaApprovalV2','decision':'accepted','content_hash':requirements['content_hash'],
            'content_approval_hash':digest_of(content_approval),'reader_document_hash':content_approval['reader_document_hash'],
            'requirements_hash':digest_of(requirements),'manifest_hash':manifest_digest(manifest),
            'policy_digest':requirements.get('policy_digest'),'media_policy_digest':digest_of(media_review_policy()),
            'validation_hashes':{r['request_id']:validation_digest(r['validation']) for r in manifest['requests']},
            'check_report_hash':digest_of(report),'checked_request_ids':report['checked_request_ids']}
        return MediaApprovalV2(approval).as_dict(),report
