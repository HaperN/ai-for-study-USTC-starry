import hashlib,json
import pytest
from aiforstudy.practice.catalog import validate_review_files
from test_practice_catalog import approved

def test_publication_rejects_missing_or_modified_evidence(tmp_path):
 q=approved()
 with pytest.raises(ValueError,match='evidence files'):validate_review_files([q],tmp_path)
 from test_practice_publication_guards import bound
 q=bound(tmp_path)
 validate_review_files([q],tmp_path)
 (tmp_path/'blind.json').write_text('tampered')
 with pytest.raises(ValueError,match='evidence hash'):validate_review_files([q],tmp_path)

def test_evidence_path_must_stay_in_catalog_package(tmp_path):
 q=approved();q['review']['evidence_files']=[{'path':'../elsewhere','sha256':'a'*64}]
 with pytest.raises(ValueError,match='escape'):validate_review_files([q],tmp_path)

def test_nested_gateway_response_and_empty_metadata_terminate(tmp_path):
 import json,hashlib
 from test_practice_publication_guards import bound
 q=bound(tmp_path);p=tmp_path/'blind.json';rows=json.loads(p.read_text());p.write_text(json.dumps({'payload':{'questions':rows},'usage':{}}));q['review']['evidence_files'][0]['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
 validate_review_files([q],tmp_path)
