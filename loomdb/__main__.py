import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .corpus import KINDS, apply_reviews, check, import_file, records, revise, search
from .db import ContractError, DEFAULT_DB, ROOT, connect, migrate
from .evaluation import evaluate
from .exporting import export_bundle


def parser():
    p=argparse.ArgumentParser(description='LOOM 独立样本数据库工具；不运行模型或应用服务')
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('migrate',help='应用带校验和的增量迁移')
    imp=sub.add_parser('import',help='事务导入 JSON 样本')
    imp.add_argument('file',type=Path)
    batch=sub.add_parser('apply-reviews',help='事务重放可版本管理的复核批次')
    batch.add_argument('file',type=Path)
    sub.add_parser('check',help='检查结构、哈希、分组和索引')
    show=sub.add_parser('show',help='查看原文、当前标注与全部历史')
    show.add_argument('id')
    s=sub.add_parser('search',help='全文子串检索，非语义搜索')
    s.add_argument('query');s.add_argument('--limit',type=int,default=20)
    s.add_argument('--origin',choices=['public_excerpt','synthetic'])
    s.add_argument('--split',choices=['dev','test'])
    r=sub.add_parser('review',help='以新版本修订或确认标注')
    r.add_argument('id');r.add_argument('--expected',type=int,required=True)
    r.add_argument('--actor',required=True);r.add_argument('--reason',required=True)
    r.add_argument('--summary');r.add_argument('--category',choices=sorted(KINDS))
    r.add_argument('--tag',action='append');r.add_argument('--human',action='store_true')
    r.add_argument('--status',choices=['pending','verified','rejected'],default='pending')
    e=sub.add_parser('export',help='生成 JSON/CSV/MySQL/SQLite/离线浏览页')
    e.add_argument('--out',type=Path,default=ROOT/'exports')
    b=sub.add_parser('evaluate',help='运行固定候选检索评测')
    b.add_argument('--benchmark',type=Path,default=ROOT/'data'/'retrieval_benchmark.json')
    b.add_argument('--output',type=Path)
    return p


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    a=parser().parse_args(argv)
    try:
        if a.command=='migrate':result=migrate(a.db)
        elif a.command=='import':result=import_file(a.db,a.file)
        elif a.command=='apply-reviews':result=apply_reviews(a.db,a.file)
        elif a.command=='check':result=check(a.db)
        elif a.command=='search':result=search(a.db,a.query,limit=a.limit,origin=a.origin,split=a.split)
        elif a.command=='review':
            result=revise(a.db,a.id,expected_revision=a.expected,actor=a.actor,reason=a.reason,
                          summary=a.summary,category=a.category,tag_names=a.tag,review_status=a.status,human=a.human)
        elif a.command=='show':
            found=[r for r in records(a.db) if r['id']==a.id]
            if not found:raise ContractError('样本不存在')
            with connect(a.db) as conn:
                history=[dict(r) for r in conn.execute('SELECT * FROM annotation_revisions WHERE sample_id=? ORDER BY revision',(a.id,))]
            result={'sample':found[0],'annotation_history':history}
        elif a.command=='export':result=export_bundle(a.db,a.out)
        else:
            result=evaluate(a.db,a.benchmark)
            if a.output:
                if a.output.resolve() in {a.db.resolve(),a.benchmark.resolve()} or a.output.suffix!='.json':
                    raise ContractError('评测输出须为独立 JSON 文件，不能覆盖数据库或题目')
                a.output.parent.mkdir(parents=True,exist_ok=True)
                a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return int(result.get('status')=='failed')
    except (ContractError,ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'status':'error','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
