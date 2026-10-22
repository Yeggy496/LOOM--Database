import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from loomdb import corpus
from loomdb.__main__ import main
from loomdb.db import ContractError, MIGRATIONS, ROOT, connect, migrate
from loomdb.evaluation import evaluate
from loomdb.exporting import export_bundle, safe_csv


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=self.root/'corpus.db'
        self.input=self.root/'batch.json'
        self.example=json.loads((ROOT/'data/public_references.json').read_text(encoding='utf-8'))['records'][0]
        migrate(self.db)

    def write(self,*rows):
        self.input.write_text(json.dumps({'schema_version':1,'records':rows},ensure_ascii=False),encoding='utf-8')
        return self.input

    def load_one(self):
        corpus.import_file(self.db,self.write(self.example))

    def counts(self):
        with connect(self.db) as c:
            return {t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
                    ('sources','samples','annotation_revisions','tags','annotation_tags','source_checks','audit_events','import_runs','sample_search')}

    def test_reimport_preserves_human_work(self):
        self.load_one()
        corpus.revise(self.db,'pub-001',expected_revision=1,actor='reviewer',reason='核对原文',human=True,
                      summary='人工整理后的候选摘要',review_status='verified')
        result=corpus.import_file(self.db,self.write(self.example))
        self.assertEqual((result['inserted'],result['skipped']),(0,1))
        r=corpus.records(self.db)[0]
        self.assertEqual((r['revision'],r['review_status'],r['summary_zh']),(2,'verified','人工整理后的候选摘要'))

    def test_failure_after_valid_insert_rolls_back_whole_batch(self):
        self.load_one();before=self.counts()
        fresh=copy.deepcopy(self.example);fresh.update(id='fresh',body='An independently collected different excerpt.')
        conflict=copy.deepcopy(self.example);conflict['body']='Changed original'
        with self.assertRaises(ContractError):corpus.import_file(self.db,self.write(fresh,conflict))
        self.assertEqual(self.counts(),before)

    def test_same_hash_different_id_rejected_without_partial_writes(self):
        self.load_one();before=self.counts()
        duplicate=copy.deepcopy(self.example);duplicate['id']='duplicate'
        with self.assertRaises(sqlite3.IntegrityError):corpus.import_file(self.db,self.write(duplicate))
        self.assertEqual(self.counts(),before)

    def test_author_normalization_and_split_guard(self):
        self.load_one();before=self.counts()
        other=copy.deepcopy(self.example);other.update(id='other',body='Different reference',split='test')
        other['source']['url']='https://example.org/new'
        other['source']['author']='  PYTHON   Documentation Contributors  '
        with self.assertRaises(sqlite3.IntegrityError):corpus.import_file(self.db,self.write(other))
        self.assertEqual(self.counts(),before)

    def test_public_and_synthetic_provenance_cannot_be_confused(self):
        for updates in ({'origin':'synthetic'},{'origin':'public_excerpt','source':None},{'training_allowed':True}):
            r=copy.deepcopy(self.example);r.update(updates)
            with self.assertRaises(ContractError):corpus.import_file(self.db,self.write(r))
        self.assertEqual(self.counts()['samples'],0)

    def test_bad_fields_url_date_and_oversized_excerpt(self):
        cases=[{'id':'bad/id'},{'body':'\0bad'},{'body':'x'*501},{'collected_on':'2026-02-30'}]
        for change in cases:
            r=copy.deepcopy(self.example);r.update(change)
            with self.assertRaises(ValueError):corpus.import_file(self.db,self.write(r))
        r=copy.deepcopy(self.example);r['source']['url']='https://name:secret@example.org'
        with self.assertRaises(ContractError):corpus.import_file(self.db,self.write(r))

    def test_source_metadata_conflict_is_not_silently_accepted(self):
        self.load_one();before=self.counts()
        r=copy.deepcopy(self.example);r['source']['author']='Different author'
        with self.assertRaises(ContractError):corpus.import_file(self.db,self.write(r))
        self.assertEqual(self.counts(),before)

    def test_duplicate_id_in_batch_is_rejected(self):
        with self.assertRaises(ContractError):corpus.import_file(self.db,self.write(self.example,self.example))
        self.assertEqual(self.counts()['samples'],0)

    def test_original_annotations_and_audit_immutable_in_sql(self):
        self.load_one()
        for statement in ["UPDATE samples SET body='oops'",'DELETE FROM samples',
                          "UPDATE annotation_revisions SET summary_zh='oops'",'DELETE FROM annotation_revisions',
                          'DELETE FROM source_checks','DELETE FROM annotation_tags','DELETE FROM audit_events',
                          "UPDATE sources SET author='oops'"]:
            with self.assertRaises(sqlite3.IntegrityError):
                with connect(self.db,write=True) as c:c.execute(statement)
        self.assertEqual(corpus.check(self.db)['status'],'ok')

    def test_no_undeclared_tags_can_be_appended_to_history(self):
        self.load_one()
        with self.assertRaises(sqlite3.IntegrityError):
            with connect(self.db,write=True) as c:
                c.execute("INSERT INTO tags VALUES('undeclared')")
                c.execute("INSERT INTO annotation_tags VALUES('pub-001:r1','undeclared')")
        self.assertEqual(corpus.check(self.db)['status'],'ok')

    def test_optimistic_revision_conflict_does_not_overwrite(self):
        self.load_one()
        corpus.revise(self.db,'pub-001',expected_revision=1,actor='A',reason='clarify',summary='新的摘要')
        before=self.counts()
        with self.assertRaises(ContractError):
            corpus.revise(self.db,'pub-001',expected_revision=1,actor='B',reason='stale',summary='过期摘要')
        self.assertEqual(self.counts(),before)
        self.assertEqual(corpus.records(self.db)[0]['summary_zh'],'新的摘要')

    def test_review_keeps_history_and_refreshes_search_index(self):
        self.load_one()
        corpus.revise(self.db,'pub-001',expected_revision=1,actor='A',reason='scope',summary='一个全新的独特短语',tag_names=['全新标签'])
        self.assertEqual(len(corpus.search(self.db,'独特短语')['results']),1)
        self.assertEqual(len(corpus.search(self.db,'全新标签')['results']),1)
        with connect(self.db) as c:
            old=c.execute("SELECT summary_zh FROM annotation_revisions WHERE id='pub-001:r1'").fetchone()[0]
        self.assertEqual(old,self.example['annotation']['summary_zh'])
        self.assertEqual(corpus.check(self.db)['status'],'ok')

    def test_verification_requires_explicit_human_and_protects_later_edits(self):
        self.load_one()
        with self.assertRaises(ContractError):
            corpus.revise(self.db,'pub-001',expected_revision=1,actor='AI',reason='check',review_status='verified')
        corpus.revise(self.db,'pub-001',expected_revision=1,actor='human',reason='checked',review_status='verified',human=True)
        with self.assertRaises(ContractError):
            corpus.revise(self.db,'pub-001',expected_revision=2,actor='AI',reason='rewrite',summary='AI overwrite')
        corpus.revise(self.db,'pub-001',expected_revision=2,actor='human',reason='reopen',human=True)
        r=corpus.records(self.db)[0]
        self.assertEqual((r['review_status'],r['reviewer']),('pending',None))

    def test_noop_review_creates_no_history(self):
        self.load_one();before=self.counts()
        result=corpus.revise(self.db,'pub-001',expected_revision=1,actor='A',reason='same')
        self.assertEqual(result['status'],'unchanged');self.assertEqual(self.counts(),before)

    def test_search_treats_operators_and_injection_as_text(self):
        self.load_one()
        for q in ['" OR "','%','_',"' OR 1=1 --",'NOT sqlite','\"']:
            self.assertEqual(corpus.search(self.db,q)['results'],[])
        self.assertEqual(corpus.search(self.db,'list')['method'],'fts5_trigram_bm25')
        self.assertEqual(len(corpus.search(self.db,'列表')['results']),1)
        with self.assertRaises(ContractError):corpus.search(self.db,' ')
        with self.assertRaises(ContractError):corpus.search(self.db,'list',limit=0)
        self.assertEqual(corpus.search(self.db,'list',split='test')['results'],[])

    def test_migrations_are_idempotent_and_detect_edit_or_missing_file(self):
        self.assertEqual(migrate(self.db)['applied'],[])
        folder=self.root/'migrations';shutil.copytree(MIGRATIONS,folder)
        first=next(folder.glob('001*'));first.write_text(first.read_text(encoding='utf-8')+'\n-- changed',encoding='utf-8')
        with self.assertRaises(ContractError):migrate(self.db,folder)
        first.unlink()
        with self.assertRaises(ContractError):migrate(self.db,folder)

    def test_failed_migration_rolls_back_ddl_and_ledger(self):
        folder=self.root/'migrations';shutil.copytree(MIGRATIONS,folder)
        (folder/'003_broken.sql').write_text('CREATE TABLE should_disappear(id INT); INVALID SQL;',encoding='utf-8')
        with self.assertRaises(sqlite3.Error):migrate(self.db,folder)
        with connect(self.db) as c:
            self.assertFalse(c.execute("SELECT 1 FROM sqlite_master WHERE name='should_disappear'").fetchall())
            self.assertEqual(c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],2)

    def test_migrate_refuses_unmanaged_existing_database(self):
        legacy=self.root/'legacy.db'
        c=sqlite3.connect(legacy);c.execute('CREATE TABLE precious(id)');c.close()
        with self.assertRaises(ContractError):migrate(legacy)

    def test_missing_read_only_database_is_never_created(self):
        missing=self.root/'missing.db'
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(main(['--db',str(missing),'check']),1)
        self.assertFalse(missing.exists())

    def test_export_preserves_quotes_unicode_provenance_and_bom(self):
        r=copy.deepcopy(self.example);r.update(body='Text with quotation " and backslash \\ and emoji 🌱',title='=1+1')
        corpus.import_file(self.db,self.write(r));out=self.root/'exports'
        export_bundle(self.db,out)
        j=json.loads((out/'notes.json').read_text(encoding='utf-8'))
        self.assertEqual(j['records'][0]['body'],r['body'])
        self.assertEqual(j['records'][0]['url'],r['source']['url'])
        self.assertTrue((out/'notes.csv').read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn("'=1+1",(out/'notes.csv').read_text(encoding='utf-8-sig'))
        self.assertEqual(corpus.records(out/'loom.db'),corpus.records(self.db))
        manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
        for name,digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((out/name).read_bytes()).hexdigest(),digest)
        self.assertIn('START TRANSACTION;', (out/'mysql_seed.sql').read_text())

    def test_viewer_script_embedding_and_output_path_safety(self):
        r=copy.deepcopy(self.example);r['body']='</script><img src=x onerror=alert(1)>'
        corpus.import_file(self.db,self.write(r));out=self.root/'out'
        export_bundle(self.db,out)
        html=(out/'viewer.html').read_text(encoding='utf-8')
        self.assertNotIn(r['body'],html)
        self.assertIn('\\u003c/script>',html)
        with self.assertRaises(ContractError):export_bundle(self.db,ROOT/'data')
        self.assertEqual(safe_csv('  @SUM(1)'),"'  @SUM(1)")
        self.assertEqual(safe_csv('\t=1'),"'\t=1")

    def test_check_detects_index_drift(self):
        self.load_one()
        with connect(self.db,write=True) as c:c.execute("DELETE FROM sample_search")
        self.assertEqual(corpus.check(self.db)['status'],'failed')

    def test_all_seeds_and_candidate_benchmark(self):
        corpus.import_file(self.db,ROOT/'data/public_references.json')
        corpus.import_file(self.db,ROOT/'data/synthetic_inbox.json')
        report=corpus.check(self.db)
        self.assertEqual((report['records'],report['public_excerpt'],report['synthetic']),(32,20,12))
        self.assertEqual(report['human_verified'],0)
        self.assertEqual(report['status'],'ok')
        result=evaluate(self.db,ROOT/'data/retrieval_benchmark.json')
        self.assertEqual(result['overall']['queries'],16)
        self.assertFalse(result['semantic_search'])

    def test_invalid_benchmark_is_error_not_a_perfect_score(self):
        self.load_one();b=self.root/'benchmark.json'
        for payload in [{'version':1,'queries':[]},
                        {'version':1,'queries':[{'id':'q','query':'list','expected_ids':['missing'],'split':'dev','track':'keyword'}]}]:
            b.write_text(json.dumps(payload),encoding='utf-8')
            with self.assertRaises(ContractError):evaluate(self.db,b)

    def test_review_batch_rollback_and_canonical_replay(self):
        self.load_one();before=self.counts()
        good={'sample_id':'pub-001','expected_revision':1,'actor':'human','reason':'checked',
              'human':True,'review_status':'verified'}
        bad={**good,'sample_id':'missing'}
        path=self.root/'reviews.json'
        path.write_text(json.dumps({'schema_version':1,'records':[good,bad]}),encoding='utf-8')
        with self.assertRaises(ContractError):corpus.apply_reviews(self.db,path)
        self.assertEqual(self.counts(),before)
        path.write_text(json.dumps({'schema_version':1,'records':[good]}),encoding='utf-8')
        corpus.apply_reviews(self.db,path)
        corpus.revise(self.db,'pub-001',expected_revision=2,actor='next human',reason='new edit',human=True,summary='later decision')
        before=self.counts()
        path.write_text(json.dumps({'records':[good],'schema_version':1},indent=4),encoding='utf-8-sig')
        result=corpus.apply_reviews(self.db,path)
        self.assertEqual(result['results'][0]['status'],'already_applied')
        self.assertEqual(self.counts(),before)
        self.assertEqual(corpus.records(self.db)[0]['summary_zh'],'later decision')

    def test_edit_origin_tracks_ai_and_human_authorship(self):
        self.load_one()
        corpus.revise(self.db,'pub-001',expected_revision=1,actor='human',reason='edit',human=True,summary='human draft')
        self.assertEqual(corpus.records(self.db)[0]['annotation_origin'],'human')
        corpus.revise(self.db,'pub-001',expected_revision=2,actor='AI',reason='edit',summary='AI draft')
        self.assertEqual(corpus.records(self.db)[0]['annotation_origin'],'ai_assisted')


if __name__=='__main__':unittest.main()
