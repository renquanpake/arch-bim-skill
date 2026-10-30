#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rules_accessible.py — 无障碍 / 疏散 / 楼梯 / 防火分区 规范规则库
================================================================

arch-bim-skill 规范审查阶段的规则数据源。每条规则带：id、名称、类别、严重级别、
规范来源（GB 50016 / GB 50352 / PMR / ADA / IBC）、检查函数。

审查输入（ComplianceContext）不绑定具体数据结构：既可由普通 brief 构建，
也可由 planner 布局结果构建，保证「生成 → 规范审查 → 问题回改」闭环在
两条产线（口述 brief 直转 / intent 确定性布局）上都可运行。

规则阈值（P0 派发要求 + 基础项）：
  门净通行宽 ≥ 830mm；门扇标称宽 ≥ 900mm；无障碍回转圆 Ø ≥ 1500mm；
  单向走廊净宽 ≥ 1200mm；双向走廊净宽 ≥ 1400mm；尽端走廊 ≤ 12m；
  疏散行走距离 ≤ 30m；楼梯 riser 150~220mm、run ≥ 220mm、净宽按类型；
  防火分区面积（GB 50016-2014 3.3.1：一级耐火多层 ≤ 2500m²、地下室 ≤ 500m²）。

用法：
    ctx = ComplianceContext(unit="mm", rooms=..., doors=..., ...)
    violations = check(ctx)          # -> list[Violation]
    for v in violations: print(v)

依赖：Python 3.8+，仅标准库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# 常量：阈值（全部为规范阈值，单位见命名后缀）
# ---------------------------------------------------------------------------
DOOR_CLEAR_MIN_MM = 830          # 门净通行宽最小值（PMR 2015 / ADA 2010 604.2 / GB 50763）
DOOR_LEAF_MIN_MM = 900           # 门扇标称宽最小值（同一来源；净宽=扇宽-门框/门缝损耗）
TURNING_CIRCLE_MM = 1500         # 无障碍厕所/前室回转圆直径
CORRIDOR_ONE_WAY_MIN_MM = 1200   # 单向走廊净宽
CORRIDOR_TWO_WAY_MIN_MM = 1400   # 双向走廊净宽
DEAD_END_MAX_M = 12.0            # 尽端走廊最大长度（IBC 1018.4 / GB 50016 附录）
EVACUATION_MAX_M = 30.0          # 房间最远点至安全出口疏散距离（IBC 1017.2 / GB 50016 5.5.17）

STAIR_RISE_MIN_MM = 150          # 踏步高度下限（舒适值，参考 ai-architect-agent stairs.py MIN_RISE）
STAIR_RISE_TARGET_MM = 190       # 目标踏步高度
STAIR_RISE_MAX_MM = 220          # 踏步高度上限（MAX_RISE）
STAIR_RUN_MIN_MM = 220           # 踏面深度下限（MIN_RUN；住宅舒适 250 / 商业 280）
STAIR_WIDTH_RES_MM = 900         # 住宅楼梯净宽下限（GB 50352-2019 6.8.2）
STAIR_WIDTH_COM_MM = 1100        # 公共建筑楼梯净宽下限

FIRE_COMPARTMENT_GROUND_MAX_M2 = 2500.0   # 一级耐火等级多层民用建筑防火分区上限（GB 50016-2014 表3.3.1）
FIRE_COMPARTMENT_UP_MAX_M2 = 1500.0       # 高层民用建筑防火分区上限（同一表）
FIRE_COMPARTMENT_UNDERGROUND_MAX_M2 = 500.0  # 地下/半地下室防火分区上限（同一表）

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

CAT_ACCESSIBLE = "无障碍"
CAT_EVACUATION = "疏散"
CAT_STAIR = "楼梯"
CAT_FIRE = "防火分区"


@dataclass(frozen=True)
class Rule:
    """一条规范规则。check(ctx) -> list[Violation]。"""
    id: str
    name: str
    category: str
    severity: str              # error / warning
    source: str                # 规范来源
    check: Callable[["ComplianceContext"], List["Violation"]]


