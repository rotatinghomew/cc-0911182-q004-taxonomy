from .contracts import normalize
from .storage import canonical, digest, transaction
from .tables import CLASSIFICATION_TABLES, TABLES


def _classification_touched(tables):
    return any(table in CLASSIFICATION_TABLES for table in tables)


def _check_frozen(connection, table, row):
    """已被集市发布的分类版本不能通过新增行静默改写。"""
    version_id = row['version_id']
    frozen = connection.execute(
        'SELECT count(*) FROM classification_marts WHERE version_id=?',
        [version_id]).fetchone()[0]
    if frozen:
        raise ValueError('已发布的分类版本不能改写：' + version_id)


def _version_exists(connection, version_id):
    return connection.execute(
        'SELECT 1 FROM classification_versions WHERE version_id=?',
        [version_id]).fetchone() is not None


def ingest(connection, payload):
    if set(payload) != {'batch_id', 'tables'}:
        raise ValueError('报送必须包含批号和数据表')
    batch = payload['batch_id']
    if not isinstance(batch, str) or not batch.strip():
        raise ValueError('批号不能为空')
    if not isinstance(payload['tables'], dict):
        raise ValueError('数据表必须为对象')
    signature = digest(payload)
    prepared = []
    for table, rows in payload['tables'].items():
        if table not in TABLES:
            raise ValueError('未知数据表：' + table)
        if not isinstance(rows, list):
            raise ValueError('表记录必须为数组')
        columns, keys = TABLES[table]
        for original in rows:
            if not isinstance(original, dict) or set(original) != set(columns):
                raise ValueError('记录字段与契约不符：' + table)
            row = normalize(table, original)
            prepared.append((table, columns, keys, row))
    with transaction(connection):
        prior = connection.execute('SELECT signature FROM receipts WHERE batch_id=?', [batch]).fetchone()
        if prior:
            if prior[0] != signature:
                raise ValueError('相同批号内容不一致')
            return {'batch_id': batch, 'inserted': 0, 'replayed': True}
        if _classification_touched(payload['tables']):
            # 分类行的版本必须已存在，或在本批次内一并声明。
            declared = {row['version_id']
                        for row in payload['tables'].get('classification_versions', [])}
            for table in ('classification_topics', 'classification_parents',
                          'content_topic_shares'):
                for row in payload['tables'].get(table, []):
                    if row['version_id'] not in declared and not _version_exists(
                            connection, row['version_id']):
                        raise ValueError('分类版本不存在：' + row['version_id'])
        inserted = 0
        for table, columns, keys, row in prepared:
            existing = connection.execute('SELECT payload FROM source_rows WHERE table_name=? AND row_key=?',
                [table, canonical([row[key] for key in keys])]).fetchone()
            serialized = canonical(row)
            if existing:
                if existing[0] != serialized:
                    raise ValueError('业务主键内容冲突：' + table)
                continue
            if table in CLASSIFICATION_TABLES:
                _check_frozen(connection, table, row)
            names = ','.join('"' + column + '"' for column in columns)
            marks = ','.join('?' for column in columns)
            connection.execute('INSERT INTO "' + table + '" (' + names + ') VALUES (' + marks + ')',
                [row[column] for column in columns])
            connection.execute('INSERT INTO source_rows VALUES (?,?,?,?)',
                [table, canonical([row[key] for key in keys]), serialized, batch])
            inserted += 1
        connection.execute('INSERT INTO receipts VALUES (?,?,?)', [batch, signature, inserted])
    return {'batch_id': batch, 'inserted': inserted, 'replayed': False}
