"""Read-only source material navigation and local image packaging."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from pathlib import Path
import re
from urllib.parse import unquote
from ...core.jsonio import read_json_soft


def merge_material_tree(tree, materials, entries, catalog_nodes):
    """Insert resources by the original directory order without inventing units."""
    result = deepcopy(tree)
    by_toc = {n['toc_id']: n['node_key'] for n in catalog_nodes}
    order_by_key = {by_toc[e['toc_id']]: e['order'] for e in entries if e.get('toc_id') in by_toc}
    nodes = {}
    def visit(rows, inherited_order=0):
        previous_order=inherited_order
        for node in rows:
            nodes[node['id']] = node
            # Body-only subsections use the nearest declared structural parent,
            # even when that parent has no selected lesson. Keep unmatched rows
            # in their existing teaching order instead of moving them to the end.
            ancestors=[key for key in order_by_key
                       if node['id']==key or node['id'].startswith(key+'-')]
            previous_order=order_by_key[max(ancestors,key=len)] if ancestors else previous_order
            node['_source_order'] = previous_order
            visit(node.get('children', []),previous_order)
    visit(result)
    by_entry = {}
    for row in sorted(materials, key=lambda m: (m.get('order', 0), m['id'])):
        by_entry[row['entry_id']] = {'id': row['id'], 'material_id': row['id'], 'title': row['title'],
            'anchor': row.get('number', ''), 'kind': 'source_material', 'unit': False,
            'declared': True, 'children': [], '_source_order': row.get('order', float('inf'))}
    for row in sorted(materials, key=lambda m: (m.get('order', 0), m['id'])):
        node = by_entry[row['entry_id']]
        parent = by_entry.get(row.get('parent_entry_id'))
        if parent is None:
            parent = nodes.get(by_toc.get(row.get('owner_toc_id')))
        (parent['children'] if parent is not None else result).append(node)
    def ordered(rows):
        rows.sort(key=lambda n: n['_source_order'])
        for node in rows:
            ordered(node.get('children', []))
            node.pop('_source_order', None)
    ordered(result)
    return result


def source_asset_paths(texts, parse_root):
    """Resolve local raster references without writes or remote fetches."""
    root = Path(parse_root).resolve()
    assets = {}
    for text in texts:
        for match in re.finditer(r'!\[[^\]\n]*\]\(\s*(<[^>\n]+>|[^\s)]+)(?:\s+"[^"\n]*")?\s*\)', text):
            url = match.group(1).strip('<>')
            path = (root / unquote(url)).resolve()
            if (url in assets or not path.is_relative_to(root) or not path.is_file()
                    or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}):
                continue
            assets[url] = path
    return assets


def source_asset_names(paths):
    return {url:'assets/source/' + hashlib.sha256(path.read_bytes()).hexdigest() + path.suffix.lower()
            for url,path in paths.items()}


def copy_source_assets(paths, names, output_root):
    for url,path in paths.items():
        target = Path(output_root)/names[url]
        target.parent.mkdir(parents=True, exist_ok=True)
        content=path.read_bytes()
        if target.stem != hashlib.sha256(content).hexdigest():
            raise ValueError('原书图片在渲染期间发生变化，请重试')
        target.write_bytes(content)




def project_source_materials(data, parse_root, formatted_payload=None):
    """Build navigation and resource identities before publishing a snapshot."""
    root = Path(parse_root)
    payload = read_json_soft(root / 'source_materials.json')
    if (root / 'source_materials.json').exists():
        from ...core.source_materials import is_current
        if not is_current(root):
            raise ValueError('source_materials.json 来源过期；请重新准备目录资料')
    materials = (payload or {}).get('materials', [])
    toc = read_json_soft(root / 'toc.v2.json') or {'nodes': []}
    # Stable catalog keys (N-level-number) are not display aliases (CH..).
    # Prefer the explicit body relation, then an unambiguous directory anchor.
    body = read_json_soft(root / 'body.json') or {}
    actual_keys = {n['toc_node']: n['body_node'] for n in body.get('units', [])
                   if n.get('toc_node') and n.get('body_node')}
    display = {}
    def display_keys(rows, level=1):
        for n in rows:
            display.setdefault((level, str(n.get('anchor') or '')), []).append(n['id'])
            display_keys(n.get('children', []), level+1)
    display_keys(data['tree'])
    catalog_nodes = []
    for n in toc.get('nodes', []):
        keys = display.get((n.get('level'), str(n.get('number') or n.get('anchor') or '')), [])
        key = actual_keys.get(n['toc_id']) or (keys[0] if len(keys) == 1 else None)
        if key:
            catalog_nodes.append({'toc_id': n['toc_id'], 'node_key': key})
    data['source_materials'] = {row['id']: row for row in materials}
    # Formatting is a display-only attachment, never source identity or review
    # evidence. Ignore stale or unusable optional rows without hiding the source.
    formatted = formatted_payload.get('materials') if isinstance(formatted_payload, dict) else {}
    if isinstance(formatted, dict):
        for row in materials:
            result = formatted.get(row['id'])
            if not isinstance(result, dict):
                continue
            markdown = result.get('display_markdown')
            if (not row.get('source_sha256') or result.get('source_sha256') != row['source_sha256']
                    or result.get('status') != 'formatted'
                    or not isinstance(markdown, str) or not markdown.strip()):
                continue
            notes = result.get('notes')
            row['display_markdown'] = markdown
            row['formatting'] = {'status': 'formatted', 'notes': [note.strip() for note in notes
                if isinstance(note, str) and note.strip()] if isinstance(notes, list) else []}
    data['tree'] = merge_material_tree(data['tree'], materials,
        (payload or {}).get('directory_entries', []), catalog_nodes)
    texts = [row.get(field, '') for row in materials for field in ('raw_markdown', 'display_markdown')]
    for unit in data['units'].values():
        texts += [b['raw_text'] for b in (unit.get('reader_document') or {}).get('blocks', [])
                  if b.get('group') == 'exercise_regions']
    paths=source_asset_paths(texts, root)
    bundle = root / 'course-library'
    if (bundle / 'current.json').exists():
        from ...core.course_library import project_library
        paths.update(project_library(data, bundle))
    data['source_assets'] = source_asset_names(paths)
    data['counts']['source_materials'] = len(materials)
    return paths


def attach_source_materials(data, parse_root, output_root):
    paths=project_source_materials(data,parse_root)
    copy_source_assets(paths,data['source_assets'],output_root)