@dataclass
class Violation:
    """一条违规记录。fix_hint 为可反馈回改 brief 的建议文本。"""
    rule_id: str
    rule_name: str
    category: str
    severity: str
    message: str
    source: str
    ref: str = ""              # 涉及的构件名
    fix_hint: str = ""         # 回改建议


@dataclass
class ComplianceContext:
    """审查视图：把 brief / 布局结果归一为规则可检查的结构。

    普通 brief 模式与 planner 布局模式都需填充该视图。
    unit: "mm" 或 "m"（所有长度字段统一按此单位解释）。
    """
    unit: str = "mm"
    rooms: List[dict] = field(default_factory=list)          # {name,type,outline|width|depth}
    doors: List[dict] = field(default_factory=list)          # {name,width(净宽),leafWidth(扇宽,可选)}
    corridors: List[dict] = field(default_factory=list)      # {name,width,length,deadEnd,oneWay}
    stairs: List[dict] = field(default_factory=list)         # {name,width,rise,run}
    fire_compartments: List[dict] = field(default_factory=list)  # {name,area_m2,level}
    building_floors: int = 1
    building_height_m: float = 0.0

    def to_m(self, v: float) -> float:
        return v / 1000.0 if self.unit == "mm" else v


# ---------------------------------------------------------------------------
# 规则实现
# ---------------------------------------------------------------------------

def _rule_door_clear(ctx: ComplianceContext) -> List[Violation]:
    """门净通行宽 ≥ 830mm。"""
    out = []
    for d in ctx.doors:
        w = ctx.to_m(d.get("width", 0.0)) * 1000.0
        if w and w < DOOR_CLEAR_MIN_MM:
            out.append(Violation(
                "door_clear_min", "门净通行宽 ≥ %dmm" % DOOR_CLEAR_MIN_MM,
                CAT_ACCESSIBLE, "error",
                "门 %s 净宽 %.0fmm 小于规范下限 %dmm" % (d.get("name", "?"), w, DOOR_CLEAR_MIN_MM),
                rule_source("door_clear_min"), d.get("name", ""),
                "加宽该门至 ≥ %dmm（含门框损耗，扇宽建议 ≥ %dmm）" % (DOOR_CLEAR_MIN_MM, DOOR_LEAF_MIN_MM)))
    return out


def _rule_door_leaf(ctx: ComplianceContext) -> List[Violation]:
    """门扇标称宽 ≥ 900mm。"""
    out = []
    for d in ctx.doors:
        leaf = ctx.to_m(d.get("leafWidth", 0.0)) * 1000.0
        if leaf and leaf < DOOR_LEAF_MIN_MM:
            out.append(Violation(
                "door_leaf_min", "门扇标称宽 ≥ %dmm" % DOOR_LEAF_MIN_MM,
                CAT_ACCESSIBLE, "warning",
                "门 %s 扇宽 %.0fmm 小于 %dmm，通行净宽可能不足" % (d.get("name", "?"), leaf, DOOR_LEAF_MIN_MM),
                rule_source("door_leaf_min"), d.get("name", ""),
                "选用扇宽 ≥ %dmm 的门" % DOOR_LEAF_MIN_MM))
    return out


def _rule_turning_circle(ctx: ComplianceContext) -> List[Violation]:
    """无障碍厕所/前室需容纳 Ø1500mm 回转圆。"""
    out = []
    for r in ctx.rooms:
        if r.get("type") not in ("wc", "toilet", "accessible_toilet", "ensuite", "bathroom", "shower_room"):
            continue
        w = ctx.to_m(r.get("width", 0.0)) * 1000.0
        d = ctx.to_m(r.get("depth", 0.0)) * 1000.0
        if w or d:
            if min(w, d) < TURNING_CIRCLE_MM:
                out.append(Violation(
                    "turning_circle", "无障碍回转圆 Ø ≥ %dmm" % TURNING_CIRCLE_MM,
                    CAT_ACCESSIBLE, "error",
                    "无障碍房间 %s 短边 %.0fmm 无法容纳 Ø%dmm 回转圆（需短边与长边均 ≥ Ø）"
                    % (r.get("name", "?"), min(w, d), TURNING_CIRCLE_MM),
                    rule_source("turning_circle"), r.get("name", ""),
                    "扩大该房间短边与长边至 ≥ %dmm" % TURNING_CIRCLE_MM))
    return out


