import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from .storage import canonical
from .tables import TABLES


def export_tables(connection, destination, revision):
    if not isinstance(revision, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', revision):
        raise ValueError('导出版本标识非法')
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    target = root / revision
    if target.exists():
        raise FileExistsError('导出版本已存在')
    stage = Path(tempfile.mkdtemp(prefix='.writing-', dir=root))
    connection.execute('BEGIN TRANSACTION')
    try:
        manifest = {'revision': revision, 'tables': {}}
        for table, (columns, keys) in TABLES.items():
            filename = table + '.parquet'
            output = stage / filename
            escaped = str(output).replace("'", "''")
            ordering = ','.join('"' + key + '"' for key in keys)
            connection.execute('COPY (SELECT * FROM "' + table + '" ORDER BY ' + ordering +
                               ") TO '" + escaped + "' (FORMAT PARQUET, COMPRESSION ZSTD)")
            count = connection.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]
            manifest['tables'][table] = {'file': filename, 'rows': count,
                'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'keys': list(keys)}
        # 分类版本随业务表导出，回读方可以据此判断使用了哪一版分类。
        versions = connection.execute(
            'SELECT version_id,label FROM classification_versions ORDER BY version_id').fetchall()
        manifest['classification_versions'] = [
            {'version_id': row[0], 'label': row[1]} for row in versions]
        releases = connection.execute(
            'SELECT release_id,version_id,source_digest FROM classification_marts '
            'ORDER BY release_id').fetchall()
        manifest['classification_marts'] = [
            {'release_id': row[0], 'version_id': row[1], 'source_digest': row[2]}
            for row in releases]
        manifest['batches'] = [row[0] for row in connection.execute(
            'SELECT batch_id FROM receipts ORDER BY batch_id').fetchall()]
        (stage / 'manifest.json').write_text(canonical(manifest), encoding='utf-8')
        connection.execute('COMMIT')
        os.rename(stage, target)
    except BaseException:
        try:
            connection.execute('ROLLBACK')
        except Exception:
            pass
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return manifest


def read_export(connection, destination, revision, table):
    if table not in TABLES or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', revision):
        raise ValueError('导出查询标识非法')
    root = Path(destination) / revision
    manifest = json.loads((root / 'manifest.json').read_text())
    entry = manifest['tables'][table]
    data = root / (table + '.parquet')
    if entry['file'] != data.name or hashlib.sha256(data.read_bytes()).hexdigest() != entry['sha256']:
        raise ValueError('列式文件与目录不一致')
    cursor = connection.execute('SELECT * FROM read_parquet(?)', [str(data)])
    names = [column[0] for column in cursor.description]
    rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    if len(rows) != entry['rows']:
        raise ValueError('导出行数不一致')
    return rows
