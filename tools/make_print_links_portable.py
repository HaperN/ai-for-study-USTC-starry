"""Keep links from printed local HTML usable after the delivery folder is moved."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, TextStringObject


def make_portable(pdf: Path, root: Path) -> dict:
    pdf, root = pdf.resolve(), root.resolve()
    pdf.relative_to(root)
    reader = PdfReader(pdf)
    writer = PdfWriter(clone_from=reader)
    changed = 0
    for page in writer.pages:
        for ref in page.get('/Annots', []):
            action = ref.get_object().get('/A')
            if not action:
                continue
            action = action.get_object()
            if action.get('/S') != '/URI':
                continue
            url = urlsplit(str(action.get('/URI', '')))
            if url.scheme != 'file':
                continue
            if url.netloc not in ('', 'localhost'):
                raise ValueError('Unexpected nonlocal file link in PDF')
            target = Path(unquote(url.path)).resolve()
            target.relative_to(root)
            if not target.is_file():
                raise ValueError('Missing linked delivery file: ' + str(target.relative_to(root)))
            relative = os.path.relpath(target, pdf.parent)
            action[NameObject('/URI')] = TextStringObject(urlunsplit(('', '', quote(relative, safe='/'), url.query, url.fragment)))
            changed += 1
    temporary = pdf.with_suffix('.portable.pdf')
    try:
        writer.write(temporary)
        temporary.replace(pdf)
    finally:
        temporary.unlink(missing_ok=True)
        writer.close()
        reader.close()
    check = PdfReader(pdf)
    result = {'pages': len(check.pages), 'portable_links_rewritten': changed,
              'tagged': '/StructTreeRoot' in check.trailer['/Root'],
              'outline_entries': len(check.outline)}
    check.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(make_portable(args.pdf, args.root), ensure_ascii=False))