def _rule_corridor_one_way(ctx: ComplianceContext) -> List[Violation]:
    """单向走廊净宽 ≥ 1200mm。"""
    out = []
    for c in ctx.corridors:
        if not c.get("oneWay"):
            continue
        w = ctx.to_m(c.get("width", 0.0)) * 1000.0
        if w and w < CORRIDOR_ONE_WAY_MIN_MM:
            out.append(Violation(
                "corridor_one_way", "单向走廊净宽 ≥ %dmm" % CORRIDOR_ONE_WAY_MIN_MM,
                CAT_ACCESSIBLE, "error",
                "单向走廊 %s 净宽 %.0fmm 小于 %dmm" % (c.get("name", "?"), w, CORRIDOR_ONE_WAY_MIN_MM),
                rule_source("corridor_one_way"), c.get("name", ""),
                "走廊加宽至 ≥ %dmm 或改为双向通行并加宽至 ≥ %dmm"
                % (CORRIDOR_ONE_WAY_MIN_MM, CORRIDOR_TWO_WAY_MIN_MM)))
    return out


def _rule_corridor_two_way(ctx: ComplianceContext) -> List[Violation]:
    """双向走廊净宽 ≥ 1400mm。"""
    out = []
    for c in ctx.corridors:
        if c.get("oneWay"):
            continue
        w = ctx.to_m(c.get("width", 0.0)) * 1000.0
        if w and w < CORRIDOR_TWO_WAY_MIN_MM:
            out.append(Violation(
                "corridor_two_way", "双向走廊净宽 ≥ %dmm" % CORRIDOR_TWO_WAY_MIN_MM,
                CAT_ACCESSIBLE, "error",
                "双向走廊 %s 净宽 %.0fmm 小于 %dmm" % (c.get("name", "?"), w, CORRIDOR_TWO_WAY_MIN_MM),
                rule_source("corridor_two_way"), c.get("name", ""),
                "走廊加宽至 ≥ %dmm" % CORRIDOR_TWO_WAY_MIN_MM))
    return out


def _rule_dead_end(ctx: ComplianceContext) -> List[Violation]:
    """尽端走廊 ≤ 12m。"""
    out = []
    for c in ctx.corridors:
        if not c.get("deadEnd"):
            continue
        length = ctx.to_m(c.get("length", 0.0))
        if length and length > DEAD_END_MAX_M:
            out.append(Violation(
                "dead_end_max", "尽端走廊 ≤ %gm" % DEAD_END_MAX_M,
                CAT_EVACUATION, "error",
                "尽端走廊 %s 长 %.1fm 超过 %gm 上限" % (c.get("name", "?"), length, DEAD_END_MAX_M),
                rule_source("dead_end_max"), c.get("name", ""),
                "缩短尽端走廊至 ≤ %gm，或在尽端增设疏散出口/楼梯" % DEAD_END_MAX_M))
    return out


def _rule_evacuation_distance(ctx: ComplianceContext) -> List[Violation]:
    """疏散行走距离 ≤ 30m（房间最远点经走廊至安全出口）。"""
    out = []
    # 走廊为线型通道：从出口端到尽端的长度即疏散路径主体；房间最远点到走廊按房间深度估。
    for c in ctx.corridors:
        length = ctx.to_m(c.get("length", 0.0))
        if not length:
            continue
        if length > EVACUATION_MAX_M:
            out.append(Violation(
                "evacuation_max", "疏散距离 ≤ %gm" % EVACUATION_MAX_M,
                CAT_EVACUATION, "error",
                "走廊 %s 疏散路径长 %.1fm 超过 %gm 上限" % (c.get("name", "?"), length, EVACUATION_MAX_M),
                rule_source("evacuation_max"), c.get("name", ""),
                "增设第二疏散出口/楼梯，或调整房间进深压缩疏散路径至 ≤ %gm" % EVACUATION_MAX_M))
    return out


