# 项目一：人物信息抓取

对百度百科的特定领域人物词条进行抓取，解析出「基本信息 / 演艺经历 / 主要作品 / 获奖记录」，
保存为 JSON，并提供 GUI 界面、数据质量评估与可视化图表。

## 一、目录结构

```
项目一/
├─ config.py          全局配置（路径、并发、间隔、重试、字段定义）——改参数只看这里
├─ utils.py           公共工具（日志、文本清洗、JSON 读写、URL 拼接、代理处理）
├─ crawler.py         爬虫：Selenium / requests 双引擎、UA 轮换、重试、断点续爬
├─ parser.py          解析：基本信息、演艺经历、主要作品、获奖记录 -> 结构化字段
├─ pipeline.py        流水线：抓取 -> 解析 -> 落盘 -> 汇总
├─ quality.py         数据质量评估（完整率 / 准确率 / 重复率 / 失败原因分类）
├─ visualize.py       可视化（5 张灰度图）
├─ gui.py             Tkinter 图形界面
├─ main.py            命令行入口
├─ requirements.txt   依赖清单
├─ 人物列表.txt        待抓取人名（学习通下载）
├─ data/
│  ├─ persons.jsonl   逐条追加（断点续爬的唯一事实来源）
│  └─ persons.json    去重汇总后的交付数据
├─ logs/
│  ├─ crawl.log       运行日志
│  ├─ crawl_index.jsonl 抓取元数据（不含正文，用于时间趋势图）
│  ├─ failed.jsonl    失败记录（含失败原因）
│  └─ progress.json   进度快照
└─ output/
   ├─ quality_report.json 质量评估（机器可读）
   ├─ 质量评估报告.md       质量评估（人读）
   ├─ 准确率抽查表.csv      人工抽查 100 条
   ├─ crawl_summary.json  抓取汇总
   ├─ gui_config.json     GUI 设置（自动生成）
   ├─ gui_history.json    GUI 历史记录（自动生成）
   ├─ screenshots/        界面效果图
   └─ charts/*.png        图表
```

## 二、环境准备

要求 Python 3.8+（本工程在 3.13 下开发）。

```bash
pip install -r requirements.txt
```

Selenium 引擎需要本机已安装 Chrome 浏览器（无需手动配置 chromedriver，
Selenium 4.6+ 会自动下载匹配的驱动）。

## 三、快速开始

```bash
# 1) 小样本试跑（先抓 20 个，确认环境无误）
python main.py crawl --limit 20

# 2) 全量抓取（可随时 Ctrl+C，重跑同一条命令自动从断点继续）
python main.py crawl --engine selenium --workers 4

# 3) 汇总 + 质量评估 + 出图
python main.py evaluate
python main.py charts

# 4) 启动 GUI
python main.py gui
```

其它命令：

```bash
python main.py status        # 查看抓取进度与失败原因分布
python main.py one 刘德华     # 抓单人并在终端打印提取结果
python main.py all           # 抓取 + 汇总 + 评估 + 出图，一条龙
```

## 四、爬虫思路

百度百科对纯 HTTP 客户端（requests）会返回「百度安全验证」页，拿不到正文。因此：

- **selenium 引擎（默认）**：无头 Chrome 打开桌面版 `baike.baidu.com`，等 React 渲染出
  `J-lemma-content` 再取 `page_source`，字段最全，与参考文档截图一致。
- **requests 引擎**：直接请求移动版 `wapbaike.baidu.com`（服务端渲染，移动 UA 可直取正文），
  轻快，字段名略有差异，解析器已统一映射。

反爬与稳定性措施：

- 请求头 User-Agent 轮换（`config.USER_AGENTS`）；
- 每抓一页随机 sleep（`SLEEP_MIN ~ SLEEP_MAX`）；
- 失败指数退避重试（`MAX_RETRIES` / `RETRY_BACKOFF`）；
- 断点续爬：已完成的写入 `data/persons.jsonl`，重跑自动跳过；
- 多线程：`ThreadPoolExecutor`，每线程独立浏览器实例，每 300 页回收一次防内存泄漏；
- 代理规避：默认清空 `HTTP_PROXY` 等环境变量并给浏览器加 `--no-proxy-server`
  （否则本机请求会被甩给系统代理，表现为「网页打不开」）。

处理三类特殊页面：

| 情况 | 判定特征 | 处理 |
| --- | --- | --- |
| 词条不存在 | 「百度百科尚未收录」 | 记 `not_found`，不重试 |
| 多义词（消歧义） | 「本词条是一个多义词」 | 自动跟到第一个义项继续解析 |
| 被反爬拦截 | 「百度安全验证」 | 记 `blocked`，重试 |

