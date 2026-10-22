"""Transactional corpus imports, immutable annotation revisions and lexical search."""
from datetime import date
from contextlib import nullcontext
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .db import ContractError, connect, now, sha, verify_migrations

KINDS = {'knowledge','idea','question','task','event'}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def text(value,field,limit=4000):
    if not isinstance(value,str) or not value.strip() or '\0' in value or len(value)>limit:
        raise ContractError(f'{field} 必须为非空文本，最多 {limit} 字符，不含 NUL')
    return value


def exact_keys(value,required,optional=()):
    if not isinstance(value,dict) or set(value)-set(required)-set(optional) or set(required)-set(value):
        raise ContractError(f'字段不符合约定；必需字段：{sorted(required)}')


def url(value):
    text(value,'URL',768)
    try:
        p=urlsplit(value)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or any(c.isspace() for c in value):
            raise ValueError()
    except ValueError:
        raise ContractError('来源 URL 必须为无凭证的 HTTP(S) 地址') from None
    return value


def iso_date(value):
    text(value,'date',10)
    if date.fromisoformat(value).isoformat()!=value:
        raise ContractError('日期须为 YYYY-MM-DD')
    return value


def tags(values):
    if not isinstance(values,list) or not 1<=len(values)<=20:
        raise ContractError('tags 须包含 1–20 个标签')
    result=[text(v,'tag',100).strip() for v in values]
    if len(set(result))!=len(result):
        raise ContractError('同一条标注不允许重复标签')
    return sorted(result)


def validate_record(raw):
    exact_keys(raw,{'id','origin','title','language','body','locator','collected_on','split','annotation'},
               {'source','scenario_group'})
    r=dict(raw)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}',text(r['id'],'id',100)):
        raise ContractError('id 只接受字母、数字、下划线和短横线')
    for field,limit in [('title',200),('body',20000),('locator',500)]:
        text(r[field],field,limit)
    if r['origin'] not in ('public_excerpt','synthetic') or r['language'] not in ('zh','en') or r['split'] not in ('dev','test'):
        raise ContractError('origin/language/split 不合法')
    iso_date(r['collected_on'])
    exact_keys(r['annotation'],{'summary_zh','category','tags'})
    a=dict(r['annotation'])
    text(a['summary_zh'],'summary_zh')
    if not isinstance(a['category'],str) or a['category'] not in KINDS:
        raise ContractError('category 不合法')
    a['tags']=tags(a['tags'])
    r['annotation']=a
    if r['origin']=='public_excerpt':
        if len(r['body'])>500 or r.get('scenario_group') is not None:
            raise ContractError('公开摘录最多 500 字符且不能指定模拟分组')
        src=r.get('source')
        exact_keys(src,{'url','author','license_status','license_name','license_url','checked_on','evidence'})
        src=dict(src)
        url(src['url']); text(src['author'],'author',200)
        iso_date(src['checked_on']); text(src['evidence'],'evidence')
        if src['license_status'] not in ('unknown','documented'):
            raise ContractError('license_status 不合法')
        if src['license_status']=='documented':
            text(src['license_name'],'license_name',200); url(src['license_url'])
        elif src['license_name'] is not None or src['license_url'] is not None:
            raise ContractError('未知许可不应附加已确认许可名称')
        r['source']=src
        r['source_id']=sha(src['url'])
        r['group_key']='publisher:'+' '.join(src['author'].split()).casefold()
    else:
        if r.get('source') is not None:
            raise ContractError('模拟样本不能伪造公开来源')
        r['group_key']='scenario:'+text(r.get('scenario_group'),'scenario_group',100).strip().casefold()
        r['source_id']=None
    r['body_sha256']=sha(r['body'])
    return r