def _rule_stair_dimension(ctx: ComplianceContext) -> List[Violation]:
    """楼梯踏步：rise 150~220mm、run ≥ 220mm；净宽按建筑类型。"""
    out = []
    for s in ctx.stairs:
        rise = ctx.to_m(s.get("rise", 0.0)) * 1000.0
        run = ctx.to_m(s.get("run", 0.0)) * 1000.0
        w = ctx.to_m(s.get("width", 0.0)) * 1000.0
        name = s.get("name", "楼梯")
        if rise and not (STAIR_RISE_MIN_MM <= rise <= STAIR_RISE_MAX_MM):
            out.append(Violation(
                "stair_rise", "踏步高度 %d~%dmm" % (STAIR_RISE_MIN_MM, STAIR_RISE_MAX_MM),
                CAT_STAIR, "error",
                "楼梯 %s 踏步高 %.0fmm 超出 %d~%dmm（目标 %.0fmm）"
                % (name, rise, STAIR_RISE_MIN_MM, STAIR_RISE_MAX_MM, STAIR_RISE_TARGET_MM),
                rule_source("stair_rise"), name,
                "调整踏步数，使 rise=层高/踏步数 落在 %d~%dmm" % (STAIR_RISE_MIN_MM, STAIR_RISE_MAX_MM)))
        if run and run < STAIR_RUN_MIN_MM:
            out.append(Violation(
                "stair_run", "踏面深度 ≥ %dmm" % STAIR_RUN_MIN_MM,
                CAT_STAIR, "error",
                "楼梯 %s 踏面 %.0fmm 小于 %dmm" % (name, run, STAIR_RUN_MIN_MM),
                rule_source("stair_run"), name,
                "加大 run 至 ≥ %dmm（住宅舒适 250 / 商业 280）" % STAIR_RUN_MIN_MM))
        if w:
            min_w = STAIR_WIDTH_RES_MM if s.get("buildingType") in ("residential", "apartment") else STAIR_WIDTH_COM_MM
            if w < min_w:
                out.append(Violation(
                    "stair_width", "楼梯净宽 ≥ %dmm" % min_w,
                    CAT_STAIR, "warning",
                    "楼梯 %s 净宽 %.0fmm 小于 %dmm" % (name, w, min_w),
                    rule_source("stair_width"), name,
                    "楼梯加宽至 ≥ %dmm" % min_w))
    return out


def _rule_fire_compartment(ctx: ComplianceContext) -> List[Violation]:
    """防火分区面积上限（GB 50016-2014 表 3.3.1，基础项）。"""
    out = []
    for fc in ctx.fire_compartments:
        area = fc.get("area_m2", 0.0)
        level = fc.get("level", "ground")
        if not area:
            continue
        limit = {
            "ground": FIRE_COMPARTMENT_GROUND_MAX_M2,
            "up": FIRE_COMPARTMENT_UP_MAX_M2,
            "underground": FIRE_COMPARTMENT_UNDERGROUND_MAX_M2,
        }.get(level, FIRE_COMPARTMENT_GROUND_MAX_M2)
        if area > limit:
            out.append(Violation(
                "fire_compartment_area", "防火分区面积 ≤ %.0fm²" % limit,
                CAT_FIRE, "error",
                "防火分区 %s 面积 %.0fm² 超过 %s 上限 %.0fm²"
                % (fc.get("name", "?"), area, {"ground": "地上", "up": "高层", "underground": "地下"}.get(level, "地上"), limit),
                rule_source("fire_compartment_area"), fc.get("name", ""),
                "增设防火墙/防火卷帘分隔防火分区，使每分区 ≤ %.0fm²" % limit))
    return out


