# 文化内容多专题分类事实仓

这是用于本地数据接收、事实核对和列式交换的仓库服务。数据使用 DuckDB 持久化，导出为 Parquet；不需要远程数据库或运行期网络。示例数据仅为本地合成的业务记录。

## 业务口径

仓库同时保留两种分类口径：

- **旧单主分类**：`topics` 是单父树，`contents.primary_topic` 只有一个主分类。`warehouse.cli overview` 与 `warehouse.queries.overview(connection)` 继续按这个口径取数，未分类内容进入 `unassigned`。
- **版本化多专题分类**：分类结构、多父关系和内容份额分别保存在 `classification_versions`、`classification_topics`、`classification_parents`、`content_topic_shares`。同一版本内，一份内容可把份额分给多个叶子专题；同一叶子可以同时挂在多个上级下。

版本化口径的规则：

- 份额只允许分配到叶子专题，且同一内容在同一版本内的份额合计必须为一。不守恒的分配、缺失节点、结构环路和重复路径都由 `warehouse.classification` 在正式取数前拒绝。
- 汇总按**贡献份额**计算：叶子份额沿所有可达上级上卷。上级共用的下级路径或多条可达路径用可达节点集合表达，因此同一份额只计一次。
- 汇总同时输出**去重内容数**：一份内容在多个叶子下都有份额时，上级的内容数只算一次，但份额贡献全部保留。两种口径分别对应 `copies` 与 `contents` 字段。
- 未分配份额的内容明确进入 `unassigned`（待分类）范围，并可通过 `pending_contents` 列出。
- 每条汇总和贡献行都带 `version_id`；`version_report` 一次返回版本身份、汇总和从上级到内容/叶子/份额的贡献明细，不需要人工拼接多份专题结果。

已发布的月度集市带独立的分类版本身份和事实期间。`marts.publish_classification` 冻结发布时的分类解释，`marts.read_classification` 按集市版本读取；同一事实期间要按新口径取数时，发布新的集市版本，而不是覆盖历史。已被集市引用的分类版本不允许通过新批次改写。旧接口 `marts.publish_topics` / `read_topics` 保持原有行为。

## 使用

运行时支持 Python 3.11.x，独立容器使用摘要固定的 Python 3.11 镜像。`requirements.txt` 精确锁定依赖版本，`requirements.lock` 保存相同版本的 Linux CPython 3.11 wheel 哈希，容器构建时校验哈希。推荐使用下列容器方式；镜像构建时准备依赖，之后运行不联网。镜像只包含本仓库应用及依赖，不启动额外服务。

```bash
docker build -t taxonomy-warehouse .
docker run --rm --network none taxonomy-warehouse
```

已经准备依赖的环境中也可以直接执行：

```bash
python -m unittest discover -s tests -v
python -m compileall -q warehouse tests
python -m warehouse.cli --database /tmp/taxonomy.duckdb load samples/delivery.json
python -m warehouse.cli --database /tmp/taxonomy.duckdb load samples/classification.json
python -m warehouse.cli --database /tmp/taxonomy.duckdb overview
python -m warehouse.cli --database /tmp/taxonomy.duckdb versions
python -m warehouse.cli --database /tmp/taxonomy.duckdb topics v2026-11 --start 2026-11-01 --end 2026-11-30
python -m warehouse.cli --database /tmp/taxonomy.duckdb contributions v2026-11 --topic root
python -m warehouse.cli --database /tmp/taxonomy.duckdb pending v2026-11
python -m warehouse.cli --database /tmp/taxonomy.duckdb version-info v2026-11
python -m warehouse.cli --database /tmp/taxonomy.duckdb publish m2026-11 v2026-11 --start 2026-11-01 --end 2026-11-30
python -m warehouse.cli --database /tmp/taxonomy.duckdb read-mart m2026-11
python -m warehouse.cli --database /tmp/taxonomy.duckdb export /tmp/taxonomy-exports release1
```

上面的导出版本不可覆盖；再次核对应使用新的数据库路径和新的导出目录，或直接运行使用临时目录的测试。测试同时验证接收事务、业务主键冲突、批号重送、持久化及列式文件回读。完整测试和样本运行通常数秒结束，没有等待外部服务的步骤。

## 数据与接口

构建所需工具和传递依赖同样精确锁定，并包含在容器依赖中。执行 `python -m build --wheel --no-isolation` 可在不下载额外构建依赖的情况下生成 wheel；SQL 表定义随包分发。

接收包包含 `batch_id` 和 `tables`。合法字段由各表契约与领域校验共同约束；同批号同内容重送不重复写入，同业务键异内容拒绝整个接收事务。已有原始行和接收回执保留，业务值更正须采用领域中允许的独立记录，不能覆写原行。原始接入不因分类结构暂时不完整而拒绝整批数据，但结构错误会让该版本无法进入正式取数和集市发布。

`warehouse.storage.open_store` 打开仓库，`warehouse.ingest.ingest` 接收业务包，`warehouse.queries` 提供事实查询：`overview`、`topic_contributions`、`topic_versions`、`version_info`、`pending_contents`。`warehouse.export.export_tables` 固定一次读取视图并生成版本目录，`read_export` 校验成员文件摘要后读取；导出清单同时记录分类版本和已发布集市身份。`samples/delivery.json` 与 `samples/classification.json` 展示两类口径的合法数据。

领域校验、事实计算与查询分别存在于同一应用的不同职责中。SQL 定义保存当前存储契约；批号回执与原始行登记属于接收事务，不是业务汇总来源。文件导出只交换现有业务表，接收回执不会充当事实行。