def add_revision(conn,sample_id,revision,summary,category,tag_names,*,origin,review_status,reviewer,actor,reason):
    annotation_id=f'{sample_id}:r{revision}'
    conn.execute('INSERT INTO annotation_revisions '
        '(id,sample_id,revision,summary_zh,category,origin,review_status,reviewer,actor,reason,tags_json,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (annotation_id,sample_id,revision,summary,category,origin,review_status,reviewer,actor,reason,canonical(tag_names),now()))
    for name in tag_names:
        conn.execute('INSERT OR IGNORE INTO tags(name) VALUES(?)',(name,))
        conn.execute('INSERT INTO annotation_tags VALUES(?,?)',(annotation_id,name))
    return annotation_id


def event(conn,sample_id,action,actor,details):
    conn.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?)',
                 (str(uuid4()),sample_id,action,actor,canonical(details),now()))


def import_file(db,path):
    path=Path(path)
    payload=json.loads(path.read_text(encoding='utf-8-sig'))
    exact_keys(payload,{'schema_version','records'})
    if type(payload['schema_version']) is not int or payload['schema_version']!=1:
        raise ContractError('只支持导入格式 schema_version=1')
    if not isinstance(payload['records'],list) or not payload['records']:
        raise ContractError('records 必须是非空数组')
    records=[validate_record(r) for r in payload['records']]
    if len({r['id'] for r in records})!=len(records):
        raise ContractError('同一文件中 id 重复')
    inserted=skipped=0
    with connect(db,write=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        files,applied=verify_migrations(conn)
        if len(files)!=len(applied):
            raise ContractError('请先应用全部迁移')
        for r in records:
            src=r.get('source')
            if src:
                values=(r['source_id'],src['url'],src['author'],src['license_status'],src['license_name'],src['license_url'])
                old=conn.execute('SELECT * FROM sources WHERE id=?',(r['source_id'],)).fetchone()
                if old and tuple(old)!=values:
                    raise ContractError('同一来源信息冲突；不能通过重导静默改写来源')
                if not old:
                    conn.execute('INSERT INTO sources VALUES(?,?,?,?,?,?)',values)
            fields=('id','source_id','origin','title','language','body','body_sha256','locator','collected_on','group_key','split')
            values=tuple(r[k] for k in fields)
            old=conn.execute('SELECT '+','.join(fields)+' FROM samples WHERE id=?',(r['id'],)).fetchone()
            if old:
                if tuple(old)!=values:
                    raise ContractError(f"{r['id']} 与已有原文或元数据冲突；请使用新 ID")
                skipped+=1
                continue
            conn.execute('INSERT INTO samples('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',values)
            a=r['annotation']
            add_revision(conn,r['id'],1,a['summary_zh'],a['category'],a['tags'],origin='ai_assisted',
                         review_status='pending',reviewer=None,actor='seed_import',reason='独立整理的候选标注，待人工复核')
            if src:
                conn.execute('INSERT INTO source_checks VALUES(?,?,?,?,?,?,?)',
                    (sha(r['id']+src['checked_on']+src['evidence']),r['id'],src['checked_on'],
                     'web_reader','matched','ai_assisted',src['evidence']))
            event(conn,r['id'],'import','seed_import',{'body_sha256':r['body_sha256'],'model_executed':False})
            inserted+=1
        conn.execute('INSERT INTO import_runs VALUES(?,?,?,?,?,?,?)',
            (str(uuid4()),path.name,sha(path.read_text(encoding='utf-8-sig')),len(records),inserted,skipped,now()))
    return {'status':'ok','inserted':inserted,'skipped':skipped,'model_executed':False}


def revise(db,sample_id,*,expected_revision,actor,reason,summary=None,category=None,tag_names=None,
           review_status='pending',human=False,_connection=None):
    text(actor,'actor',200); text(reason,'reason')
    if type(human) is not bool:raise ContractError('human 须为布尔值')
    if type(expected_revision) is not int or expected_revision<1:
        raise ContractError('expected_revision 须为正整数')
    if review_status not in ('pending','verified','rejected') or (review_status!='pending' and not human):
        raise ContractError('确认或拒绝须显式指定 --human，并填写人工复核者')
    request_hash=sha(canonical({'sample_id':sample_id,'expected_revision':expected_revision,'actor':actor,
        'reason':reason,'summary':summary,'category':category,'tag_names':tag_names,'review_status':review_status,'human':human}))
    with (nullcontext(_connection) if _connection is not None else connect(db,write=True)) as conn:
        if _connection is None:conn.execute('BEGIN IMMEDIATE')
        replay=conn.execute("SELECT details_json FROM audit_events WHERE sample_id=? AND action='annotation_revision' "
            "AND json_extract(details_json,'$.request_sha256')=?",(sample_id,request_hash)).fetchone()
        if replay:
            return {'status':'already_applied','revision':json.loads(replay[0])['revision']}
        old=conn.execute('SELECT * FROM current_annotations WHERE sample_id=?',(sample_id,)).fetchone()
        if not old or old['revision']!=expected_revision:
            raise ContractError('记录不存在或版本冲突；重新读取后再提交')
        summary=old['summary_zh'] if summary is None else text(summary,'summary_zh')
        category=old['category'] if category is None else category
        if not isinstance(category,str) or category not in KINDS:
            raise ContractError('category 不合法')
        tag_names=json.loads(old['tags_json']) if tag_names is None else tags(tag_names)
        if old['review_status'] in ('verified','rejected') and not human:
            raise ContractError('已人工决策的标注只能由人工继续修订')
        changed=(summary!=old['summary_zh'] or category!=old['category'] or tag_names!=json.loads(old['tags_json']))
        origin=('human' if human else 'ai_assisted') if changed else old['origin']
        reviewer=actor if review_status!='pending' else None
        if (summary,category,tag_names,review_status,reviewer)==(old['summary_zh'],old['category'],json.loads(old['tags_json']),old['review_status'],old['reviewer']):
            return {'status':'unchanged','revision':old['revision']}
        revision=old['revision']+1
        new_id=add_revision(conn,sample_id,revision,summary,category,tag_names,origin=origin,
                            review_status=review_status,reviewer=reviewer,actor=actor,reason=reason)
        event(conn,sample_id,'annotation_revision',actor,{'before_id':old['id'],'after_id':new_id,'reason':reason,
            'request_sha256':request_hash,'revision':revision})
    return {'status':'ok','revision':revision}


def apply_reviews(db,path):
    payload=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    exact_keys(payload,{'schema_version','records'})
    if payload['schema_version']!=1 or not isinstance(payload['records'],list):
        raise ContractError('复核批次须为 schema_version=1 的 records 数组')
    results=[]
    with connect(db,write=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        for r in payload['records']:
            exact_keys(r,{'sample_id','expected_revision','actor','reason','human'},
                       {'summary','category','tag_names','review_status'})
            if type(r['human']) is not bool:raise ContractError('human 须为布尔值')
            results.append(revise(db,_connection=conn,**r))
    return {'status':'ok','results':results}


def records(db,*,origin=None,split=None):
    conditions=[]; params=[]
    for field,value,allowed in [('origin',origin,{'public_excerpt','synthetic'}),('split',split,{'dev','test'})]:
        if value is not None:
            if value not in allowed:
                raise ContractError(f'{field} 筛选不合法')
            conditions.append(f's.{field}=?');params.append(value)
    with connect(db) as conn:
        rows=conn.execute('SELECT s.*,a.id AS annotation_id,a.revision,a.summary_zh,a.category,'
            'a.origin AS annotation_origin,a.review_status,a.reviewer,a.tags_json,'
            'p.url,p.author,p.license_status,p.license_name,p.license_url '
            'FROM samples s JOIN current_annotations a ON a.sample_id=s.id '
            'LEFT JOIN sources p ON p.id=s.source_id '+
            ('WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY s.id',params).fetchall()
        result=[]
        for row in rows:
            d=dict(row)
            d['tags']=json.loads(d.pop('tags_json'))
            d['source_checks']=[dict(r) for r in conn.execute(
                'SELECT checked_on,method,status,checker_type,evidence FROM source_checks WHERE sample_id=? ORDER BY checked_on,id',(d['id'],))]
            result.append(d)
    return result


def search(db,query,*,limit=20,origin=None,split=None):
    text(query,'query',200)
    if type(limit) is not int or not 1<=limit<=100:
        raise ContractError('limit 须为 1–100 的整数')
    eligible={r['id']:r for r in records(db,origin=origin,split=split)}
    with connect(db) as conn:
        if len(query.strip())>=3:
            # A single quoted phrase: user-supplied AND/OR/quotes never become operators.
            phrase='"'+query.strip().replace('"','""')+'"'
            rows=conn.execute('SELECT sample_id,bm25(sample_search,0,5,1,3,2) AS score '
                'FROM sample_search WHERE sample_search MATCH ? ORDER BY score,sample_id',(phrase,)).fetchall()
            method='fts5_trigram_bm25'
        else:
            pattern='%'+query.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            rows=conn.execute("SELECT sample_id,0 AS score FROM sample_search WHERE "
                "title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\' "
                'ORDER BY sample_id',(pattern,)*4).fetchall()
            method='literal_short_query'
    matches=[{'sample':eligible[r['sample_id']],'score':r['score']} for r in rows if r['sample_id'] in eligible]
    return {'method':method,'semantic_search':False,'query':query,'matched':len(matches),'results':matches[:limit]}


def check(db):
    errors=[]
    with connect(db) as conn:
        files,applied=verify_migrations(conn)
        if len(files)!=len(applied):errors.append('迁移未全部应用')
        if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':errors.append('完整性异常')
        if conn.execute('PRAGMA foreign_key_check').fetchall():errors.append('外键异常')
        if conn.execute('SELECT group_key FROM samples GROUP BY group_key HAVING count(DISTINCT split)>1').fetchall():
            errors.append('dev/test 分组泄漏')
        for r in conn.execute('SELECT id,body,body_sha256 FROM samples'):
            if sha(r['body'])!=r['body_sha256']:errors.append('原文哈希不符：'+r['id'])
        total=conn.execute('SELECT count(*) FROM samples').fetchone()[0]
        if conn.execute('SELECT count(*) FROM current_annotations').fetchone()[0]!=total:
            errors.append('样本缺少当前标注')
        expected=[]
        for r in conn.execute('SELECT s.id,s.title,s.body,a.summary_zh,a.id AS aid,a.tags_json '
                              'FROM samples s JOIN current_annotations a ON a.sample_id=s.id ORDER BY s.id'):
            names=[t[0] for t in conn.execute('SELECT tag_name FROM annotation_tags WHERE annotation_id=? ORDER BY tag_name',(r['aid'],))]
            if names!=json.loads(r['tags_json']):errors.append('标签关系不完整：'+r['id'])
            expected.append((r['id'],r['title'],r['body'],r['summary_zh'],' '.join(names)))
        indexed=[tuple(r) for r in conn.execute('SELECT sample_id,title,body,summary,tags FROM sample_search ORDER BY sample_id')]
        # Tag order in group_concat is not a contract; compare tag sets.
        if len(expected)!=len(indexed) or any(a[:4]!=b[:4] or set(a[4].split())!=set(b[4].split()) for a,b in zip(expected,indexed)):
            errors.append('全文索引与当前快照不同步')
    items=records(db)
    return {'status':'ok' if not errors else 'failed','errors':errors,'records':total,
            'public_excerpt':sum(r['origin']=='public_excerpt' for r in items),
            'synthetic':sum(r['origin']=='synthetic' for r in items),
            'human_verified':sum(r['review_status']=='verified' for r in items),
            'training_allowed':False,'scope':'离线结构检查；不代替人工语义复核或网页重新访问'}
