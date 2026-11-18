CREATE TABLE IF NOT EXISTS receipts(batch_id VARCHAR PRIMARY KEY, signature VARCHAR NOT NULL, inserted INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_rows(table_name VARCHAR, row_key VARCHAR, payload VARCHAR NOT NULL, batch_id VARCHAR NOT NULL, PRIMARY KEY(table_name,row_key));
        CREATE TABLE IF NOT EXISTS topics(topic_id VARCHAR PRIMARY KEY, parent_id VARCHAR, label VARCHAR);
        CREATE TABLE IF NOT EXISTS contents(content_id VARCHAR PRIMARY KEY, title VARCHAR, primary_topic VARCHAR);
        CREATE TABLE IF NOT EXISTS distribution(fact_id VARCHAR PRIMARY KEY, day VARCHAR, content_id VARCHAR, copies BIGINT CHECK(copies>=0));

CREATE TABLE IF NOT EXISTS topic_marts(release_id VARCHAR PRIMARY KEY, source_digest VARCHAR, rows_json VARCHAR);

-- 分类口径按版本保存；同一事实期间可以在不同版本下分别取数，互不覆盖。
CREATE TABLE IF NOT EXISTS classification_versions(version_id VARCHAR PRIMARY KEY, label VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS classification_topics(
    version_id VARCHAR NOT NULL,
    topic_id VARCHAR NOT NULL,
    label VARCHAR,
    PRIMARY KEY(version_id, topic_id));
-- 一条记录表示 topic_id 有一个上级 parent_id；同一 topic_id 可有多条记录（多父/多路径）。
CREATE TABLE IF NOT EXISTS classification_parents(
    version_id VARCHAR NOT NULL,
    topic_id VARCHAR NOT NULL,
    parent_id VARCHAR NOT NULL,
    PRIMARY KEY(version_id, topic_id, parent_id));
-- share 是在一次分类口径下，内容分给某个叶子专题的贡献份额。
CREATE TABLE IF NOT EXISTS content_topic_shares(
    version_id VARCHAR NOT NULL,
    content_id VARCHAR NOT NULL,
    topic_id VARCHAR NOT NULL,
    share DECIMAL(38,18) NOT NULL CHECK(share > 0 AND share <= 1),
    PRIMARY KEY(version_id, content_id, topic_id));

CREATE TABLE IF NOT EXISTS classification_marts(
    release_id VARCHAR PRIMARY KEY,
    version_id VARCHAR NOT NULL,
    source_digest VARCHAR,
    rows_json VARCHAR NOT NULL);
