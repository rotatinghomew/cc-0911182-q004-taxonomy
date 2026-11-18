TABLES = {
    # 兼容原有单主分类数据：topics 仍是一条下级到一条上级的简单树。
    'topics': (['topic_id', 'parent_id', 'label'], ['topic_id']),
    'contents': (['content_id', 'title', 'primary_topic'], ['content_id']),
    'distribution': (['fact_id', 'day', 'content_id', 'copies'], ['fact_id']),
    # 版本化分类口径：允许一个上级拥有多条下级路径，也允许一份内容进入多个叶子。
    'classification_versions': (['version_id', 'label'], ['version_id']),
    'classification_topics': (['version_id', 'topic_id', 'label'], ['version_id', 'topic_id']),
    'classification_parents': (['version_id', 'topic_id', 'parent_id'],
                               ['version_id', 'topic_id', 'parent_id']),
    'content_topic_shares': (['version_id', 'content_id', 'topic_id', 'share'],
                             ['version_id', 'content_id', 'topic_id']),
}

# 分类关系一旦用于发布集市就冻结，后续批次不能静默改写该口径。
CLASSIFICATION_TABLES = ('classification_versions', 'classification_topics',
                         'classification_parents', 'content_topic_shares')
