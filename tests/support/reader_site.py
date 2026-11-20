"""Explicit UI fixtures using the production reader projector and static shell.

These fixtures exercise rendering, never issue or imitate approval receipts.
The pipeline's envelope/identity tests live in contracts/test_reader_decisions.py.
"""
from copy import deepcopy
from pathlib import Path


def write_reader_fixture(contents, references, output, *, media=None, figures=None, animations=None, interactions=None):
    from aiforstudy.blocks.content.reader_document import project_reader
    from aiforstudy.blocks.render.layout import layout_from_reader_media
    from aiforstudy.pipeline.stage_io import _legacy
    data={'book':'Reader fixture','scope':[],'tree':[],'units':{},'figures':figures or {},
        'concepts':{},'diagrams':{},'models':{},'toc_gaps':[],'concept_figures':{},'number_refs':{},
        'counts':{'canvas_models':0},'animations':animations or {},'preview_only':True,'production_accepted':False}
    for content in contents:
        unit=content['unit_id']; reader=project_reader(content,references).as_dict()
        entries=deepcopy((media or {}).get(unit,[]))
        interaction=(interactions or {}).get(unit,{})
        for model in interaction.get('models',[]):
            identity=unit+'.'+model['id'];data['models'][identity]={**model,'id':identity,'unit_id':unit}
        data['scope'].append(unit)
        data['tree'].append({'id':unit,'title':content['title'],'unit':True,'children':[]})
        data['units'][unit]={'unit_id':unit,'title':content['title'],'reader_document':reader,
            'reader_layout':layout_from_reader_media(reader,entries,interaction_mounts=interaction.get('interaction_placements',[])),
            'body_markdown':content['body_markdown'],'reader_media':entries,
            'figures':[],'concepts':[],'crumb':'UI fixture'}
    _legacy('build').write_reader_site(data,Path(output),book=data['book'])
    return data
