from datetime import date
from decimal import Decimal, InvalidOperation

from .checks import number, text

SHARE_UNIT = Decimal('0.000000000000000001')


def normalize(table, original):
    row = dict(original)
    nullable = {('topics', 'parent_id'), ('contents', 'primary_topic')}
    for field, value in row.items():
        if (table, field) in nullable and value == '':
            continue
        if field == 'share':
            continue
        if field != 'copies':
            text(value)
    if table == 'distribution':
        row['day'] = date.fromisoformat(row['day']).isoformat()
        if isinstance(row['copies'], bool) or not isinstance(row['copies'], int) or row['copies'] < 0:
            raise ValueError('发行数量无效')
    if table == 'content_topic_shares':
        share = Decimal(number(row['share']))
        if share <= 0 or share > 1:
            raise ValueError('份额必须在零到一之间')
        try:
            if share != share.quantize(SHARE_UNIT):
                raise ValueError('份额最多十八位小数')
        except InvalidOperation:
            raise ValueError('份额最多十八位小数') from None
        row['share'] = format(share.normalize(), 'f')
    return row
