import copy
import json
import tempfile
import unittest
from pathlib import Path

from warehouse.classification import classify, contributions, restore_report
from warehouse.export import export_tables, read_export
from warehouse.ingest import ingest
from warehouse.marts import (classification_releases, publish_classification,
                             publish_topics, read_classification, read_topics)
from warehouse.queries import (overview, pending_contents, topic_contributions,
                               topic_versions, version_info)
from warehouse.storage import open_store


def classification_payload(batch='classification-v1', version='v1'):
    """两叶多父：music 同时挂 arts 与 root，heritage 同时挂 arts 与 root。"""
    return {
        'batch_id': batch,
        'tables': {
            'classification_versions': [
                {'version_id': version, 'label': version + ' 口径'},
            ],
            'classification_topics': [
                {'version_id': version, 'topic_id': 'root', 'label': '全部'},
                {'version_id': version, 'topic_id': 'arts', 'label': '文艺'},
                {'version_id': version, 'topic_id': 'music', 'label': '音乐'},
                {'version_id': version, 'topic_id': 'heritage', 'label': '非遗'},
            ],
            'classification_parents': [
                {'version_id': version, 'topic_id': 'arts', 'parent_id': 'root'},
                {'version_id': version, 'topic_id': 'music', 'parent_id': 'arts'},
                {'version_id': version, 'topic_id': 'music', 'parent_id': 'root'},
                {'version_id': version, 'topic_id': 'heritage', 'parent_id': 'arts'},
                {'version_id': version, 'topic_id': 'heritage', 'parent_id': 'root'},
            ],
            'content_topic_shares': [
                {'version_id': version, 'content_id': 'c1', 'topic_id': 'music', 'share': '0.6'},
                {'version_id': version, 'content_id': 'c1', 'topic_id': 'heritage', 'share': '0.4'},
                {'version_id': version, 'content_id': 'c2', 'topic_id': 'heritage', 'share': '1'},
            ],
        },
    }


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = open_store(self.root / 'store.duckdb')
        self.payload = json.loads(Path('samples/delivery.json').read_text())

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def load(self):
        return ingest(self.connection, copy.deepcopy(self.payload))

    def load_classification(self, payload=None):
        return ingest(self.connection, copy.deepcopy(payload or classification_payload()))

    def add_facts(self, rows):
        ingest(self.connection, {'batch_id': 'facts-' + rows[0]['fact_id'],
                                 'tables': {'distribution': rows}})

    def test_replay_keeps_source_rows(self):
        first = self.load()
        second = self.load()
        self.assertGreater(first['inserted'], 0)
        self.assertEqual(second['inserted'], 0)
        self.assertTrue(second['replayed'])
        self.assertEqual(self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_batch_collision_is_atomic(self):
        self.load()
        collision = copy.deepcopy(self.payload)
        collision['tables'] = {}
        with self.assertRaises(ValueError):
            ingest(self.connection, collision)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 1)

    def test_unknown_table_does_not_write_receipt(self):
        invalid = {'batch_id': 'bad', 'tables': {'missing_table': []}}
        with self.assertRaises(ValueError):
            ingest(self.connection, invalid)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)

    def test_different_batch_same_rows_is_not_duplicate(self):
        first = self.load()
        duplicate = copy.deepcopy(self.payload)
        duplicate['batch_id'] = 'retry-another-envelope'
        self.assertEqual(ingest(self.connection, duplicate)['inserted'], 0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM source_rows').fetchone()[0], first['inserted'])

    def test_export_roundtrip_and_hash(self):
        self.load()
        result = export_tables(self.connection, self.root / 'exports', 'release1')
        for table, entry in result['tables'].items():
            actual = read_export(self.connection, self.root / 'exports', 'release1', table)
            self.assertEqual(len(actual), entry['rows'])
            self.assertEqual(set(actual[0]) if actual else set(),
                             set(self.payload['tables'][table][0]) if actual else set())
        table = next(iter(result['tables']))
        output = self.root / 'exports' / 'release1' / (table + '.parquet')
        output.write_bytes(output.read_bytes() + b'broken')
        with self.assertRaises(ValueError):
            read_export(self.connection, self.root / 'exports', 'release1', table)

    def test_export_names_and_immutability(self):
        self.load()
        with self.assertRaises(ValueError):
            export_tables(self.connection, self.root, '../escape')
        export_tables(self.connection, self.root, 'stable')
        with self.assertRaises(FileExistsError):
            export_tables(self.connection, self.root, 'stable')

    def test_reopen_preserves_data(self):
        self.load()
        expected = overview(self.connection)
        self.connection.close()
        self.connection = open_store(self.root / 'store.duckdb')
        self.assertEqual(overview(self.connection), expected)

    def test_rejected_record_has_no_partial_state(self):
        invalid = copy.deepcopy(self.payload)
        populated = next(rows for rows in invalid['tables'].values() if rows)
        populated.append({})
        with self.assertRaises(ValueError):
            ingest(self.connection, invalid)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM source_rows').fetchone()[0], 0)

    # ---- 原有单主分类行为保持不变 ----

    def test_existing_primary_rollup(self):
        from warehouse.classification import ancestors
        self.load()
        self.assertEqual(ancestors(self.connection, 'music'), ['music', 'root'])
        root = next(row for row in classify(self.connection) if row['topic_id'] == 'root')
        self.assertEqual(root, {'topic_id': 'root', 'copies': 100, 'contents': 2})

    def test_cycle_is_not_published_as_total(self):
        from warehouse.classification import ancestors
        self.payload['tables']['topics'][0]['parent_id'] = 'music'
        self.load()
        with self.assertRaises(ValueError):
            ancestors(self.connection, 'music')

    def test_unassigned_does_not_disappear(self):
        self.payload['tables']['contents'][0]['primary_topic'] = ''
        self.load()
        self.assertEqual(next(row for row in classify(self.connection)
                              if row['topic_id'] == 'unassigned')['copies'], 60)

    def test_published_mart_is_not_live_tree(self):
        self.load()
        first = publish_topics(self.connection, 'month1')
        fact = dict(self.payload['tables']['distribution'][0], fact_id='f3')
        ingest(self.connection, {'batch_id': 'more-distribution',
                                 'tables': {'distribution': [fact]}})
        self.assertEqual(read_topics(self.connection, 'month1'), first)
        with self.assertRaises(ValueError):
            publish_topics(self.connection, 'month1')

    # ---- 多父、多叶子与份额汇总 ----

    def test_shared_paths_are_counted_once(self):
        self.load()
        self.load_classification()
        rows = {row['topic_id']: row for row in classify(self.connection, 'v1')}
        # c1=60 分 0.6/0.4，c2=40 全在 heritage，总额 100。
        # music 有 arts->root 和直接 root 两条路径，root 仍只收到 100。
        self.assertEqual(rows['music']['copies'], 36)
        self.assertEqual(rows['heritage']['copies'], 64)
        self.assertEqual(rows['arts']['copies'], 100)
        self.assertEqual(rows['root']['copies'], 100)

    def test_deduplicated_contents_with_multiple_leaf_shares(self):
        self.load()
        self.load_classification()
        rows = {row['topic_id']: row for row in classify(self.connection, 'v1')}
        # c1 在 music 与 heritage 下都有份额，汇总内容数只算一次。
        self.assertEqual(rows['root']['contents'], 2)
        self.assertEqual(rows['arts']['contents'], 2)
        self.assertEqual(rows['music']['contents'], 1)

    def test_deep_shared_branch_not_double_counted(self):
        payload = classification_payload()
        payload['tables']['classification_topics'].extend([
            {'version_id': 'v1', 'topic_id': 'shared', 'label': '共用'},
        ])
        payload['tables']['classification_parents'].extend([
            # shared 是通往 root 的唯一路径，music 经 arts、heritage 两条路到达。
            {'version_id': 'v1', 'topic_id': 'shared', 'parent_id': 'root'},
            {'version_id': 'v1', 'topic_id': 'arts', 'parent_id': 'shared'},
        ])
        self.load()
        self.load_classification(payload)
        rows = {row['topic_id']: row for row in classify(self.connection, 'v1')}
        self.assertEqual(rows['shared']['copies'], 100)
        self.assertEqual(rows['root']['copies'], 100)

    def test_trace_from_parent_to_content_and_share(self):
        from decimal import Decimal
        self.load()
        self.load_classification()
        detail = contributions(self.connection, 'v1', 'root')
        self.assertEqual(
            [(row['content_id'], row['leaf_topic_id'], row['share'], row['copies'])
             for row in detail],
            [('c1', 'heritage', Decimal('0.4'), Decimal('24')),
             ('c1', 'music', Decimal('0.6'), Decimal('36')),
             ('c2', 'heritage', Decimal('1'), Decimal('40'))])

    def test_report_includes_identity_summary_and_detail_together(self):
        self.load()
        self.load_classification()
        report = overview(self.connection, 'v1', '2026-11-09', '2026-11-09')
        self.assertEqual(report['version_id'], 'v1')
        self.assertEqual(report['version_label'], 'v1 口径')
        self.assertEqual(report['start'], '2026-11-09')
        self.assertIn('topics', report)
        self.assertIn('contributions', report)
        self.assertIn('unassigned_contents', report)
        self.assertEqual({row['topic_id'] for row in report['topics']},
                         {'root', 'arts', 'music', 'heritage', 'unassigned'})
        self.assertEqual({row['topic_id']: row['label'] for row in report['topics']}['music'],
                         '音乐')

    def test_unassigned_content_is_explicit(self):
        self.load()
        self.load_classification()
        self.add_facts([{'fact_id': 'f3', 'day': '2026-11-10', 'content_id': 'c3', 'copies': 10}])
        rows = {row['topic_id']: row for row in classify(self.connection, 'v1')}
        self.assertEqual(rows['unassigned']['copies'], 10)
        self.assertEqual(rows['root']['copies'], 100)
        self.assertIn('c3', pending_contents(self.connection, 'v1'))
        detail = contributions(self.connection, 'v1', 'unassigned')
        self.assertEqual([(row['content_id'], row['share'], row['copies']) for row in detail],
                         [('c3', None, 10)])

    # ---- 非法结构被正式取数拒绝 ----

    def test_cycle_rejected_by_formal_read(self):
        payload = classification_payload()
        payload['tables']['classification_parents'].append(
            {'version_id': 'v1', 'topic_id': 'root', 'parent_id': 'music'})
        self.load()
        self.load_classification(payload)
        with self.assertRaises(ValueError):
            classify(self.connection, 'v1')
        with self.assertRaises(ValueError):
            publish_classification(self.connection, 'bad', 'v1')
        self.assertEqual(classification_releases(self.connection), [])

    def test_missing_node_rejected_by_formal_read(self):
        payload = classification_payload()
        payload['tables']['classification_parents'].append(
            {'version_id': 'v1', 'topic_id': 'music', 'parent_id': 'ghost'})
        self.load()
        self.load_classification(payload)
        with self.assertRaises(ValueError):
            classify(self.connection, 'v1')

    def test_non_conserving_shares_rejected_by_formal_read(self):
        payload = classification_payload()
        payload['tables']['content_topic_shares'] = [
            row for row in payload['tables']['content_topic_shares']
            if not (row['content_id'] == 'c1' and row['topic_id'] == 'heritage')
        ]
        self.load()
        self.load_classification(payload)
        with self.assertRaises(ValueError):
            classify(self.connection, 'v1')
        with self.assertRaises(ValueError):
            contributions(self.connection, 'v1', 'root')

    def test_share_on_non_leaf_rejected(self):
        payload = classification_payload()
        payload['tables']['content_topic_shares'] = [
            {'version_id': 'v1', 'content_id': 'c1', 'topic_id': 'arts', 'share': '1'},
            {'version_id': 'v1', 'content_id': 'c2', 'topic_id': 'heritage', 'share': '1'},
        ]
        self.load()
        self.load_classification(payload)
        with self.assertRaises(ValueError):
            classify(self.connection, 'v1')

    def test_invalid_share_value_is_rejected_at_ingest(self):
        payload = classification_payload()
        payload['tables']['content_topic_shares'][0]['share'] = '1.5'
        with self.assertRaises(ValueError):
            self.load_classification(payload)

    def test_unknown_version_is_rejected(self):
        payload = classification_payload()
        payload['tables'].pop('classification_versions')
        with self.assertRaises(ValueError):
            self.load_classification(payload)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)

    # ---- 版本隔离与集市冻结 ----

    def test_new_version_reads_same_period_without_rewriting_history(self):
        self.load()
        self.load_classification()
        first = publish_classification(self.connection, 'month1', 'v1',
                                       '2026-11-01', '2026-11-30')
        v2 = classification_payload('classification-v2', 'v2')
        # 编辑整批调整：c1 改为全部归 music，结构不变。
        v2['tables']['content_topic_shares'] = [
            {'version_id': 'v2', 'content_id': 'c1', 'topic_id': 'music', 'share': '1'},
            {'version_id': 'v2', 'content_id': 'c2', 'topic_id': 'heritage', 'share': '1'},
        ]
        ingest(self.connection, copy.deepcopy(v2))
        second = publish_classification(self.connection, 'month2', 'v2',
                                        '2026-11-01', '2026-11-30')
        self.assertEqual(first['topics'], read_classification(self.connection, 'month1')['topics'])
        self.assertEqual(first['version_id'], 'v1')
        self.assertEqual(second['version_id'], 'v2')
        rows = {row['topic_id']: row for row in second['topics']}
        self.assertEqual(rows['music']['copies'], 60)
        self.assertEqual(rows['heritage']['copies'], 40)
        self.assertEqual(rows['root']['copies'], 100)

    def test_published_version_cannot_be_rewritten(self):
        self.load()
        self.load_classification()
        publish_classification(self.connection, 'month1', 'v1')
        changed = classification_payload('classification-v1-edit', 'v1')
        changed['tables']['classification_versions'][0]['label'] = '被改写的口径'
        with self.assertRaises(ValueError):
            ingest(self.connection, changed)
        added = {'batch_id': 'classification-v1-add', 'tables': {
            'content_topic_shares': [
                {'version_id': 'v1', 'content_id': 'c2', 'topic_id': 'music', 'share': '0.5'}]}}
        with self.assertRaises(ValueError):
            ingest(self.connection, added)
        self.assertEqual(
            self.connection.execute('SELECT count(*) FROM receipts').fetchone()[0], 2)

    def test_export_manifest_carries_classification_identity(self):
        self.load()
        self.load_classification()
        publish_classification(self.connection, 'month1', 'v1')
        manifest = export_tables(self.connection, self.root / 'exports', 'release1')
        self.assertIn('classification_topics', manifest['tables'])
        self.assertEqual(manifest['classification_versions'],
                         [{'version_id': 'v1', 'label': 'v1 口径'}])
        self.assertEqual(manifest['classification_marts'][0]['release_id'], 'month1')
        self.assertEqual(manifest['classification_marts'][0]['version_id'], 'v1')
        rows = read_export(self.connection, self.root / 'exports', 'release1',
                           'content_topic_shares')
        self.assertEqual(len(rows), 3)

    def test_release_is_immutable_for_period(self):
        self.load()
        self.load_classification()
        publish_classification(self.connection, 'month1', 'v1', '2026-11-01', '2026-11-30')
        self.add_facts([{'fact_id': 'f3', 'day': '2026-11-09',
                         'content_id': 'c2', 'copies': 5}])
        with self.assertRaises(ValueError):
            publish_classification(self.connection, 'month1', 'v1',
                                   '2026-11-01', '2026-11-30')
        # 同一事实期间可在新版本标识下另行取数。
        again = publish_classification(self.connection, 'month1-restated', 'v1',
                                       '2026-11-01', '2026-11-30')
        self.assertEqual(next(row for row in again['topics']
                              if row['topic_id'] == 'heritage')['copies'], 69)

    def test_version_identity_and_releases_are_visible(self):
        self.load()
        self.load_classification()
        publish_classification(self.connection, 'month1', 'v1')
        self.assertEqual(topic_versions(self.connection),
                         [{'version_id': 'v1', 'label': 'v1 口径'}])
        info = version_info(self.connection, 'v1')
        self.assertEqual(info['version_id'], 'v1')
        self.assertEqual(info['releases'], ['month1'])
        self.assertEqual(info['leaf_count'], 2)
        self.assertEqual(classification_releases(self.connection, 'v1')[0]['release_id'], 'month1')
        self.assertEqual(read_classification(self.connection, 'month1')['version_id'], 'v1')

    def test_mart_roundtrip_keeps_decimal_values(self):
        self.load()
        self.load_classification()
        published = publish_classification(self.connection, 'month1', 'v1')
        stored = read_classification(self.connection, 'month1')
        for key in ('version_id', 'version_label', 'topics', 'contributions',
                    'unassigned_contents', 'source_digest'):
            self.assertEqual(stored[key], published[key], key)
        self.assertEqual(restore_report(stored), stored)


if __name__ == '__main__':
    unittest.main()
