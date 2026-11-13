# -*- coding: utf-8 -*-
"""
项目二：知识图谱构建 —— 全局配置。

包含路径、属性图 schema（关系 / 实体类型 / 属性）、模型与训练超参。
所有可调项集中在这里，改配置不用动业务代码。
"""
from pathlib import Path
import os

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUT_DIR = BASE_DIR / "output"
LOG_DIR = BASE_DIR / "logs"
MODEL_DIR = BASE_DIR / "models"
CKPT_DIR = MODEL_DIR / "rel_clf"
DOCS_DIR = BASE_DIR / "docs"

# 基座模型（从 项目一 所在机器上已有的 bert-base-chinese 复用，避免重复下载）
BERT_PATH = MODEL_DIR / "bert-base-chinese"

# 项目一 的产出（本项目的输入）
PROJ1_PERSONS = Path("project/学习//智能计算方向/项目一/data/persons.json")

# 中间与最终产物
PAIRS_TRAIN = DATA_DIR / "pairs_train.jsonl"
PAIRS_DEV = DATA_DIR / "pairs_dev.jsonl"
PAIRS_TEST = DATA_DIR / "pairs_test.jsonl"
HAND_TEST = DATA_DIR / "hand_test.jsonl"        # 人工核验集（手工标注）
GAZETTEER_JSON = DATA_DIR / "gazetteer.json"
TRIPLES_JSON = DATA_DIR / "triples.json"
TRIPLES_CSV = DATA_DIR / "triples.csv"
LOCAL_GRAPH = DATA_DIR / "graph_local.json"
METRICS_JSON = OUT_DIR / "metrics.json"
METRICS_MD = OUT_DIR / "模型评估报告.md"
VIZ_HTML = OUT_DIR / "graph.html"
VIZ_PNG = OUT_DIR / "graph_overview.png"
TRIPLE_STATS_MD = OUT_DIR / "三元组统计.md"

