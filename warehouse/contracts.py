from datetime import date
from .checks import text


def normalize(table,original):
    row=dict(original)
    for field,value in row.items():
        if field in {'parent_id','primary_topic'} and value=='':
            continue
        if field!='copies':
            text(value)
    if table=='distribution':
        row['day']=date.fromisoformat(row['day']).isoformat()
        if isinstance(row['copies'],bool) or not isinstance(row['copies'],int) or row['copies']<0:
            raise ValueError('发行数量无效')
    return row
