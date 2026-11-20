"""Historical candidates require explicit schema-4 fields, never inherited approvals."""
import json
from pathlib import Path
import pytest
from aiforstudy.pipeline.candidate_import import import_legacy_candidate
from aiforstudy.errors import ContractError
from test_content_v4_reader_contract import candidate


def migration_input():
    content=candidate();content['unit_id']='CH01-S01';content['_meta']['schema_version']='3.0'
    fields={key:content.pop(key) for key in ('reader_intro','prerequisite_recaps','summary','teaching_plan','teaching_evidence','math_models','learning_checks')}
    content['figures']=[{'id':'F'}];content['approval']={'decision':'accepted'}
    return content,fields


def test_import_is_explicit_v4_candidate_without_old_media_or_acceptance(tmp_path):
    old,fields=migration_input();source=tmp_path/'old.json';source.write_text(json.dumps(old));before=source.read_bytes()
    report=import_legacy_candidate(source,tmp_path/'work',book_id='b',source_run_id='old-run',v4_fields=fields)
    content=json.loads(Path(report['candidate_path']).read_text())
    assert source.read_bytes()==before
    assert content['_meta']['schema_version']=='4.0'
    assert 'figures' not in content and 'approval' not in content
    assert not report['content_approval_inherited'] and not report['media_approval_inherited']
    assert report['schema_conversion']['source_schema_version']=='3.0'
    assert json.loads(Path(report['media_attachment']).read_text())['/figures']==old['figures']
    with pytest.raises(ContractError): import_legacy_candidate(source,tmp_path/'work',book_id='b',v4_fields=fields)


@pytest.mark.parametrize('bad', ['missing-fields','old-schema-disguise','unit-mismatch'])
def test_import_rejection_is_zero_write(tmp_path,bad):
    old,fields=migration_input()
    if bad=='old-schema-disguise': old['_meta']['schema_version']='4.0'
    source=tmp_path/'old.json';source.write_text(json.dumps(old))
    with pytest.raises(ContractError):
        import_legacy_candidate(source,tmp_path/'work',book_id='b',v4_fields=None if bad=='missing-fields' else fields,
                                expected_unit_id='CH02-S01' if bad=='unit-mismatch' else 'CH01-S01')
    assert not (tmp_path/'work').exists()
