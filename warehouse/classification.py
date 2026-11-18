"""分类口径校验、份额汇总与追溯。

旧的单主分类字段保留在 ``topics`` / ``contents`` 中，``version_id`` 为空时
按旧口径取数。新的分类口径按 ``version_id`` 隔离：专题允许有多个上级，内容
可以按份额分配到多个叶子专题。所有正式取数都会先校验分类结构。
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from .storage import records

UNASSIGNED = 'unassigned'
ONE = Decimal(1)
_SHARE_UNIT = Decimal('0.000000000000000001')


def _decimal(value):
    """把数据库数值整理成稳定、无多余尾零的 Decimal。"""
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError('数量格式错误') from None
    if not result.is_finite():
        raise ValueError('数量格式错误')
    if result == 0:
        return Decimal(0)
    return Decimal(format(result.normalize(), 'f'))


def _check_period(start, end):
    for value in (start, end):
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError('日期格式错误')
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError('日期格式错误') from None
    if start is not None and end is not None and start > end:
        raise ValueError('日期范围无效')


def _facts(connection, start=None, end=None):
    _check_period(start, end)
    clauses = []
    parameters = []
    if start is not None:
        clauses.append('day >= ?')
        parameters.append(start)
    if end is not None:
        clauses.append('day <= ?')
        parameters.append(end)
    where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
    return records(connection,
                   'SELECT fact_id,day,content_id,copies FROM distribution' + where + ' ORDER BY fact_id',
                   parameters)


def _version_model(connection, version_id):
    """读取并校验一个分类版本；任何结构错误都在这里阻止正式取数。"""
    if not isinstance(version_id, str) or not version_id:
        raise ValueError('分类版本不能为空')
    version = connection.execute(
        'SELECT version_id,label FROM classification_versions WHERE version_id=?',
        [version_id]).fetchone()
    if version is None:
        raise ValueError('分类版本不存在')

    topics = {
        row['topic_id']: row['label']
        for row in records(connection,
                           'SELECT topic_id,label FROM classification_topics '
                           'WHERE version_id=? ORDER BY topic_id', [version_id])
    }
    if UNASSIGNED in topics:
        raise ValueError('待分类保留标识不能用作专题')
    parents = {topic: set() for topic in topics}
    children = {topic: set() for topic in topics}
    for row in records(connection,
                       'SELECT topic_id,parent_id FROM classification_parents '
                       'WHERE version_id=? ORDER BY topic_id,parent_id', [version_id]):
        topic = row['topic_id']
        parent = row['parent_id']
        if topic not in topics or parent not in topics:
            raise ValueError('分类节点不存在')
        parents[topic].add(parent)
        children[parent].add(topic)

    # 显式检测环路，避免只在遍历时才暴露，也避免同名路径掩盖错误。
    state = {}
    for start_topic in topics:
        if state.get(start_topic) == 2:
            continue
        stack = [(start_topic, False)]
        while stack:
            topic, leaving = stack.pop()
            if leaving:
                state[topic] = 2
                continue
            mark = state.get(topic, 0)
            if mark == 1:
                raise ValueError('分类存在环路')
            if mark == 2:
                continue
            state[topic] = 1
            stack.append((topic, True))
            for parent in parents[topic]:
                if state.get(parent) == 1:
                    raise ValueError('分类存在环路')
                if state.get(parent) != 2:
                    stack.append((parent, False))

    # 内容表与发行事实中出现的标识都视为已知内容；未分配的内容进入待分类范围。
    registered_contents = {
        row['content_id'] for row in records(connection, 'SELECT content_id FROM contents')
    }
    known_contents = set(registered_contents)
    known_contents.update(
        row['content_id'] for row in records(connection, 'SELECT DISTINCT content_id FROM distribution'))
    shares_by_content = {}
    totals = {}
    for row in records(connection,
                       'SELECT content_id,topic_id,share FROM content_topic_shares '
                       'WHERE version_id=? ORDER BY content_id,topic_id', [version_id]):
        content = row['content_id']
        topic = row['topic_id']
        if content not in registered_contents:
            raise ValueError('分类内容不存在')
        if topic not in topics:
            raise ValueError('分类节点不存在')
        if children[topic]:
            raise ValueError('份额必须分配到叶子专题')
        share = _decimal(row['share'])
        # DECIMAL 精度以内的份额，避免浮点误差；超出精度视为非法数据。
        try:
            exact = share == share.quantize(_SHARE_UNIT)
        except InvalidOperation:
            exact = False
        if share <= 0 or share > ONE or not exact:
            raise ValueError('份额必须在零到一之间且最多十八位小数')
        shares_by_content.setdefault(content, []).append((topic, share))
        totals[content] = totals.get(content, Decimal(0)) + share

    for content, total in totals.items():
        if total != ONE:
            raise ValueError('内容份额合计不等于一：' + content)

    leaves = sorted(topic for topic in topics if not children[topic])
    # 一个叶子的全部可达上级（含自身）：用集合表达，多条路径/共用路径只算一次。
    leaf_ancestors = {}
    for leaf in leaves:
        reached = set()
        stack = [leaf]
        while stack:
            topic = stack.pop()
            if topic in reached:
                continue
            reached.add(topic)
            stack.extend(parents[topic])
        leaf_ancestors[leaf] = reached

    return {
        'version_id': version[0],
        'label': version[1],
        'topics': topics,
        'parents': parents,
        'children': children,
        'leaves': leaves,
        'leaf_ancestors': leaf_ancestors,
        'shares_by_content': shares_by_content,
        'unassigned_contents': sorted(known_contents - set(shares_by_content)),
    }


def ancestors(connection, topic, version_id=None):
    """返回专题自身及全部可达上级。

    未指定版本时沿用旧的单父链语义；指定版本时按多父结构返回去重后的集合。
    """
    if version_id is None:
        mapping = {row['topic_id']: row['parent_id']
                   for row in records(connection, 'SELECT * FROM topics')}
        result = []
        current = topic
        while current:
            if current in result:
                raise ValueError('分类存在环路')
            if current not in mapping:
                raise ValueError('分类节点不存在')
            result.append(current)
            current = mapping[current]
        return result
    model = _version_model(connection, version_id)
    if topic not in model['topics']:
        raise ValueError('分类节点不存在')
    reached = set()
    stack = [topic]
    while stack:
        current = stack.pop()
        if current in reached:
            continue
        reached.add(current)
        stack.extend(model['parents'][current])
    return [topic] + sorted(reached - {topic})


def _aggregate(model, facts):
    """按叶子份额汇总到所有上级，并保留可追溯的内容贡献明细。"""
    totals = {topic: Decimal(0) for topic in model['topics']}
    totals[UNASSIGNED] = Decimal(0)
    members = {topic: set() for topic in model['topics']}
    members[UNASSIGNED] = set()
    detail = {}
    shares = model['shares_by_content']
    share_lookup = {
        content: dict(assignments) for content, assignments in shares.items()
    }

    for fact in facts:
        content = fact['content_id']
        copies = Decimal(str(fact['copies']))
        assignments = shares.get(content)
        if assignments:
            for leaf, share in assignments:
                amount = copies * share
                for topic in model['leaf_ancestors'][leaf]:
                    totals[topic] += amount
                    members[topic].add(content)
                    key = (topic, content, leaf)
                    detail[key] = detail.get(key, Decimal(0)) + amount
        else:
            # 没有份额的内容进入待分类范围，而不是从汇总中消失。
            totals[UNASSIGNED] += copies
            members[UNASSIGNED].add(content)
            key = (UNASSIGNED, content, None)
            detail[key] = detail.get(key, Decimal(0)) + copies

    return totals, members, detail, share_lookup


def _topic_rows(model, totals, members):
    version_id = model['version_id']
    rows = [
        {'version_id': version_id, 'topic_id': topic,
         'label': model['topics'][topic],
         'copies': _decimal(totals[topic]), 'contents': len(members[topic])}
        for topic in sorted(model['topics'])
    ]
    rows.append({'version_id': version_id, 'topic_id': UNASSIGNED,
                 'label': '待分类',
                 'copies': _decimal(totals[UNASSIGNED]),
                 'contents': len(members[UNASSIGNED])})
    return rows


def _detail_rows(model, detail, share_lookup):
    version_id = model['version_id']
    rows = []
    for (topic, content, leaf), copies in detail.items():
        rows.append({
            'version_id': version_id,
            'topic_id': topic,
            'content_id': content,
            'leaf_topic_id': leaf,
            # 待分类内容没有分配份额，明确留空而不是伪造为全额。
            'share': None if leaf is None else _decimal(share_lookup[content][leaf]),
            'copies': _decimal(copies),
        })
    rows.sort(key=lambda row: (row['topic_id'], row['content_id'],
                               row['leaf_topic_id'] or ''))
    return rows


def classify(connection, version_id=None, start=None, end=None):
    """返回专题汇总。

    ``version_id`` 为空时保持旧单主分类汇总；指定版本时按份额与去重内容数
    两种口径汇总，并附带版本标识。
    """
    if version_id is None:
        primaries = {row['content_id']: row['primary_topic']
                     for row in records(connection, 'SELECT content_id,primary_topic FROM contents')}
        target_cache = {}
        totals = {}
        members = {}
        for row in _facts(connection, start, end):
            topic = primaries.get(row['content_id'])
            if topic not in target_cache:
                target_cache[topic] = ancestors(connection, topic) if topic else [UNASSIGNED]
            for target in target_cache[topic]:
                totals[target] = totals.get(target, 0) + row['copies']
                members.setdefault(target, set()).add(row['content_id'])
        return [{'topic_id': topic, 'copies': totals[topic],
                 'contents': len(members[topic])} for topic in sorted(totals)]

    model = _version_model(connection, version_id)
    totals, members, _, _ = _aggregate(model, _facts(connection, start, end))
    return _topic_rows(model, totals, members)


def contributions(connection, version_id, topic_id=None, start=None, end=None):
    """返回从上级专题到实际内容贡献的明细，份额会追溯到具体叶子专题。"""
    model = _version_model(connection, version_id)
    if topic_id is not None and topic_id != UNASSIGNED and topic_id not in model['topics']:
        raise ValueError('分类节点不存在')
    facts = _facts(connection, start, end)
    _, _, detail, share_lookup = _aggregate(model, facts)
    rows = _detail_rows(model, detail, share_lookup)
    if topic_id is not None:
        rows = [row for row in rows if row['topic_id'] == topic_id]
    return rows


def unassigned_contents(connection, version_id):
    """列出该分类版本下尚未分配份额的内容。"""
    return list(_version_model(connection, version_id)['unassigned_contents'])


def describe_version(connection, version_id):
    """说明当前查询使用的是哪版分类以及它是否已用于已发布集市。"""
    model = _version_model(connection, version_id)
    releases = [row[0] for row in connection.execute(
        'SELECT release_id FROM classification_marts WHERE version_id=? '
        'ORDER BY release_id', [version_id]).fetchall()]
    return {
        'version_id': model['version_id'],
        'label': model['label'],
        'topic_count': len(model['topics']),
        'leaf_count': len(model['leaves']),
        'assigned_content_count': len(model['shares_by_content']),
        'unassigned_contents': list(model['unassigned_contents']),
        'releases': releases,
    }


def versions(connection):
    return [{'version_id': row['version_id'], 'label': row['label']}
            for row in records(connection,
                               'SELECT version_id,label FROM classification_versions '
                               'ORDER BY version_id')]


def version_report(connection, version_id, start=None, end=None):
    """一次性返回版本身份、汇总和贡献明细，避免调用方自行拼接专题结果。"""
    model = _version_model(connection, version_id)
    totals, members, detail, share_lookup = _aggregate(
        model, _facts(connection, start, end))
    return {
        'version_id': model['version_id'],
        'version_label': model['label'],
        'start': start,
        'end': end,
        'topics': _topic_rows(model, totals, members),
        'contributions': _detail_rows(model, detail, share_lookup),
        'unassigned_contents': list(model['unassigned_contents']),
    }


def version_source(connection, version_id, start=None, end=None):
    """生成用于冻结集市的来源摘要视图。"""
    model = _version_model(connection, version_id)
    _check_period(start, end)
    return {
        'version': {'version_id': model['version_id'], 'label': model['label']},
        'topics': records(connection,
                          'SELECT topic_id,label FROM classification_topics '
                          'WHERE version_id=? ORDER BY topic_id', [version_id]),
        'parents': records(connection,
                           'SELECT topic_id,parent_id FROM classification_parents '
                           'WHERE version_id=? ORDER BY topic_id,parent_id', [version_id]),
        'shares': records(connection,
                          'SELECT content_id,topic_id,share FROM content_topic_shares '
                          'WHERE version_id=? ORDER BY content_id,topic_id', [version_id]),
        'contents': [row['content_id'] for row in records(
            connection, 'SELECT content_id FROM contents ORDER BY content_id')],
        'distribution': _facts(connection, start, end),
        'start': start,
        'end': end,
    }


def restore_report(report):
    """把集市 JSON 中按字符串保存的数值还原为 Decimal，保证读写同形。"""
    if not isinstance(report, dict):
        return report
    for row in report.get('topics', []):
        if 'copies' in row:
            row['copies'] = _decimal(row['copies'])
    for row in report.get('contributions', []):
        if row.get('share') is not None:
            row['share'] = _decimal(row['share'])
        row['copies'] = _decimal(row['copies'])
    return report
