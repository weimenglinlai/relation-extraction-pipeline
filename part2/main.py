# -*- coding: utf-8 -*-
"""
项目二命令行入口。

常用：
    python main.py prepare          # DuIE2.0 转换 + 弱监督 + 划分数据集
    python main.py train            # 训练 CasRel
    python main.py eval             # 单独评估
    python main.py extract          # 对项目一全部词条抽三元组
    python main.py import           # 写入图数据库（本地 / Neo4j）
    python main.py viz              # 生成交互图谱 + 统计图
    python main.py gui              # 启动界面
    python main.py all              # 抽三元组 + 入库 + 出图（一次性）
"""
import argparse
import sys

import config as C
from utils import ensure_utf8_stdout, load_json


def cmd_prepare(a):
    import prepare_duie
    prepare_duie.main()
    import weak_label
    weak_label.main()
    import build_dataset
    build_dataset.main()
    return 0


def cmd_train(a):
    import train
    sys.argv = ["train.py", "--epochs", str(a.epochs), "--batch-size", str(a.batch_size)]
    if a.limit:
        sys.argv += ["--limit", str(a.limit)]
    return train.main()


def cmd_eval(a):
    import evaluate
    sys.argv = ["evaluate.py"] + (["--limit", str(a.limit)] if a.limit else [])
    evaluate.main()
    return 0


def cmd_extract(a):
    import extract
    extract.run(limit=a.limit, use_model=not a.no_model)
    return 0


def cmd_import(a):
    import store
    triples = load_json(C.TRIPLES_JSON, [])
    if not triples:
        print("没有三元组，请先 python main.py extract")
        return 1
    st = store.get_store()
    if st.backend == "neo4j":
        n = st.import_triples(triples, clear=a.clear)
        st.close()
    else:
        n = st.add_triples(triples, clear=a.clear)
        st.save()
    print(f"已写入 {n} 条关系到 {st.backend}")
    print("统计：", st.stats() if st.backend == "local" else "")
    return 0


def cmd_viz(a):
    import viz
    triples = load_json(C.TRIPLES_JSON, [])
    if not triples:
        print("没有三元组，请先 python main.py extract")
        return 1
    viz.save_pyvis(triples)
    viz.save_stats_png(triples)
    return 0


def cmd_gui(a):
    import gui
    gui.main()
    return 0


def cmd_all(a):
    cmd_extract(a)
    cmd_import(a)
    cmd_viz(a)
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="项目二：人物知识图谱构建")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("prepare", help="准备训练数据").set_defaults(func=cmd_prepare)

    sp = sub.add_parser("train", help="训练 CasRel")
    sp.add_argument("--epochs", type=int, default=C.EPOCHS)
    sp.add_argument("--batch-size", type=int, default=C.BATCH_SIZE)
    sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(func=cmd_train)

    sp = sub.add_parser("eval", help="评估模型")
    sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("extract", help="批量抽三元组")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--no-model", action="store_true")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("import", help="写入图数据库")
    sp.add_argument("--clear", action="store_true", help="先清空已有图")
    sp.set_defaults(func=cmd_import)

    sub.add_parser("viz", help="生成图谱可视化").set_defaults(func=cmd_viz)
    sub.add_parser("gui", help="启动 GUI").set_defaults(func=cmd_gui)

    sp = sub.add_parser("all", help="抽取 + 入库 + 出图")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--no-model", action="store_true")
    sp.add_argument("--clear", action="store_true")
    sp.set_defaults(func=cmd_all)
    return p


def main(argv=None):
    ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 0
    if not hasattr(args, "clear"):
        args.clear = False
    if not hasattr(args, "no_model"):
        args.no_model = False
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