# ---------------------------------------------------------------------------
# 规则注册表（顺序即报告排序）
# ---------------------------------------------------------------------------
RULES: List[Rule] = [
    Rule("door_clear_min", "门净通行宽 ≥ 830mm", CAT_ACCESSIBLE, "error",
         "PMR 2015 / ADA 2010 §604 / GB 50763-2012", _rule_door_clear),
    Rule("door_leaf_min", "门扇标称宽 ≥ 900mm", CAT_ACCESSIBLE, "warning",
         "PMR 2015 / ADA 2010 §604 / GB 50763-2012", _rule_door_leaf),
    Rule("turning_circle", "无障碍回转圆 Ø1500mm", CAT_ACCESSIBLE, "error",
         "PMR 2015 / ADA 2010 §603 / GB 50763-2012", _rule_turning_circle),
    Rule("corridor_one_way", "单向走廊净宽 ≥ 1200mm", CAT_ACCESSIBLE, "error",
         "IBC 2018 §1020 / GB 50352-2019 §6.8", _rule_corridor_one_way),
    Rule("corridor_two_way", "双向走廊净宽 ≥ 1400mm", CAT_ACCESSIBLE, "error",
         "IBC 2018 §1020 / GB 50352-2019 §6.8", _rule_corridor_two_way),
    Rule("dead_end_max", "尽端走廊 ≤ 12m", CAT_EVACUATION, "error",
         "IBC 2018 §1018.4 / GB 50016-2014 §5.5.17", _rule_dead_end),
    Rule("evacuation_max", "疏散距离 ≤ 30m", CAT_EVACUATION, "error",
         "IBC 2018 §1017 / GB 50016-2014 §5.5.17", _rule_evacuation_distance),
    Rule("stair_dimension", "楼梯踏步/净宽基础项", CAT_STAIR, "error",
         "GB 50352-2019 §6.8 / GB 50016-2014 §6.4", _rule_stair_dimension),
    Rule("fire_compartment_area", "防火分区面积上限", CAT_FIRE, "error",
         "GB 50016-2014 表3.3.1", _rule_fire_compartment),
]


def rule_source(rule_id: str) -> str:
    for r in RULES:
        if r.id == rule_id:
            return r.source
    # 子规则（由同一规则函数产出）回落到父规则来源
    _sub = {
        "stair_rise": "stair_dimension",
        "stair_run": "stair_dimension",
        "stair_width": "stair_dimension",
    }
    parent = _sub.get(rule_id)
    if parent:
        for r in RULES:
            if r.id == parent:
                return r.source
    return ""




def check(ctx: ComplianceContext) -> List[Violation]:
    """按注册表顺序执行全部规则，返回违规清单（空=合规通过）。"""
    out: List[Violation] = []
    for r in RULES:
        try:
            out.extend(r.check(ctx))
        except Exception as e:  # 单规则失败不中断整体审查
            out.append(Violation(r.id, r.name, r.category, "warning",
                                 "规则执行异常：%s" % e, r.source, "", ""))
    return out


def summarize(violations: List[Violation]) -> dict:
    """生成审查摘要（供 CLI / 报告用）。"""
    by_sev = {}
    by_cat = {}
    for v in violations:
        by_sev[v.severity] = by_sev.get(v.severity, 0) + 1
        by_cat[v.category] = by_cat.get(v.category, 0) + 1
    return {
        "total": len(violations),
        "errors": by_sev.get("error", 0),
        "warnings": by_sev.get("warning", 0),
        "byCategory": by_cat,
        "passed": len(violations) == 0,
    }


def to_dict(violations: List[Violation]) -> list:
    return [{
        "ruleId": v.rule_id, "ruleName": v.rule_name, "category": v.category,
        "severity": v.severity, "message": v.message, "source": v.source,
        "ref": v.ref, "fixHint": v.fix_hint,
    } for v in violations]


# ---------------------------------------------------------------------------
# 便捷构建器：由 planner 布局结果 / 普通 brief 生成 ComplianceContext
# ---------------------------------------------------------------------------