## 五、提取思路

解析器对「桌面版 / 移动版」两套 DOM 都做了兼容，核心是**语义属性优先、类名前缀兜底**：

| 目标 | 桌面版 | 移动版 |
| --- | --- | --- |
| 章节标题 | `div[data-tag=header][data-name][data-level]` | `div[data-tag=header][data-title]` |
| 正文段落 | `div[data-tag=paragraph]` | 同左 |
| 信息模块 | `div[data-tag=module][data-module-type]` | 同左 |
| 基本信息 | `dl > div.itemWrapper > dt/dd` | `div.cardItem > cardName/cardValue` |

要点：

1. **基本信息**：按 `dt/dd` 配对，字段名归一化（去对齐全角空格）后经 `FIELD_ALIASES`
   映射到标准字段（本名、外文名、别名、国籍、民族、出生地、出生日期、毕业院校、职业、
   代表作品、主要成就）。没有「基本信息」标题的网页，取正文开头到目录之前的部分兜底。
2. **章节树**：用**栈**按标题层级归位（同级并列、下级嵌套），避免二级标题被错误地挂到
   上一个二级标题下（这会让「主要作品」只剩一个子类）。
3. **主要作品**：按子章节分类，表格取「表头 + 数据行」，无表格的卡片流按 `dl` 取条目。
4. **获奖记录**：百度百科用「外层包装表套内层明细表」，先跳过含嵌套表的包装表，
   再从包装表的单格行里按顺序取出分组标签（影视类 / 音乐类 / 荣誉称号…）配到各明细表。
5. **清洗**：去掉 `[1]` 式引注角标、零宽字符、重复空白、图片与脚本节点。

## 六、保存方式

- `data/persons.jsonl`：抓一条解析一条、追加一行（内存友好，崩溃不丢已完成的），
  是断点续爬与汇总的唯一事实来源。
- `data/persons.json`：按去重后的数组，每条记录字段如下：

```jsonc
{
  "name": "刘德华",              // 列表里的人名
  "lemma_name": "刘德华",        // 词条实际标题（可能被重定向）
  "url": "https://baike.baidu.com/item/刘德华",
  "crawl_time": "2026-10-12 17:20:33",
  "engine": "selenium",
  "summary": "…词条摘要…",
  "basic_info": { "本名": "…", "外文名": "…", "国籍": "…", "…": "…" },
  "basic_info_raw": { "原始字段名": "取值" },     // 未映射前的原样保留
  "career": "…演艺经历全文…",
  "works": [ { "category": "参演电影", "header": [...], "items": [[...]],
               "cards": [{...}], "count": 178 } ],
  "awards": [ { "group": "影视类", "header": [...], "items": [[...]] } ],
  "sections": [ { "name": "演艺经历", "level": 1, "subs": [...],
                  "text": "…", "table_count": 0 } ],
  "raw_text": "…正文纯文本（供项目二做三元组抽取）…",
  "stats": { "section_count": 7, "work_categories": 12, "award_groups": 5 }
}
```

`raw_text` 与 `sections` 是给**项目二（知识图谱构建）**预留的：前者可直接送入关系抽取，
后者的章节结构便于按章节定位三元组来源。

## 七、GUI 功能

`python main.py gui`

界面为浅色中性配色，六个标签页，左栏固定放历史记录与章节大纲，底部状态栏实时显示进度与统计。
在高分屏（125%/150% 缩放）上会自动声明 DPI 感知，文字为原生清晰渲染而不是被系统放大糊掉。

| 功能 | 说明 |
| --- | --- |
| 输入 URL | 支持完整百度百科 URL（非百科域名会弹窗拦截），也支持直接写人名自动拼 URL；输入框带灰字提示 |
| 展示网页 HTML | 「网页 HTML 源码」标签页显示原始源码（超长按展示上限截断并提示） |
| 展示提取文本 | 「提取文本」汇总各块；「章节正文」逐章节铺开全文；「结构化结果 JSON」；「主要作品 / 获奖记录」按表展示 |
| 章节大纲 | 左栏列出章节树，**双击即跳到「章节正文」里对应位置并高亮** |
| 导出结果 | 「文件 - 导出结果」另存为 JSON / TXT；也可「复制提取文本」直接进剪贴板 |
| 批量输入 | 每行一个人名/URL，可设线程数与引擎，进度条显示真实百分比 |
| 多线程 | 批量抓取走线程池，界面不假死 |
| 历史记录 | 持久化到 `output/gui_history.json`，双击载入、Delete 删除、可清空 |
| 一键出报告 | 「出报告/图表」按钮直接刷新质量报告与图表（等价于 evaluate + charts） |
| 运行日志 | 「运行日志」标签页记录每一步操作与失败原因 |

