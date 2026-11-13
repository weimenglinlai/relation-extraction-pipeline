# -*- coding: utf-8 -*-
"""统一配色：实体类型 → 颜色。

Schema 图、统计图、交互网页、人物子图共用这一套，保证同一实体在所有图里颜色一致。
颜色都取中等偏深，白字压在上面可读。
"""

TYPE_COLORS = {
    "人物": "#3E6DA8",   # 深蓝（枢纽）
    "作品": "#D9762F",   # 橙
    "奖项": "#C9A227",   # 金
    "机构": "#4E9E6A",   # 绿
    "地点": "#8A63B0",   # 紫
    "职业": "#2E8B8B",   # 青
    "国家": "#B4483F",   # 砖红
    "民族": "#9C6B45",   # 棕
}

INK = "#1a1a1a"
GRID = "#e8e8e8"
EDGE = "#7a7a7a"
WHITE_TEXT = "#ffffff"
DEFAULT = "#6b7280"


def type_color(name, default=DEFAULT):
    return TYPE_COLORS.get(name, default)
