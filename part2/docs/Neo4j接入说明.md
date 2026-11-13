# Neo4j 接入说明

本项目已接入真实图数据库并完成导入：**10,271 节点 / 13,335 关系**，Cypher 查询正常。
同时保留本地图存储作为兜底，没启动 Neo4j 时会自动回退，流程不会中断。

---

## 一、当前可用的实例

| 项 | 值 |
| --- | --- |
| 版本 | Neo4j Enterprise 5.3.0 |
| 目录 | `项目二\neo4j\neo4j-enterprise-5.3.0`（**在项目内部**，随项目一起拷走即可） |
| JDK | 本机已有的 `zulu17.38.21-ca-jdk17.0.5`（不在项目内，见下方说明） |
| Bolt | `bolt://localhost:7690` |
| Routing | `localhost:7691` |
| HTTP / Browser | `http://localhost:7476` |
| 账号 | `neo4j / kgproject123` |

配置已写进 `config.py`，所以 `python main.py gui` 打开就是 Neo4j 后端，Cypher 页可直接执行任意语句。

### 启动 / 停止

```bash
python start_neo4j.py          # 前台启动，Ctrl+C 停止
python start_neo4j.py --check  # 只测连接，打印图规模
```

### 为什么端口不是 7687

Neo4j Desktop 里也有一个 DBMS 在跑，占用了 7474 / 7687 / 7688。两个实例各用一套端口互不干扰：

| 端口 | 归属 |
| --- | --- |
| 7474 / 7687 / 7688 | Neo4j Desktop 里的 DBMS |
| 7476 / 7690 / 7691 | 本项目的实例 |

### 为什么不用 `bin\neo4j.bat`

这套 5.3 发行版的 `neo4j.bat` 只是转调 `neo4j.ps1`，而它在 PowerShell 5.1 下执行会报：

```
Invoke-Neo4jAdmin : 已添加了具有相同键的项
```

这是 Neo4j-Management 模块被重复导入的已知问题，与项目代码无关。
`start_neo4j.py` 直接调 java 主类 `org.neo4j.server.startup.Neo4jBoot`，绕开这个包装脚本：

```python
java -cp "../lib/*;../plugins/*" -Dfile.encoding=UTF-8 \
     org.neo4j.server.startup.Neo4jBoot console
```

两个必须注意的点（踩过）：

1. **必须在 `bin` 目录下执行**——`Neo4jBoot` 按「当前目录的上一级」推算 `NEO4J_HOME`，
   在别处执行会找不到 `conf` 并报 `NEO4J_CONF path doesn't exist`；
2. **企业版要接受许可**——启动前设置环境变量 `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`，
   否则只打印一段许可声明就退出。

---

## 二、导入逻辑

`store.Neo4jStore.import_triples()` 按关系类型分批 `UNWIND`：

```cypher
UNWIND $rows AS row
MERGE (s:Person {name: row.s})
MERGE (o:`作品` {name: row.o})
MERGE (s)-[e:`ACTED_IN` {relation: $rel}]->(o)
SET e.confidence = row.conf
```

- 节点用 `MERGE`，重复导入不产生重复节点；
- 导入前给 8 个标签都建 `name` 唯一约束——没有约束时 `MERGE` 会退化成全标签扫描，几千条就明显变慢；
- 关系类型不能用参数，所以按关系分组、把类型名拼进 Cypher（关系名来自 `config.REL2TYPE`，不是用户输入）；
- `--clear` 会先 `MATCH (n) DETACH DELETE n`。

```bash
python main.py import --clear   # 清空后重新导入
```

---

## 三、常用 Cypher

```cypher
// 1. 看整体规模
MATCH (n) RETURN count(n) AS 节点数;
MATCH ()-[r]->() RETURN count(r) AS 关系数;

// 2. 某个人物的全部关系
MATCH (p:Person {name:'英达'})-[r]->(o)
RETURN r.relation AS 关系, o.name AS 客体
ORDER BY 关系;

// 3. 某人参演/执导的作品
MATCH (p:Person {name:'英达'})-[r]->(o:Work)
WHERE r.relation IN ['参演','执导','编剧','演唱','主持']
RETURN 关系=r.relation, 作品=o.name;

// 4. 谁和谁合作
MATCH (a:Person)-[:COOPERATED_WITH]->(b:Person)
RETURN a.name AS 人物A, b.name AS 人物B LIMIT 50;

// 5. 度数最高的节点（图谱枢纽）
MATCH (n) RETURN n.name AS 节点, size((n)--()) AS 度数
ORDER BY 度数 DESC LIMIT 20;

// 6. 两跳路径
MATCH p=(a:Person {name:'英达'})-[*1..2]-(b)
RETURN p LIMIT 50;

// 7. 按关系统计
MATCH ()-[r]->() RETURN r.relation AS 关系, count(*) AS 数量
ORDER BY 数量 DESC;

// 8. 合作最多的搭档
MATCH (a:Person)-[:COOPERATED_WITH]->(b:Person)
RETURN a.name AS 人物, count(*) AS 合作数
ORDER BY 合作数 DESC LIMIT 10;
```

---

## 四、方式二：Neo4j Desktop

不想用命令行，也可以在已安装的 Neo4j Desktop 里建库：

1. **New Project** → **Add** → **Local DBMS**，版本 5.x，设置密码；
2. 点 **Start** 启动；
3. 在 DBMS 详情页看 Bolt 端口；
4. 把端口和密码填进 `config.py` 的 `NEO4J_URI` / `NEO4J_PASSWORD`。

Desktop 里的 DBMS 只在运行期间可连，关掉窗口服务就停了。

---

## 五、排错

| 现象 | 原因与处理 |
| --- | --- |
| `Unable to connect to localhost:7690` | 服务没启动：`python start_neo4j.py` |
| `NEO4J_CONF path doesn't exist` | 不在 `bin` 目录下执行，`start_neo4j.py` 已处理 |
| 启动后立刻退出、日志只有许可声明 | 没设 `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes` |
| `UnsupportedOperationException: set` | **JDK 版本不对**。Neo4j 5.3 只支持 Java 17，用 22 会在 `UnsafeUtil.initDirectByteBuffer` 崩。本机 `JAVA_HOME` 恰好指向 jdk-22，所以 `start_neo4j.py` 不盲信它，会逐个校验版本 |
| `Address already in use: bind` | 端口被占。改 `conf/neo4j.conf` 的 bolt/routing/backup 端口；注意**企业版还有一个 routing 连接器**，默认 7688 |
| `The client is unauthorized` | 密码不对，用 `python start_neo4j.py --admin dbms set-initial-password 新密码`（需先停库） |
| 连接失败但流程还能跑 | 正常：`store.get_store()` 会自动回退本地图存储并在日志告警 |

### JDK 是怎么选的

`start_neo4j.py` 按下面的顺序找，**每个候选都会先验版本，只认 17**：

1. 环境变量 `KG_JAVA_HOME`（应急，跳过校验）
2. 本机已有的 `zulu17.38.21-ca-jdk17.0.5`
3. 环境变量 `JAVA_HOME`（仅当它是 17）
4. `PATH` 里的 `java`（仅当它是 17）

> 为什么要这么麻烦：本机 `JAVA_HOME` 指向 `./data
> 直接用它跑 Neo4j 不会报「版本不支持」这种明确错误，而是抛
> `java.lang.UnsupportedOperationException: set`，堆栈指向 `UnsafeUtil`，
> 完全看不出是 JDK 的问题。
