from blocks.parse.concepts.extract_concepts import extract_unit


def test_exercise_references_do_not_create_concept_coverage_retries():
    text='定义 1.1.1 零向量是所有坐标为零的向量。\n\n习题\n1. 证明定理 1.1.2。\n'
    class Client:
        model='offline'; effort='none'
        def __init__(self): self.calls=0
        def chat_json(self,messages,**kwargs):
            self.calls+=1
            assert '证明定理 1.1.2' in messages[1]['content']
            return {'data':{'concepts':[{'name':'零向量','type':'definition','formal':'定义 1.1.1 零向量是所有坐标为零的向量。','source_quote':'定义 1.1.1 零向量是所有坐标为零的向量。'}], 'named_results':[]}}
    client=Client()
    result=extract_unit({'unit_id':'CH01-S01','title':'向量','anchor':'1.1'},text,client)
    assert result['coverage']['编号条目']==1
    assert result['coverage']['漏掉的条目']==[]
    assert client.calls==1
