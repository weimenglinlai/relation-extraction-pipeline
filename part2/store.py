# -*- coding: utf-8 -*-
"""
图存储：双后端。

* Neo4jStore —— 配置了 config.NEO4J_URI 时使用官方 neo4j 驱动，支持任意 Cypher；
* LocalStore —— 未配置时把图存成 data/graph_local.json，并提供若干等价的内置查询。

节点 id 统一用 `类型:名称`，避免「人物：黎明」与「作品：黎明」撞车。
"""
import json
from collections import Counter, defaultdict

import config as C
from utils import dump_json, get_logger, load_json

log = get_logger()

LABEL2TYPE = {v: k for k, v in C.ENTITY_TYPES.items()}
REL2OBJ_TYPE = {r[0]: r[2] for r in C.RELATIONS}


def node_id(etype: str, name: str) -> str:
    return f"{etype}:{name}"


def edge_type(relation: str) -> str:
    return C.REL2TYPE.get(relation, "RELATED_TO")


# --------------------------------------------------------------------------- #
class LocalStore:
    """本地图存储：JSON 落盘 + 内存索引，够用于几万个三元组。"""

    backend = "local"

    def __init__(self, path=None):
        self.path = path or C.LOCAL_GRAPH
        self.nodes = {}       # id -> {id, type, label, name, props}
        self.edges = []       # {source, target, type, relation}
        self._out = defaultdict(list)
        self.load()

    # -- 读写 ---------------------------------------------------------- #
    def load(self):
        data = load_json(self.path, {}) or {}
        self.nodes = {n["id"]: n for n in data.get("nodes", [])}
        self.edges = data.get("edges", [])
        self._reindex()

    def _reindex(self):
        self._out = defaultdict(list)
        for e in self.edges:
            self._out[e["source"]].append(e)

    def save(self):
        dump_json({"nodes": sorted(self.nodes.values(), key=lambda n: n["id"]),
                   "edges": self.edges}, self.path)
        log.info("本地图已保存：%s（%d 节点 / %d 边）",
                 self.path.name, len(self.nodes), len(self.edges))

    # -- 写入 ---------------------------------------------------------- #
    def add_triples(self, triples, clear=False):
        if clear:
            self.nodes, self.edges = {}, []
            # 必须同时清掉索引：否则 _out 里残留上一张图的边，
            # 新图里同 (source,target,relation) 的边会被误判成重复而丢弃。
            self._out = defaultdict(list)
        seen = set()
        added = 0
        for t in triples:
            s, r, o = t["subject"], t["predicate"], t["object"]
            stype = "人物"
            otype = REL2OBJ_TYPE.get(r, "作品")
            sid, oid = node_id(stype, s), node_id(otype, o)
            for nid, etype, nm in ((sid, stype, s), (oid, otype, o)):
                if nid not in self.nodes:
                    self.nodes[nid] = {"id": nid, "type": etype,
                                       "label": C.ENTITY_TYPES.get(etype, "Entity"),
                                       "name": nm, "props": {}}
            key = (sid, oid, r)
            if key in seen:
                continue
            seen.add(key)
            self.edges.append({"source": sid, "target": oid,
                               "type": edge_type(r), "relation": r,
                               "confidence": t.get("confidence", 1.0),
                               "source_kind": t.get("source", "")})
            added += 1
        self._reindex()
        return added

    def set_node_props(self, etype, name, props: dict):
        nid = node_id(etype, name)
        if nid in self.nodes:
            self.nodes[nid]["props"].update({k: v for k, v in props.items() if v})

    # -- 查询 ---------------------------------------------------------- #
    def stats(self):
        node_by_type = Counter(n["type"] for n in self.nodes.values())
        edge_by_rel = Counter(e["relation"] for e in self.edges)
        return {
            "backend": self.backend,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "node_by_type": dict(node_by_type.most_common()),
            "edge_by_relation": dict(edge_by_rel.most_common()),
        }

    def neighbors(self, name: str, depth: int = 1, limit: int = 200):
        """以某个人物为中心取子图（GUI/可视化用）。"""
        start = [nid for nid, n in self.nodes.items()
                 if n["name"] == name and n["type"] == "人物"]
        if not start and name:
            start = [nid for nid, n in self.nodes.items() if name in n["name"]][:1]
        seen_nodes, seen_edges = set(start), []
        frontier = set(start)
        for _ in range(max(1, depth)):
            nxt = set()
            for nid in frontier:
                for e in self._out.get(nid, []):
                    seen_edges.append(e)
                    if e["target"] not in seen_nodes:
                        seen_nodes.add(e["target"])
                        nxt.add(e["target"])
                for e in self.edges:
                    if e["target"] == nid and e["source"] not in seen_nodes:
                        seen_nodes.add(e["source"])
                        nxt.add(e["source"])
                        seen_edges.append(e)
            frontier = nxt
            if len(seen_nodes) > limit:
                break
        return ([self.nodes[n] for n in seen_nodes if n in self.nodes],
                seen_edges[:limit * 4])

    def search(self, keyword: str, limit: int = 100):
        kw = (keyword or "").strip()
        if not kw:
            return []
        return [n for n in self.nodes.values() if kw in n["name"]][:limit]

    def preset_query(self, key: str, param: str = ""):
        """
        离线时的内置查询（对应常用 Cypher）。
        key: person_works | person_awards | top_nodes | path
        """
        if key == "person_works":
            rows = []
            for e in self.edges:
                if e["relation"] in ("参演", "执导", "编剧", "演唱", "主持") \
                        and (not param or self.nodes[e["source"]]["name"] == param):
                    rows.append([e["relation"], self.nodes[e["source"]]["name"],
                                 self.nodes[e["target"]]["name"]])
            return rows[:500]
        if key == "person_awards":
            rows = []
            for e in self.edges:
                if e["relation"] == "获得奖项" and (not param or self.nodes[e["source"]]["name"] == param):
                    rows.append([self.nodes[e["source"]]["name"],
                                 self.nodes[e["target"]]["name"]])
            return rows[:500]
        if key == "top_nodes":
            deg = Counter()
            for e in self.edges:
                deg[self.nodes[e["source"]]["name"]] += 1
                deg[self.nodes[e["target"]]["name"]] += 1
            return [[n, c] for n, c in deg.most_common(30)]
        return []


