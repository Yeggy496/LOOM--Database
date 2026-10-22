"""Portable snapshots. Export never opens a remote database or calls a model."""
import csv
import io
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .corpus import canonical, check, records
from .db import ContractError, ROOT, connect, sha

PORTABLE_TABLES=('sources','samples','annotation_revisions','tags','annotation_tags',
                 'source_checks','import_runs','audit_events')


def safe_csv(value):
    value=canonical(value) if isinstance(value,(dict,list)) else '' if value is None else str(value)
    if value.startswith(('\t','\r','\n')) or re.match(r'^\s*[=+@-]',value):
        value="'"+value
    return value


def mysql_value(value):
    if value is None:return 'NULL'
    if type(value) is int:return str(value)
    if isinstance(value,str):return "CONVERT(X'"+value.encode('utf-8').hex()+"' USING utf8mb4)"
    raise ContractError('MySQL 导出遇到不支持的字段类型')


def mysql_seed(conn):
    lines=['-- Requires schema/mysql/V001__corpus.sql in an EMPTY development database.',
           '-- Stop on first error; never use mysql --force. FTS/SQLite migration metadata excluded.',
           'SET NAMES utf8mb4 COLLATE utf8mb4_0900_bin;','START TRANSACTION;']
    counts={}
    for table in PORTABLE_TABLES:
        cols=[r[1] for r in conn.execute('PRAGMA table_info('+table+')')]
        rows=conn.execute('SELECT * FROM '+table+' ORDER BY '+','.join(cols)).fetchall()
        counts[table]=len(rows)
        for row in rows:
            lines.append('INSERT INTO `'+table+'` ('+','.join('`'+c+'`' for c in cols)+') VALUES ('+
                         ','.join(mysql_value(v) for v in row)+');')
    lines.append('COMMIT;')
    for table,count in counts.items():
        lines.append(f"SELECT '{table}' AS table_name,COUNT(*) AS actual_rows,{count} AS expected_rows FROM `{table}`;")
    return '\n'.join(lines)+'\n',counts


def export_bundle(db,out=ROOT/'exports'):
    db=Path(db).resolve();out=Path(out).resolve()
    if (out/'loom.db').resolve()==db:
        raise ContractError('导出目录会覆盖正在读取的数据库；请选择其它目录')
    if out==ROOT or ROOT in out.parents and any(part in {'loomdb','schema','migrations','data','tests'} for part in out.relative_to(ROOT).parts):
        raise ContractError('导出目录不能覆盖源代码、迁移或原始数据目录')
    out.mkdir(parents=True,exist_ok=True)
    snapshot=out/('.snapshot-'+uuid4().hex+'.db')
    try:
        with connect(db) as conn:
            target=sqlite3.connect(snapshot)
            try:conn.backup(target)
            finally:target.close()
        # Every format reads this frozen backup, even if another writer is active.
        return _export_snapshot(snapshot,out)
    finally:
        snapshot.unlink(missing_ok=True)


def _export_snapshot(db,out):
    report=check(db)
    if report['status']!='ok':raise ContractError('数据库检查未通过，拒绝导出')
    items=records(db)
    fingerprint=sha(canonical(items))
    payload={'contract_version':1,'scope':'research_corpus','dataset_sha256':fingerprint,
             'model_executed':False,'gold_standard':False,'records':items}
    json_text=json.dumps(payload,ensure_ascii=False,indent=2)+'\n'
    buffer=io.StringIO(newline='')
    cols=list(items[0]) if items else ['id','origin','title','body']
    writer=csv.DictWriter(buffer,fieldnames=cols)
    writer.writeheader()
    writer.writerows({k:safe_csv(v) for k,v in r.items()} for r in items)
    viewer=(ROOT/'viewer'/'template.html').read_text(encoding='utf-8')
    viewer=viewer.replace('__CORPUS_JSON__',json_text.replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029'))
    with connect(db) as conn:
        conn.execute('BEGIN')
        seed,counts=mysql_seed(conn)
        target=sqlite3.connect(out/'loom.db')
        try:conn.backup(target)
        finally:target.close()
    files={'notes.json':json_text,'notes.csv':buffer.getvalue(),'viewer.html':viewer,
           'mysql_seed.sql':seed,'quality.json':json.dumps(report,ensure_ascii=False,indent=2)+'\n'}
    for name,content in files.items():
        (out/name).write_text(content,encoding='utf-8-sig' if name.endswith('.csv') else 'utf-8',newline='')
    manifest={'contract_version':1,'dataset_sha256':fingerprint,'records':len(items),'table_counts':counts,
              'files':{name:__import__('hashlib').sha256((out/name).read_bytes()).hexdigest()
                       for name in sorted([*files,'loom.db'])}}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {'status':'ok','records':len(items),'directory':str(out),'dataset_sha256':fingerprint}
