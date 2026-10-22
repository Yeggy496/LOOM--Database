"""Read-only delivery verification: hashes, rebuild, row equivalence and baseline."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from loomdb.corpus import apply_reviews, check, import_file, records
from loomdb.db import ROOT, migrate
from loomdb.evaluation import evaluate


def main():
    out=ROOT/'exports'
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
    for name,digest in manifest['files'].items():
        if hashlib.sha256((out/name).read_bytes()).hexdigest()!=digest:
            raise AssertionError('Delivery hash differs: '+name)
    delivered=out/'loom.db'
    report=check(delivered)
    if report['status']!='ok':raise AssertionError(report)
    data=json.loads((out/'notes.json').read_text(encoding='utf-8'))
    if data['records']!=records(delivered):raise AssertionError('SQLite/JSON mismatch')
    html=(out/'viewer.html').read_text(encoding='utf-8')
    embedded=html.split('<script id="corpus" type="application/json">',1)[1].split('</script>',1)[0]
    if json.loads(embedded)!=data:raise AssertionError('Viewer/JSON mismatch')
    with tempfile.TemporaryDirectory() as folder:
        rebuilt=Path(folder)/'rebuilt.db'
        migrate(rebuilt)
        for name in ('public_references.json','synthetic_inbox.json'):
            import_file(rebuilt,ROOT/'data'/name)
        for revision in sorted((ROOT/'data/reviews').glob('*.json')):
            apply_reviews(rebuilt,revision)
        if records(rebuilt)!=records(delivered):raise AssertionError('Seeds do not reproduce the shipped corpus')
        actual=evaluate(rebuilt,ROOT/'data/retrieval_benchmark.json')
        baseline=json.loads((out/'retrieval_eval.json').read_text(encoding='utf-8'))
        if actual!=baseline:raise AssertionError('Frozen retrieval baseline differs')
    print(f"Delivery verified: hashes, {report['records']} records, seed rebuild, viewer payload and retrieval baseline")


if __name__=='__main__':main()