# --------------------------------------------------------------------------- #
class Neo4jStore:
    backend = "neo4j"

    def __init__(self, uri=None, user=None, password=None, database=None):
        from neo4j import GraphDatabase
        self.uri = uri or C.NEO4J_URI
        self.database = database or C.NEO4J_DATABASE
        self.driver = GraphDatabase.driver(
            self.uri, auth=(user or C.NEO4J_USER, password or C.NEO4J_PASSWORD),
            # 默认 30 秒，Neo4j 没启动时界面会卡很久，这里压到 3 秒快速失败
            connection_timeout=3, connection_acquisition_timeout=5)
        self.driver.verify_connectivity()

    def close(self):
        self.driver.close()

    def import_triples(self, triples, clear=False, batch=1000):
        rows = [{"s": t["subject"], "r": t["predicate"], "o": t["object"],
                 "st": "人物", "ot": REL2OBJ_TYPE.get(t["predicate"], "作品"),
                 "conf": t.get("confidence", 1.0)} for t in triples]
        with self.driver.session(database=self.database) as sess:
            if clear:
                sess.run("MATCH (n) DETACH DELETE n")
            # 每个标签都建唯一约束：MERGE 没有约束会退化成全标签扫描，几千条就明显变慢
            for label in C.ENTITY_TYPES.values():
                try:
                    sess.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:`{label}`) "
                             f"REQUIRE n.name IS UNIQUE")
                except Exception as e:
                    log.warning("创建约束 %s 失败：%s", label, e)
        # 关系类型不能参数化，按关系分组、用动态标签/关系名拼 Cypher
        by_rel = defaultdict(list)
        for r in rows:
            by_rel[r["r"]].append(r)
        total = 0
        with self.driver.session(database=self.database) as sess:
            for rel, items in by_rel.items():
                otype = C.ENTITY_TYPES.get(items[0]["ot"], "Entity")
                etype = C.REL2TYPE.get(rel, "RELATED_TO")
                cypher = (
                    "UNWIND $rows AS row "
                    "MERGE (s:Person {name: row.s}) "
                    f"MERGE (o:`{otype}` {{name: row.o}}) "
                    f"MERGE (s)-[e:`{etype}` {{relation: $rel}}]->(o) "
                    "SET e.confidence = row.conf"
                )
                for i in range(0, len(items), batch):
                    chunk = items[i:i + batch]
                    sess.run(cypher, rows=chunk, rel=rel)
                    total += len(chunk)
                log.info("导入关系 %s：%d 条", rel, len(items))
        return total

    def query(self, cypher: str, params: dict = None, limit: int = 500):
        """返回 {'keys': [...], 'rows': [[...]]}，keys 直接用 Cypher 里的别名当表头。"""
        with self.driver.session(database=self.database) as sess:
            res = sess.run(cypher, params or {})
            keys = list(res.keys())
            rows = [list(rec.values()) for rec in res][:limit]
        return {"keys": keys, "rows": rows}

    def stats(self):
        with self.driver.session(database=self.database) as sess:
            n = sess.run("MATCH (n) RETURN count(n)").single()[0]
            e = sess.run("MATCH ()-[r]->() RETURN count(r)").single()[0]
            nt = sess.run("MATCH (n) UNWIND labels(n) AS l "
                          "RETURN l AS label, count(*) AS c ORDER BY c DESC").data()
            er = sess.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c "
                          "ORDER BY c DESC").data()
        return {"backend": self.backend, "nodes": n, "edges": e,
                "node_by_type": {r["label"]: r["c"] for r in nt},
                "edge_by_relation": {r["t"]: r["c"] for r in er}}


def get_store():
    """按配置返回可用后端；Neo4j 连不上时自动回退本地并提示。"""
    if C.NEO4J_URI:
        try:
            store = Neo4jStore()
            log.info("使用 Neo4j：%s", C.NEO4J_URI)
            return store
        except Exception as e:
            log.warning("Neo4j 连接失败（%s），回退到本地图存储", e)
    return LocalStore()
