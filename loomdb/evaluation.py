"""A frozen candidate benchmark; results measure lexical retrieval, not semantics."""
import json
from pathlib import Path

from .corpus import canonical, records, search
from .db import ContractError, sha


def evaluate(db,benchmark):
    data=json.loads(Path(benchmark).read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict) or data.get('version')!=1 or not isinstance(data.get('queries'),list) or not data['queries']:
        raise ContractError('评测文件须为非空 version=1 查询集')
    items=records(db);by_id={r['id']:r for r in items}
    details=[];seen=set()
    for q in data['queries']:
        if not isinstance(q,dict) or set(q)!={'id','query','expected_ids','split','track'}:
            raise ContractError('评测查询字段不合法')
        if not isinstance(q['id'],str) or not q['id'] or q['id'] in seen:
            raise ContractError('评测 id 不能为空或重复')
        seen.add(q['id'])
        ids=q['expected_ids']
        if not isinstance(ids,list) or any(not isinstance(i,str) or i not in by_id for i in ids) or len(ids)!=len(set(ids)):
            raise ContractError('评测目标 ID 不存在或重复')
        if q['split'] not in ('dev','test') or any(by_id[i]['split']!=q['split'] for i in ids):
            raise ContractError('评测目标与 split 不一致')
        if q['track'] not in ('keyword','natural','negative') or (q['track']=='negative')!= (len(ids)==0):
            raise ContractError('正负例目标约定不合法')
        result=search(db,q['query'],limit=5,split=q['split'])
        returned=[r['sample']['id'] for r in result['results']]
        ranks=[returned.index(i)+1 for i in ids if i in returned]
        details.append({**q,'returned_ids':returned,'hit':bool(ranks),
                        'reciprocal_rank':1/min(ranks) if ranks else 0,'method':result['method']})
    def aggregate(rows):
        positives=[r for r in rows if r['expected_ids']];negatives=[r for r in rows if not r['expected_ids']]
        return {'queries':len(rows),'positives':len(positives),'negatives':len(negatives),
                'hit_at_5':sum(r['hit'] for r in positives)/len(positives) if positives else None,
                'mrr_at_5':sum(r['reciprocal_rank'] for r in positives)/len(positives) if positives else None,
                'negative_false_return_rate':sum(bool(r['returned_ids']) for r in negatives)/len(negatives) if negatives else None}
    return {'status':'ok','candidate_labels':True,'human_verified':False,'semantic_search':False,
            'benchmark_sha256':sha(canonical(data)),'dataset_sha256':sha(canonical(items)),
            'overall':aggregate(details),'by_track':{t:aggregate([r for r in details if r['track']==t]) for t in ('keyword','natural','negative')},
            'by_split':{s:aggregate([r for r in details if r['split']==s]) for s in ('dev','test')},'details':details}