def context_from_brief(brief: dict) -> ComplianceContext:
    """由普通 brief 构建审查视图。

    走廊数据若 brief 未显式给出（口述直转模式无布局结构），
    返回的 corridors 为空，走廊类规则自动跳过；门宽由 doors 与
    walls[].openings 中的 door 洞宽汇总，楼梯由 stairs 段读取。
    """
    unit = (brief.get("project", {}).get("unit") or "mm").lower()
    ctx = ComplianceContext(unit=unit)
    rooms = brief.get("rooms", [])
    ctx.rooms = [{
        "name": r.get("name", "?"),
        "type": (r.get("type") or "").lower(),
        "outline": r.get("outline", []),
    } for r in rooms]
    # 房间宽/深（由 outline 计算，供回转圆规则）
    for r in ctx.rooms:
        xs = [p[0] for p in r.get("outline", [])]
        ys = [p[1] for p in r.get("outline", [])]
        if xs:
            r["width"] = max(xs) - min(xs)
            r["depth"] = max(ys) - min(ys)

    # 门：独立 doors + 墙 openings 中的 door 洞
    doors = []
    for d in brief.get("doors", []):
        doors.append({"name": d.get("name", d.get("id", "门")),
                      "width": d.get("width", 0.0),
                      "leafWidth": d.get("leafWidth", 0.0)})
    for w in brief.get("walls", []):
        for o in w.get("openings", []):
            if o.get("type") == "door":
                doors.append({"name": "%s@%s" % (w.get("id", w.get("name", "墙")), o.get("startX", 0)),
                              "width": o.get("width", 0.0)})
    ctx.doors = doors

    # 楼梯（若 brief 携带 stairs 段）
    for s in brief.get("stairs", []):
        ctx.stairs.append({"name": s.get("name", "楼梯"),
                           "width": s.get("width", 0.0),
                           "rise": s.get("rise", 0.0),
                           "run": s.get("run", 0.0),
                           "buildingType": brief.get("project", {}).get("buildingType", "commercial")})

    # 防火分区：以单层外包面积 + 楼层数近似（基础项）
    xs, ys = [], []
    for r in ctx.rooms:
        for x, y in r.get("outline", []):
            xs.append(x), ys.append(y)
    if xs:
        area_m2 = (max(xs) - min(xs)) * (max(ys) - min(ys)) / 1e6
        level = "underground" if brief.get("project", {}).get("undergroundFloors") else "ground"
        ctx.fire_compartments.append({"name": brief.get("project", {}).get("name", "主分区"),
                                      "area_m2": area_m2, "level": level})
    ctx.building_floors = int(brief.get("project", {}).get("floors", 1))
    ctx.building_height_m = ctx.to_m(brief.get("project", {}).get("floorHeight", 0.0)) * ctx.building_floors
    return ctx


def context_from_layout(layout: dict, project: dict) -> ComplianceContext:
    """由 planner 布局结果构建审查视图（走廊/楼梯/房间尺寸均来自确定性布局）。"""
    unit = (project.get("unit") or "mm").lower()
    ctx = ComplianceContext(unit=unit)
    s = 1000.0 if unit == "mm" else 1.0
    for r in layout.get("rooms", []):
        rect = r.get("rect", [0, 0, 0, 0])
        x0, y0, x1, y1 = rect
        ctx.rooms.append({
            "name": r.get("name", "?"), "type": (r.get("type") or "").lower(),
            "width": (x1 - x0) * s, "depth": (y1 - y0) * s,
        })
    for d in layout.get("doors", []):
        ctx.doors.append({"name": d.get("name", "门"),
                          "width": d.get("width", 0.0) * s,
                          "leafWidth": d.get("leafWidth", 0.0) * s})
    for c in layout.get("corridors", []):
        ctx.corridors.append({
            "name": c.get("name", "走廊"),
            "width": c.get("width", 0.0) * s,
            "length": c.get("length", 0.0) * s,
            "deadEnd": c.get("deadEnd", False),
            "oneWay": c.get("oneWay", False),
        })
    for st in layout.get("stairs", []):
        ctx.stairs.append({"name": st.get("name", "楼梯"),
                           "width": st.get("width", 0.0) * s,
                           "rise": st.get("rise", 0.0) * s,
                           "run": st.get("run", 0.0) * s,
                           "buildingType": project.get("buildingType", "commercial")})
    for fc in layout.get("fireCompartments", []):
        ctx.fire_compartments.append(fc)
    ctx.building_floors = int(project.get("floors", 1))
    return ctx