### 参数设置（设置 - 参数设置 / Ctrl+,）

| 分组 | 可调项 |
| --- | --- |
| 显示 | 界面字号、正文字号、源码展示上限、是否自动换行 |
| 抓取参数 | 抓取引擎、并发线程数、最小/最大请求间隔、单页重试次数、单页超时、是否归档 HTML、是否忽略系统代理 |
| 其它 | 抓取后自动导出单条 JSON、历史记录上限 |

设置写入 `output/gui_config.json`，下次启动自动加载；「设置 - 恢复默认设置」可一键还原。

### 字号调整

三种方式，实时生效并自动保存：

- 菜单「设置 - 字号增大 / 字号减小」；
- 快捷键 `Ctrl+=` / `Ctrl+-`，或按住 `Ctrl` 滚动鼠标滚轮；
- 「参数设置」里分别指定界面字号与正文字号。

### 快捷键

| 快捷键 | 作用 |
| --- | --- |
| `Ctrl+Enter` | 抓取并提取（在输入框里回车同样触发） |
| `Ctrl+S` | 导出结果 |
| `Ctrl+,` | 打开参数设置 |
| `F5` | 质量评估 + 出图 |
| `Ctrl+=` / `Ctrl+-` | 字号增大 / 减小 |
| `Ctrl+滚轮` | 缩放字号 |

文本框支持右键菜单（复制 / 全选 / 清空）。

`output/screenshots/` 下附了四张界面效果图，可直接用于报告。

## 八、数据质量评估

`python main.py evaluate` -> `output/质量评估报告.md`

- **字段完整率**：逐字段非空比例，报告里标注是否达到 85% 要求；
- **准确率**：生成 `准确率抽查表.csv`（100 条，含全部字段与「查验结论」列）供人工核验；
  另附一套**格式规则自动校验**（出生日期是否像日期、外文名是否含拉丁字母等）作为参考基线；
- **重复率**： / URL / 词条名 / 内容指纹四种口径；
- **失败原因分类**：`logs/failed.jsonl` 按状态归类。

填好抽查表后重新执行 `evaluate`，报告会自动计算人工准确率（「部分正确」按 0.5 计）。

## 九、可视化

`python main.py charts` -> `output/charts/`

1. `01_字段完整率.png` —— 各字段完整率条形图，带 85% 达标线；
2. `02_领域分布.png` —— 人物领域分布饼图；
3. `03_抓取时间趋势.png` —— 抓取数量随时间变化折线图（附累计曲线）；
4. `04_失败原因.png` —— 失败原因分类条形图；
5. `05_字段完整率热力图.png` —— 前 30 条记录 × 各字段的完整率热力图。

配色遵循**灰度**（深色=有值，浅色=缺失），未达标项用斜纹高亮。

## 十、加分项与调参

已实现：反爬（UA 轮换 + 频率控制）、断点续爬、多线程、导出、批量、进度条、历史记录。

调参集中在 `config.py`：

| 参数 | 含义 |
| --- | --- |
| `DEFAULT_ENGINE` | `selenium`（字段全、慢）/ `requests`（快、走移动版） |
| `DEFAULT_WORKERS` | 并发线程数，每线程一个浏览器实例，4~6 较稳 |
| `SLEEP_MIN` / `SLEEP_MAX` | 每页请求间隔（秒） |
| `MAX_RETRIES` / `RETRY_BACKOFF` | 重试次数与退避基数 |
| `SAVE_HTML` | 是否归档原始 HTML（gzip，2000 页桌面版约 8GB，默认关） |
| `STANDARD_FIELDS` / `FIELD_ALIASES` | 标准字段与字段名别名映射 |

对比实践（单线程 vs 多线程、requests vs selenium）可用 `--engine` / `--workers` 组合实测，
例如 `python main.py crawl --limit 30 --workers 1` 与 `--workers 4` 对比耗时。

## 十一、注意事项

- 抓取请控制频率，仅用于学习，不要对目标站点造成压力。
- 若某段时间百度风控收紧（大量 `blocked`），把 `SLEEP_MIN/SLEEP_MAX` 调大、`DEFAULT_WORKERS` 调到 2 再跑。
- 修改 `parser.py` 的抽取规则后，若 `SAVE_HTML=True` 可用 `pipeline.reparse()` 离线重解析，
  无需重新联网抓取。
