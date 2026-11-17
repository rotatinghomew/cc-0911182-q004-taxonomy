import json
from .classification import classify
from .storage import canonical,digest,records


def publish_topics(connection,release):
    rows=classify(connection)
    source={table:records(connection,'SELECT * FROM '+table+' ORDER BY 1') for table in ['topics','contents','distribution']}
    signature=digest(source)
    prior=connection.execute('SELECT source_digest,rows_json FROM topic_marts WHERE release_id=?',[release]).fetchone()
    if prior:
        if prior[0]!=signature:
            raise ValueError('已有集市版本不允许被新数据覆盖')
        return json.loads(prior[1])
    connection.execute('INSERT INTO topic_marts VALUES (?,?,?)',[release,signature,canonical(rows)])
    return rows


def read_topics(connection,release):
    row=connection.execute('SELECT rows_json FROM topic_marts WHERE release_id=?',[release]).fetchone()
    if row is None:
        raise ValueError('专题集市版本不存在')
    return json.loads(row[0])
