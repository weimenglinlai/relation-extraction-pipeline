# Relation Extraction Pipeline

Text parsing, relation extraction and quality evaluation utilities.

## Structure

```
part1/README.md
part1/config.py
part1/crawler.py
part1/gui.py
part1/main.py
part1/parser.py
part1/pipeline.py
part1/quality.py
part1/requirements.txt
part1/utils.py
part1/visualize.py
part1/人物列表.txt
part2/DuIE2.0/duie_schema/duie_schema.json
part2/README.md
part2/build_dataset.py
part2/casrel_model.py
part2/config.py
part2/data/relation2idx.json
part2/data/thresholds.json
part2/dataset.py
part2/docs/Neo4j接入说明.md
part2/docs/属性图Schema.md
part2/evaluate.py
part2/extract.py
part2/gui.py
part2/main.py
part2/make_schema_doc.py
part2/palette.py
part2/prepare_duie.py
part2/requirements.txt
part2/rules.py
part2/start_neo4j.py
part2/store.py
part2/train.py
part2/tune.py
part2/utils.py
part2/viz.py
part2/viz_report.py
part2/weak_label.py
```

## Setup

Install dependencies from `requirements.txt` when present:

```bash
pip install -r requirements.txt
```

Data files, model weights and generated outputs are not included; place them under the paths referenced by the configuration before running.
