"""Replay verified TOC model evidence after a local body-only adaptation.

This never relaxes dual_source.is_current. The original accepted result is
captured BEFORE adaptation; PDF, OCR directory, prompts, code, outputs and
images must remain identical. The normal validators then consume saved model
responses and create authority for the new document hash. No network provider
is used and no new model usage is charged.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from . import dual_source as dual
from .contract import ContractError, file_sha256, read_json, write_json


def capture(parse_dir, pdf):
    root, pdf = Path(parse_dir), Path(pdf)
    if not dual.is_current(root, pdf):
        return None
    return {
        'manifest': read_json(root / 'toc.dual-source.json'),
        'manifest_sha256': file_sha256(root / 'toc.dual-source.json'),
        'outputs': {name: read_json(root / name) for name in dual.OUTPUTS},
    }


def replay(parse_dir, pdf, evidence, *, log=None):
    """Return None without mutation when reuse cannot be proven; otherwise revalidate."""
    if not evidence:
        return None
    root, pdf = Path(parse_dir), Path(pdf)
    try:
        saved = evidence['manifest']
        current = dual._fingerprint(root, pdf)
        old = copy.deepcopy(saved['inputs'])
        for key in ('document.md', 'headings.json'):
            old['files'][key] = current['files'][key]
        if (saved.get('status') != 'accepted' or old != current
                or saved['outputs'] != {n: file_sha256(root / n) for n in dual.OUTPUTS}
                or file_sha256(root / 'toc.dual-source.json') != evidence['manifest_sha256']
                or not saved.get('images')
                or any(file_sha256(Path(s['path'])) != s['sha256'] for s in saved['images'])):
            return None
        payloads = evidence['outputs']
        # Reject mutation of the captured in-memory responses too.
        if any(payloads[n] != read_json(root / n) for n in dual.OUTPUTS):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None

    class CachedDirectoryClient:
        resource_id = ''

        def chat_json(self, messages, *, tag, images=None):
            data = json.loads(messages[-1]['content'])
            expected_images = {s['pdf_page']: s['sha256'] for s in saved['images']}
            if images and any(file_sha256(Path(p)) not in expected_images.values() for p in images):
                raise ContractError('本地目录回放图像与已复核图像不一致')
            if tag.startswith('toc-visual:'):
                batches = payloads['toc.visual.json']['batches']
                found = [b for b in batches if b['pdf_pages'] == data['pdf_pages']]
                if len(found) != 1:
                    raise ContractError('本地目录回放页面批次不一致')
                response = found[0]['response']
            else:
                pages = data.get('pdf_pages') or sorted({r['toc_page']
                    for name in ('mineru', 'visual') for r in data[name]})
                if (data['mineru'] != [r for r in payloads['toc.mineru-normalized.json']['entries'] if r['toc_page'] in pages]
                        or data['visual'] != [r for r in payloads['toc.visual.json']['entries'] if r['toc_page'] in pages]):
                    raise ContractError('本地目录回放的比较输入不一致')
                if tag.startswith('toc-compare:'):
                    artifact = payloads['toc.comparison.json']
                elif tag.startswith('toc-arbitrate:'):
                    if any(p not in expected_images for p in data['pdf_pages']):
                        raise ContractError('本地目录回放复核页面不一致')
                    artifact = payloads['toc.arbitration.json']
                else:
                    raise ContractError('未知目录回放操作')
                found = [b for b in artifact['batches']
                         if b['request_fingerprint'] == dual._request_fingerprint(data)]
                if len(found) != 1:
                    raise ContractError('本地目录回放批次、差异或父级语境不一致')
                response = found[0]['response']
            return {'data': copy.deepcopy(response), 'cached': True,
                    'usage': {'total_tokens': 0},
                    'fingerprint': 'local-evidence:' + evidence['manifest_sha256']}

    report = dual.generate_directory(root, pdf, CachedDirectoryClient(), log=log)
    report['local_replay'] = {
        'reason': 'body-adaptation-only', 'source_manifest_sha256': evidence['manifest_sha256'],
        'previous_inputs': saved['inputs'], 'previous_calls': saved.get('calls', []),
        'new_paid_calls': 0,
    }
    write_json(root / 'toc.dual-source.json', report)
    return report
