from .storage import records


def ancestors(connection,topic):
    mapping={row['topic_id']:row['parent_id'] for row in records(connection,'SELECT * FROM topics')}
    result=[]
    current=topic
    while current:
        if current in result:
            raise ValueError('分类存在环路')
        if current not in mapping:
            raise ValueError('分类节点不存在')
        result.append(current)
        current=mapping[current]
    return result


def classify(connection):
    totals={}
    members={}
    for row in records(connection,'SELECT d.*,c.primary_topic FROM distribution d LEFT JOIN contents c USING(content_id) ORDER BY fact_id'):
        topic=row['primary_topic']
        targets=ancestors(connection,topic) if topic else ['unassigned']
        for target in targets:
            totals[target]=totals.get(target,0)+row['copies']
            members.setdefault(target,set()).add(row['content_id'])
    return [{'topic_id':topic,'copies':totals[topic],'contents':len(members[topic])} for topic in sorted(totals)]
