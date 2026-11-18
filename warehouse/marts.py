import json
import re

from .classification import restore_report, version_report, version_source
from .storage import canonical, digest, records, transaction
from .tables import TABLES


def _release_id(release):
    if not isinstance(release, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', release):
        raise ValueError('集市版本标识非法')
    return release


def publish_topics(connection, release):
    from .classification import classify
    rows = classify(connection)
    # 旧集市只覆盖原来的三张业务表，摘要口径保持不变。
    source = {table: records(connection, 'SELECT * FROM "' + table + '" ORDER BY 1')
              for table in TABLES if table in ('topics', 'contents', 'distribution')}
    signature = digest(source)
    prior = connection.execute('SELECT source_digest,rows_json FROM topic_marts WHERE release_id=?',
                               [release]).fetchone()
    if prior:
        if prior[0] != signature:
            raise ValueError('已有集市版本不允许被新数据覆盖')
        return json.loads(prior[1])
    connection.execute('INSERT INTO topic_marts VALUES (?,?,?)',
                       [release, signature, canonical(rows)])
    return rows


def read_topics(connection, release):
    row = connection.execute('SELECT rows_json FROM topic_marts WHERE release_id=?',
                             [release]).fetchone()
    if row is None:
        raise ValueError('专题集市版本不存在')
    return json.loads(row[0])


def publish_classification(connection, release, version_id, start=None, end=None):
    """发布一个分类版本的月度集市。

    集市同时记录分类版本、事实期间和来源摘要；已发布内容按当时的分类解释
    冻结，同一事实期间的新口径应使用新的集市版本。
    """
    _release_id(release)
    # 先完整校验并计算一次，非法结构不会进入集市表。
    report = version_report(connection, version_id, start, end)
    signature = digest(version_source(connection, version_id, start, end))
    with transaction(connection):
        prior = connection.execute(
            'SELECT version_id,source_digest,rows_json FROM classification_marts '
            'WHERE release_id=?', [release]).fetchone()
        if prior:
            if prior[0] != version_id or prior[1] != signature:
                raise ValueError('已有集市版本不允许被新数据或新分类覆盖')
            return dict(restore_report(json.loads(prior[2])), source_digest=prior[1])
        connection.execute(
            'INSERT INTO classification_marts VALUES (?,?,?,?)',
            [release, version_id, signature, canonical(report)])
    return dict(report, source_digest=signature)


def read_classification(connection, release):
    row = connection.execute(
        'SELECT version_id,source_digest,rows_json FROM classification_marts '
        'WHERE release_id=?', [release]).fetchone()
    if row is None:
        raise ValueError('分类集市版本不存在')
    report = restore_report(json.loads(row[2]))
    # 即使存储被外部替换，也明确返回集市自身的版本身份。
    report['version_id'] = row[0]
    report['source_digest'] = row[1]
    return report


def classification_releases(connection, version_id=None):
    clauses = ''
    parameters = []
    if version_id is not None:
        clauses = ' WHERE version_id=?'
        parameters.append(version_id)
    return [{'release_id': row['release_id'], 'version_id': row['version_id'],
             'source_digest': row['source_digest']}
            for row in records(connection,
                               'SELECT release_id,version_id,source_digest '
                               'FROM classification_marts' + clauses +
                               ' ORDER BY release_id', parameters)]