for _d in (DATA_DIR, OUT_DIR, LOG_DIR, MODEL_DIR, DOCS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# 属性图 schema
# --------------------------------------------------------------------------- #
# 实体类型（节点标签）
ENTITY_TYPES = {
    "人物": "Person",
    "作品": "Work",
    "奖项": "Award",
    "机构": "Organization",
    "地点": "Place",
    "国家": "Country",
    "民族": "Ethnicity",
    "职业": "Occupation",
}

# 关系定义：(关系名, Subject类型, Object类型, 说明, Neo4j 关系类型)
# 前 14 条是「节点-节点」关系，已满足参考文档「不少于 10 种」的要求。
RELATIONS = [
    ("出生地", "人物", "地点", "人物出生或长期生活的城市/地区", "BORN_IN"),
    ("籍贯", "人物", "地点", "人物的祖籍", "NATIVE_OF"),
    ("国籍", "人物", "国家", "人物所属国籍", "NATIONALITY"),
    ("民族", "人物", "民族", "人物所属民族", "ETHNICITY"),
    ("毕业院校", "人物", "机构", "人物就读或毕业的院校", "GRADUATED_FROM"),
    ("经纪公司", "人物", "机构", "签约的经纪/唱片公司", "SIGNED_WITH"),
    ("职业", "人物", "职业", "人物从事的职业", "OCCUPATION"),
    ("参演", "人物", "作品", "以演员身份出演的影视作品", "ACTED_IN"),
    ("执导", "人物", "作品", "以导演身份执导的作品", "DIRECTED"),
    ("编剧", "人物", "作品", "以编剧身份参与的作品", "WROTE"),
    ("演唱", "人物", "作品", "演唱的歌曲或专辑", "SANG"),
    ("主持", "人物", "作品", "主持的节目", "HOSTED"),
    ("获得奖项", "人物", "奖项", "获得或提名的奖项", "WON_AWARD"),
    ("合作", "人物", "人物", "与另一位人物在同一部作品中合作", "COOPERATED_WITH"),
]

RELATION_NAMES = [r[0] for r in RELATIONS]
RELATION_LABELS = ["NA"] + RELATION_NAMES       # 分类模型标签表，0 = 无关系
LABEL2ID = {n: i for i, n in enumerate(RELATION_LABELS)}
ID2LABEL = {i: n for n, i in LABEL2ID.items()}
REL2TYPE = {r[0]: r[4] for r in RELATIONS}      # 关系名 -> Neo4j 关系类型

# 节点-属性关系（同样计入 schema 的关系总数，用于报告里的 schema 表）
NODE_ATTRIBUTES = {
    "Person": [("出生日期", "Date"), ("性别", "String"), ("星座", "String"),
               ("血型", "String"), ("身高", "String"), ("体重", "String"),
               ("外文名", "String"), ("别名", "String"), ("粉丝名称", "String"),
               ("主要成就", "String"), ("代表作品", "String")],
    "Work": [("作品类型", "String"), ("年份", "String"), ("饰演角色", "String")],
    "Award": [("获奖年份", "String"), ("届次", "String"), ("获奖结果", "String")],
}

# 作品分类 -> 关系名的映射（用于弱监督标注与规则抽取）
WORK_CATEGORY_REL = [
    ("导演", "执导"),
    ("编剧", "编剧"),
    ("创作", "编剧"),
    ("演唱", "演唱"),
    ("音乐", "演唱"),
    ("单曲", "演唱"),
    ("专辑", "演唱"),
    ("综艺", "主持"),
    ("节目", "主持"),
    ("主持", "主持"),
    ("电影", "参演"),
    ("电视", "参演"),
    ("短片", "参演"),
    ("MV", "参演"),
    ("配音", "参演"),
    ("纪录片", "参演"),
    ("舞台剧", "参演"),
    ("话剧", "参演"),
]

# 作品标题常见的表头关键词（不同词条的列名不统一）
WORK_TITLE_HEADERS = ("作品名", "作品名称", "歌曲名称", "歌名", "剧名",
                      "节目名称", "专辑名称", "专辑名", "影片名", "片名", "名称")

# --------------------------------------------------------------------------- #
# 语料与标注
# --------------------------------------------------------------------------- #
MIN_SENT_LEN = 8          # 太短的句子直接丢弃
MAX_SENT_LEN = 220        # 太长（多半是拼接噪声）也丢弃
MAX_PAIRS_PER_PAGE = 160  # 单页最多产出多少个候选实体对，防止长词条淹没数据
NEG_RATIO = 3             # 负样本（NA）最多保留为正样本的几倍

# 训练/验证/测试按「人物」划分，避免同一词条的句子同时出现在训练和测试里
SPLIT_RATIO = (0.70, 0.15, 0.15)
SEED = 20261022

# --------------------------------------------------------------------------- #
# 模型与训练
# --------------------------------------------------------------------------- #
MAX_LEN = 192
BATCH_SIZE = 16
EPOCHS = 6
LR = 3e-5
WARMUP_RATIO = 0.1
DEVICE = "cuda"            # cuda / cpu，cuda 不可用时自动回退
DROPOUT = 0.1

# --------------------------------------------------------------------------- #
# 图数据库
# --------------------------------------------------------------------------- #
# 留空则使用本地图存储（data/graph_local.json）；填上则走 Neo4j。
# 本机已用「独立端口实例」跑通：bolt 7690（Neo4j Desktop 已占用 7474/7687/7688）。
# 也可以用环境变量临时覆盖（KG_NEO4J_URI / KG_NEO4J_USER / KG_NEO4J_PASSWORD）。
NEO4J_URI = os.environ.get("KG_NEO4J_URI", "bolt://localhost:7690")
NEO4J_USER = os.environ.get("KG_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("KG_NEO4J_PASSWORD", "kgproject123")
NEO4J_DATABASE = os.environ.get("KG_NEO4J_DATABASE", "neo4j")

# 抽取置信度阈值：规则命中与模型预测加权后的下限
MIN_CONFIDENCE = 0.45
