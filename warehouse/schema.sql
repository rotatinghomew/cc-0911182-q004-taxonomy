CREATE TABLE IF NOT EXISTS receipts(batch_id VARCHAR PRIMARY KEY, signature VARCHAR NOT NULL, inserted INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS source_rows(table_name VARCHAR, row_key VARCHAR, payload VARCHAR NOT NULL, batch_id VARCHAR NOT NULL, PRIMARY KEY(table_name,row_key));
        CREATE TABLE IF NOT EXISTS topics(topic_id VARCHAR PRIMARY KEY, parent_id VARCHAR, label VARCHAR);
        CREATE TABLE IF NOT EXISTS contents(content_id VARCHAR PRIMARY KEY, title VARCHAR, primary_topic VARCHAR);
        CREATE TABLE IF NOT EXISTS distribution(fact_id VARCHAR PRIMARY KEY, day VARCHAR, content_id VARCHAR, copies BIGINT CHECK(copies>=0));

CREATE TABLE IF NOT EXISTS topic_marts(release_id VARCHAR PRIMARY KEY, source_digest VARCHAR, rows_json VARCHAR);