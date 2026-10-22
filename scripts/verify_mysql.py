"""CI-only integration check. Requires an empty disposable MySQL database.

Set MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD and MYSQL_DATABASE.
PyMySQL is an optional test dependency, not required by the SQLite toolkit.
"""
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loomdb.db import ROOT, connect
from loomdb.exporting import PORTABLE_TABLES


def statements(content):
    delimiter=';';buffer=[]
    for line in content.splitlines():
        stripped=line.strip()
        if not stripped or stripped.startswith('--'):continue
        if stripped.startswith('DELIMITER '):
            if buffer:raise ValueError('Unexpected delimiter in statement')
            delimiter=stripped.split()[1];continue
        buffer.append(line)
        if stripped.endswith(delimiter):
            value='\n'.join(buffer).rstrip()
            yield value[:-len(delimiter)]
            buffer=[]
    if buffer:raise ValueError('Unterminated SQL statement')


def main():
    import pymysql
    connection=pymysql.connect(host=os.environ['MYSQL_HOST'],port=int(os.environ.get('MYSQL_PORT','3306')),
        user=os.environ['MYSQL_USER'],password=os.environ['MYSQL_PASSWORD'],database=os.environ['MYSQL_DATABASE'],
        charset='utf8mb4',autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SHOW TABLES')
            if cursor.fetchall():raise RuntimeError('Refusing to touch a nonempty MySQL database')
            for file in (ROOT/'schema/mysql/V001__corpus.sql',ROOT/'exports/mysql_seed.sql'):
                for sql in statements(file.read_text(encoding='utf-8')):cursor.execute(sql)
            connection.commit()
            with connect(ROOT/'exports/loom.db') as sqlite:
                for table in PORTABLE_TABLES:
                    columns=[r[1] for r in sqlite.execute('PRAGMA table_info('+table+')')]
                    names=','.join('`'+c+'`' for c in columns)
                    expected=sorted([tuple(r) for r in sqlite.execute('SELECT '+names+' FROM '+table)],key=repr)
                    cursor.execute('SELECT '+names+' FROM `'+table+'`')
                    actual=sorted(cursor.fetchall(),key=repr)
                    if actual!=expected:raise AssertionError('Full-field round-trip differs: '+table)
            for sql in ("UPDATE samples SET title='tampered' WHERE id='pub-001'",
                        "UPDATE annotation_revisions SET summary_zh='tampered' WHERE id='pub-001:r1'",
                        "DELETE FROM source_checks WHERE sample_id='pub-001'",
                        "DELETE FROM audit_events WHERE sample_id='pub-001'"):
                try:cursor.execute(sql)
                except pymysql.MySQLError:connection.rollback()
                else:raise AssertionError('Immutable history protection failed')
            cursor.execute('SELECT VERSION()')
            print('MySQL',cursor.fetchone()[0],': 8 tables full-field round-trip and immutable-history checks passed')
    finally:connection.close()


if __name__=='__main__':main()
