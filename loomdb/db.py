"""Checksum-locked SQLite migrations and explicit connection ownership."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'runtime' / 'loom.db'
MIGRATIONS = ROOT / 'migrations' / 'sqlite'


class ContractError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@contextmanager
def connect(path, *, write=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise ContractError(f'数据库不存在：{path}；先执行 migrate')
    conn = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=5000')
    try:
        yield conn
        if write:
            conn.commit()
    except BaseException:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()


def migration_files(folder=MIGRATIONS):
    files = sorted(Path(folder).glob('[0-9][0-9][0-9]_*.sql'))
    if not files or [int(f.name[:3]) for f in files] != list(range(1,len(files)+1)):
        raise ContractError('迁移文件必须从 001 连续编号')
    return files


def verify_migrations(conn, folder=MIGRATIONS):
    files = migration_files(folder)
    expected = {f.name: sha(f.read_text(encoding='utf-8')) for f in files}
    applied = dict(conn.execute('SELECT name,checksum FROM schema_migrations'))
    if any(name not in expected or expected[name] != checksum for name,checksum in applied.items()):
        raise ContractError('已应用迁移缺失或被改写；恢复历史迁移并新增版本')
    if list(sorted(applied)) != [f.name for f in files[:len(applied)]]:
        raise ContractError('已应用迁移不是连续前缀')
    return files, applied


def migrate(path=DEFAULT_DB, folder=MIGRATIONS):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and 'schema_migrations' not in tables:
            raise ContractError('拒绝接管无迁移记录的已有数据库；请选择新路径')
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations '
                     '(name TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)')
        conn.commit()
        files,applied = verify_migrations(conn,folder)
        added=[]
        for f in files:
            if f.name in applied:
                continue
            sql=f.read_text(encoding='utf-8')
            # Only repository-controlled names/checksums are interpolated here.
            name=f.name.replace("'","''")
            conn.executescript('BEGIN IMMEDIATE;\n'+sql+
                f"\nINSERT INTO schema_migrations VALUES('{name}','{sha(sql)}','{now()}');\nCOMMIT;")
            added.append(f.name)
        return {'status':'ok','applied':added,'version':len(files)}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
