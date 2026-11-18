import argparse
import json
from pathlib import Path

from .export import export_tables
from .ingest import ingest
from .marts import (classification_releases, publish_classification,
                    read_classification)
from .queries import (overview, pending_contents, topic_contributions,
                      topic_versions, version_info)
from .storage import open_store


def main():
    parser = argparse.ArgumentParser(description='本地仓库日常接收与核对')
    parser.add_argument('--database', default='warehouse.duckdb')
    commands = parser.add_subparsers(dest='action', required=True)
    load = commands.add_parser('load', help='接收一个业务包')
    load.add_argument('file', type=Path)
    commands.add_parser('overview', help='查看现有业务汇总（旧单主分类）')

    summary = commands.add_parser('topics', help='按分类版本查看专题汇总')
    summary.add_argument('version_id')
    summary.add_argument('--start')
    summary.add_argument('--end')

    contrib = commands.add_parser('contributions', help='从上级追到内容与份额')
    contrib.add_argument('version_id')
    contrib.add_argument('--topic')
    contrib.add_argument('--start')
    contrib.add_argument('--end')

    pending = commands.add_parser('pending', help='查看待分类内容')
    pending.add_argument('version_id')

    info = commands.add_parser('version-info', help='查看分类版本身份与已发布集市')
    info.add_argument('version_id')

    commands.add_parser('versions', help='列出分类版本')

    publish = commands.add_parser('publish', help='按分类版本发布月度集市')
    publish.add_argument('release')
    publish.add_argument('version_id')
    publish.add_argument('--start')
    publish.add_argument('--end')

    read = commands.add_parser('read-mart', help='按集市版本读取分类结果')
    read.add_argument('release')

    releases = commands.add_parser('marts', help='列出已发布分类集市')
    releases.add_argument('--version', dest='version_id')

    output = commands.add_parser('export', help='冻结列式导出')
    output.add_argument('destination', type=Path)
    output.add_argument('revision')
    args = parser.parse_args()
    connection = open_store(args.database)
    try:
        if args.action == 'load':
            result = ingest(connection, json.loads(args.file.read_text(encoding='utf-8')))
        elif args.action == 'export':
            result = export_tables(connection, args.destination, args.revision)
        elif args.action == 'overview':
            result = overview(connection)
        elif args.action == 'topics':
            result = overview(connection, args.version_id, args.start, args.end)
        elif args.action == 'contributions':
            result = topic_contributions(connection, args.version_id, args.topic,
                                         args.start, args.end)
        elif args.action == 'pending':
            result = {'version_id': args.version_id,
                      'unassigned_contents': pending_contents(connection, args.version_id)}
        elif args.action == 'version-info':
            result = version_info(connection, args.version_id)
        elif args.action == 'versions':
            result = topic_versions(connection)
        elif args.action == 'publish':
            result = publish_classification(connection, args.release, args.version_id,
                                            args.start, args.end)
        elif args.action == 'read-mart':
            result = read_classification(connection, args.release)
        else:
            result = classification_releases(connection, args.version_id)
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
