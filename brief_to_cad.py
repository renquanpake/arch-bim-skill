#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
brief_to_cad.py — 建筑描述（brief JSON）→ CAD JSON 指令（request.json）转换器
===============================================================================

arch-bim-skill 管线中的核心转换器：
    结构化建筑描述（brief.json）→ 对齐 CadJsonPlugin 协议的 request.json

功能：
  * 解析 brief（房间列表 / 墙段 / 门窗 / 柱梁 / 屋顶 / 层高 / 单位）
  * 单位换算：brief 默认 mm，插件协议为 m（AutoCAD 1 图形单位 = 1 米）
  * 生成 3D 建模指令：wall / floor / column / beam / roof / door / window
    - wall 由 start/end 推导 length + angle（绕 Z 轴旋转角）
    - wall.openings 携带门窗洞（type/startX/width/height/sill）——扩展插件消费
    - floor 由房间 outline 生成轮廓拉伸
    - roof 由屋顶类型生成（gable/shed/flat，扩展插件消费）
  * 生成 2D 平面指令（--plan）：俯视线框 polyline2d + 尺寸标注 text2d，带图层
  * 把最终 DWG 落盘路径写入 request["savePath"]

兼容性说明：
  现有 CadJsonPlugin 原生支持 wall/column/beam/floor/instance；
  openings / roof / door / window / polyline2d / text2d / layer 为扩展字段，
  旧插件会静默忽略未知键（不报错、不影响已支持构件），扩展插件消费这些字段。
  扩展方案详见 docs/plugin-extension.md。

用法：
  python brief_to_cad.py --brief brief.json [--request cad-request.json]
                         [--dwg D:/models/out.dwg] [--plan]
  python brief_to_cad.py --brief - --request cad-request.json   # 从 stdin 读 brief
  python brief_to_cad.py --env-check                           # 只检查外部路径

依赖：Python 3.8+，仅标准库。
"""

import argparse
import json
import math
import sys
import os
import tempfile

# Windows 默认 GBK 控制台无法输出 ✓/⚠ 等符号，降级替换而非抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# 同目录子包（compliance / planner）导入
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# 集中路径配置；并行落地中缺失时容忍，回退下方旧常量
try:
    import archbim_config as cfg
except ImportError:
    cfg = None

import preflight
import sheets
import elevations2
import schedules

# 标注符号包（docs/drawing-standards.md §5 标注包 v1）。缺失/被同名包遮蔽时
# 置 None，各集成点均有回退分支——缺依赖不崩、smoke 不红。
try:
    import annotations
    if not all(hasattr(annotations, fn) for fn in (
            "elev_symbol", "axis_bubble", "room_label",
            "door_window_code", "door_window_label", "section_mark")):
        annotations = None
except ImportError:
    annotations = None

from compliance import check as compliance_check
from compliance import context_from_brief, context_from_layout
from compliance import summarize as compliance_summarize
from compliance import to_dict as compliance_to_dict

# 插件轮询的请求文件路径（与 CadJsonPlugin.cs 常量一致）
DEFAULT_REQUEST = os.path.join(
    os.path.expanduser("~"), "Documents", "Marvis", "ai-modeling", "cad-request.json")
if cfg is not None:
    DEFAULT_REQUEST = os.path.join(
        cfg.resolve(cfg.get("paths.requestDir")) or os.path.dirname(DEFAULT_REQUEST),
        "cad-request.json")


# 图层约定（2D 平面）
LAYER_WALL = "A-WALL"
LAYER_DOOR = "A-DOOR"
LAYER_WINDOW = "A-WINDOW"
LAYER_COLUMN = "A-COLUMN"
LAYER_DIM = "A-DIM"
LAYER_TEXT = "A-TEXT"
LAYER_BEAM = "A-BEAM"
LAYER_ROOF = "A-ROOF"
LAYER_STAIR = "A-STAIR"

# 扩展能力标记（用于摘要提示哪些字段需要插件扩展）
EXTENDED_TYPES = {"roof", "door", "window", "polyline2d", "text2d", "box"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="brief JSON → CAD request.json 转换器（含 intent 布局与规范审查）")
    p.add_argument("--brief", default="",
                   help="结构化建筑描述文件路径；'-' 表示从 stdin 读取。与 --intent 二选一，至少其一")
    p.add_argument("--intent", default="",
                   help="P1 Spine-Column 意图文件路径（LLM 只出 intent，确定性代码完成布局，输出兼容 brief）")
    p.add_argument("--request", default=DEFAULT_REQUEST,
                   help="输出 request.json 路径（默认插件轮询路径）")
    p.add_argument("--dwg", default="",
                   help="DWG 落盘路径（写入 savePath）；缺省为配置 paths.dwgOutputDir，无配置时用系统临时目录")
    p.add_argument("--sheets", action="store_true",
                   help="生成施工图套图（图框+标题栏+真比例排版，输出纯 2D 图纸 DWG）")
    p.add_argument("--sheet-size", choices=("A1", "A2", "A3"), default="A1", help="图幅")
    p.add_argument("--sheet-scale", type=int, default=100, help="比例分母，如 100 表示 1:100")
    p.add_argument("--sheet-per", type=int, default=4, help="每张图纸放的视图数")
    p.add_argument("--area-schedule", default="", help="输出房间面积表 CSV（调 schedules.py）")
    p.add_argument("--quantity", default="", help="输出工程量快估 CSV（调 schedules.py）")
    p.add_argument("--schedule", default="",
                   help="输出门窗统计表 CSV 路径")
    p.add_argument("--elev-true", action="store_true",
                   help="真消隐立面（背面剔除+共享边去重，替代 AABB 示意投影）")
    p.add_argument("--elevations", action="store_true",
                   help="生成 4 个立面 + 1-1 剖面（由 3D 构件投影，示意级）")
    p.add_argument("--plan-only", action="store_true",
                   help="配合 --plan-sheet：只输出 2D 平面，不写 3D 构件")
    p.add_argument("--revit-sheets", action="store_true",
                   help="配合 --revit-drawings：建 Revit 原生图纸(ViewSheet+标题栏+Viewport)")
    p.add_argument("--revit-drawings", default="",
                   help="Revit 出图目录：建每层平面视图并导出 DWG")
    p.add_argument("--plan-sheet", action="store_true",
                   help="把每一层平面排成一张图（多楼层网格排版）")
    p.add_argument("--plan", action="store_true",
                   help="同时生成 2D 平面指令（俯视线框 + 图层标注）")
    p.add_argument("--check", action="store_true",
                   help="生成后执行规范合规审查（P0），输出违规问题清单")
    p.add_argument("--check-out", default="",
                   help="审查报告输出路径（缺省与 request 同目录 violations.json）")
    p.add_argument("--unit", choices=("mm", "m"), default="",
                   help="覆盖 brief 中的单位（默认读取 brief.project.unit，缺省 mm）")
    p.add_argument("--target", choices=("both", "cad", "revit"), default="both",
                   help="发单前校验的目标平台（容差不同）：both/cad/revit")
    p.add_argument("--validate", choices=("fix", "report", "off"), default="fix",
                   help="发单前几何校验：fix=自动修复（默认） report=只报告 off=跳过")
    p.add_argument("--allow-invalid", action="store_true",
                   help="校验不通过时仍然写出 request（默认拦截）")
    p.add_argument("--env-check", action="store_true",
                   help="只检查外部依赖路径（acad/accoreconsole/插件 DLL/Revit/请求目录/输出目录），不转换")
    return p.parse_args(argv)


ENV_CHECK_KEYS = (
    ("acadExe", "paths.acadExe"),
    ("accoreconsoleExe", "paths.accoreconsoleExe"),
    ("cadJsonDll", "paths.cadJsonDll"),
    ("revitExe", "paths.revitExe"),
    ("requestDir", "paths.requestDir"),
    ("dwgOutputDir", "paths.dwgOutputDir"),
)


def env_check():
    """--env-check：打印外部路径解析结果与存在性。返回 0 全绿 / 1 有缺失 / 2 cfg 缺失。"""
    if cfg is None:
        print("[brief_to_cad] 未找到 archbim_config 模块，集中配置未落地，无法执行环境检查。")
        return 2
    print("[brief_to_cad] 环境检查")
    out_dir = cfg.resolve(cfg.get("paths.dwgOutputDir") or "")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    for label, key in ENV_CHECK_KEYS:
        value = cfg.get(key)
        path = cfg.resolve(value) if value else ""
        state = "OK  " if path and os.path.exists(path) else "MISS"
        print("  %-18s %s  %s" % (label, state, path or "(未配置)"))
    missing = cfg.check([key for _, key in ENV_CHECK_KEYS])
    if missing:
        print("  缺失项 :")
        for item in missing:
            print("    - " + item)
        return 1
    print("  env OK")
    return 0


def load_brief(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def resolve_unit(brief, cli_unit):
    if cli_unit:
        return cli_unit
    return (brief.get("project", {}).get("unit") or "mm").lower()


def to_m(value, unit):
    """brief 单位（mm/m）→ 米。数值保持 float。"""
    v = float(value)
    return v / 1000.0 if unit == "mm" else v


def length_angle(start, end):
    """由两点推导 (length, angle)。angle 为绕 Z 轴旋转角（弧度），与插件约定一致。"""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    return math.hypot(dx, dy), math.atan2(dy, dx)


def build_wall_el(wall, unit):
    """墙段 → wall 元素（length/thickness/height/basePoint/angle + openings）。"""
    start = wall.get("start", [0, 0])
    end = wall.get("end", [6.0, 0.0])
    length, angle = length_angle(start, end)
    length = to_m(length, unit) if not wall.get("length") else to_m(wall["length"], unit)
    elevation = to_m(wall.get("elevation", 0.0), unit)
    el = {
        "type": "wall",
        "name": wall.get("id", wall.get("name", "W")),
        "length": round(length, 6),
        "thickness": round(to_m(wall.get("thickness", 0.2), unit), 6),
        "height": round(to_m(wall.get("height", 3.0), unit), 6),
        "basePoint": [round(start[0] / (1000.0 if unit == "mm" else 1.0), 6),
                      round(start[1] / (1000.0 if unit == "mm" else 1.0), 6),
                      round(elevation, 6)],
        "angle": round(angle, 6),
    }
    openings = wall.get("openings", [])
    if openings:
        el["openings"] = clamp_openings(openings, length, unit)
    if wall.get("layer"):
        el["layer"] = wall["layer"]
    return el


def clamp_openings(openings, wall_length_m, unit):
    """门窗洞夹紧到墙段范围内：越界洞会让 CAD 侧整面墙失败（Revit 侧静默夹紧）。

    startX 夹到 [0, L-w]；宽度仍越界则收窄到剩余长度；实在放不下（<100mm）丢弃。
    夹紧/丢弃都会在 stderr 留痕，避免静默改图。
    """
    out = []
    for o in openings:
        sx = to_m(o.get("startX", 0.0), unit)
        w = to_m(o.get("width", 1.0), unit)
        h = to_m(o.get("height", 1.5), unit)
        sill = to_m(o.get("sill", 0.9), unit)
        if w <= 0:
            continue
        orig = (sx, w)
        if sx < 0:
            w += sx
            sx = 0.0
        if sx + w > wall_length_m:
            w = wall_length_m - sx
            if sx > wall_length_m - 0.1:
                sx = max(0.0, wall_length_m - 0.1 - w)
        if w < 0.1:
            sys.stderr.write("[warn] 门窗洞越界丢弃: startX=%.3f width=%.3f 墙长=%.3f\n"
                             % (orig[0], orig[1], wall_length_m))
            continue
        if abs(sx - orig[0]) > 1e-9 or abs(w - orig[1]) > 1e-9:
            sys.stderr.write("[warn] 门窗洞夹紧: startX %.3f->%.3f width %.3f->%.3f 墙长=%.3f\n"
                             % (orig[0], sx, orig[1], w, wall_length_m))
        out.append({
            "type": o.get("type", "window"),
            "startX": round(sx, 6),
            "width": round(w, 6),
            "height": round(h, 6),
            "sill": round(sill, 6),
        })
    return out


def build_floor_el(room, unit):
    outline = room.get("outline", [])
    if len(outline) < 3:
        raise ValueError("房间 %s 的 outline 至少需要 3 个点" % room.get("name", "?"))
    s = 1000.0 if unit == "mm" else 1.0
    return {
        "type": "floor",
        "name": room.get("name", "F"),
        "outline": [[round(x / s, 6), round(y / s, 6)] for x, y in outline],
        "thickness": round(to_m(room["floorThickness"], unit) if "floorThickness" in room else 0.15, 6),
        "elevation": round(to_m(room.get("elevation", 0.0), unit), 6),
    }


def build_column_el(col, unit):
    s = 1000.0 if unit == "mm" else 1.0
    return {
        "type": "column",
        "name": col.get("id", col.get("name", "C")),
        "width": round(to_m(col.get("width", 0.4), unit), 6),
        "depth": round(to_m(col.get("depth", 0.4), unit), 6),
        "height": round(to_m(col.get("height", 3.0), unit), 6),
        "basePoint": [round(col["basePoint"][0] / s, 6),
                      round(col["basePoint"][1] / s, 6),
                      round(to_m(col.get("elevation", 0.0), unit), 6)],
        "angle": round(col.get("angle", 0.0), 6),
    }


def build_beam_el(beam, unit, floor_height_m):
    start = beam.get("start", [0, 0])
    end = beam.get("end", [6, 0])
    length, angle = length_angle(start, end)
    s = 1000.0 if unit == "mm" else 1.0
    # elevation 字段为 brief 单位；缺省取楼层高度（已换算为米）
    z = to_m(beam["elevation"], unit) if "elevation" in beam else floor_height_m
    return {
        "type": "beam",
        "name": beam.get("id", beam.get("name", "B")),
        "length": round(to_m(length, unit), 6),
        "width": round(to_m(beam.get("width", 0.3), unit), 6),
        "height": round(to_m(beam.get("height", 0.5), unit), 6),
        "basePoint": [round(start[0] / s, 6), round(start[1] / s, 6), round(z, 6)],
        "angle": round(angle, 6),
    }


def build_roof_el(roof, unit, outline_bbox, floor_height_m):
    """屋顶 → roof 元素（扩展字段，旧插件忽略）。"""
    s = 1000.0 if unit == "mm" else 1.0
    roof_type = roof.get("type", "gable")
    overhang = to_m(roof.get("overhang", 0.3), unit)
    elevation = (to_m(roof["elevation"], unit) if "elevation" in roof
                 else (floor_height_m if floor_height_m else 3.0))
    el = {
        "type": "roof",
        "name": roof.get("name", "ROOF"),
        "roofType": roof_type,
        "thickness": round(to_m(roof.get("thickness", 0.12), unit), 6),
        "height": round(to_m(roof.get("height", 1.5), unit), 6),
        "elevation": round(elevation, 6),
        "overhang": round(overhang, 6),
    }
    if roof.get("outline"):
        el["outline"] = [[round(x / s, 6), round(y / s, 6)] for x, y in roof["outline"]]
    else:
        # 由房间外包矩形（已为米）外扩 overhang（米）得到屋顶平面轮廓，勿再缩放
        if outline_bbox:
            x0, y0, x1, y1 = outline_bbox
            el["outline"] = [
                [round(x0 - overhang, 6), round(y0 - overhang, 6)],
                [round(x1 + overhang, 6), round(y0 - overhang, 6)],
                [round(x1 + overhang, 6), round(y1 + overhang, 6)],
                [round(x0 - overhang, 6), round(y1 + overhang, 6)],
            ]
    if roof.get("start") and roof.get("end"):
        el["ridgeStart"] = [round(roof["start"][0] / s, 6), round(roof["start"][1] / s, 6)]
        el["ridgeEnd"] = [round(roof["end"][0] / s, 6), round(roof["end"][1] / s, 6)]
    return el


def attach_door_window(doors, windows, walls_by_id, unit):

    """把独立的 doors/windows 挂接到对应墙的 openings（wallRef），无墙时返回独立元素。"""

    standalone = []

    for kind, group in (("door", doors), ("window", windows)):

        for item in group:

            wall_id = item.get("wallRef")

            if wall_id and wall_id in walls_by_id:

                walls_by_id[wall_id].setdefault("openings", []).append(

                    {

                        "type": item.get("type", kind),

                        "startX": item.get("startX", 0.0),

                        "width": item.get("width", 0.9),

                        "height": item.get("height", 2.1),

                        "sill": item.get("sill", 0.0),

                    }

                )

            else:

                s = 1000.0 if unit == "mm" else 1.0

                standalone.append({

                    "type": kind,

                    "name": item.get("id", item.get("name", "D/W")),

                    "width": round(to_m(item.get("width", 0.9), unit), 6),

                    "height": round(to_m(item.get("height", 2.1), unit), 6),

                    "thickness": round(to_m(item.get("thickness", 0.05), unit), 6),

                    "basePoint": [round(item.get("basePoint", [0, 0])[0] / s, 6),

                                  round(item.get("basePoint", [0, 0])[1] / s, 6),

                                  round(to_m(item.get("elevation", 0.0), unit), 6)],

                    "angle": round(item.get("angle", 0.0), 6),

                })

    return standalone





def compute_outline_bbox(rooms, unit):
    s = 1000.0 if unit == "mm" else 1.0
    xs, ys = [], []
    for r in rooms:
        for x, y in r.get("outline", []):
            xs.append(x / s)
            ys.append(y / s)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def polygon_area_centroid(outline, s=1.0):
    """shoelace 面积与质心：返回 (面积m²绝对值, cx米, cy米)；退化回退包围盒中心。"""
    pts = [(float(p[0]) / s, float(p[1]) / s) for p in outline]
    a = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cr = x0 * y1 - x1 * y0
        a += cr
        cx += (x0 + x1) * cr
        cy += (y0 + y1) * cr
    if abs(a) < 1e-9:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return 0.0, (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    a *= 0.5
    return abs(a), cx / (6.0 * a), cy / (6.0 * a)


def _append_annot(els, new_els, prefix):
    """拼接标注元素并给 name 加层/房间前缀，避免跨楼层重名触发
    preflight duplicate_name 警告。元素均为模型米坐标。"""
    for e in new_els:
        e["name"] = "%s-%s" % (prefix, e.get("name", "ANN"))
        els.append(e)


def build_plan_elements(brief, unit, level_elev=None, offset=(0.0, 0.0)):
    """2D 平面：俯视线框 + 尺寸标注。

    level_elev=None 时只画首层（兼容旧 --plan）；给定标高（米）时只画该层，
    并把整块图形平移到 offset（米），用于多楼层排版成一张图。
    """
    s = 1000.0 if unit == "mm" else 1.0
    els = []
    walls = brief.get("walls", [])
    rooms = brief.get("rooms", [])
    columns = brief.get("columns", [])
    ox, oy = offset

    def on_level(el):
        z = to_m(el.get("elevation", 0.0), unit)
        if level_elev is None:
            return abs(z) < 1e-9
        return abs(z - level_elev) < 1e-6

    walls = [w for w in walls if on_level(w)]
    rooms = [r for r in rooms if on_level(r)]
    columns = [c for c in columns if on_level(c)]

    def pt(x, y):
        return [round(x / s + ox, 6), round(y / s + oy, 6)]

    # 墙双线（2D 平面只画首层：多层墙段按 elevation 过滤，避免 30 层叠在一起）
    for w in walls:
        if to_m(w.get("elevation", 0.0), unit) > 1e-9:
            continue
        start, end = w.get("start", [0, 0]), w.get("end", [6, 0])
        t = to_m(w.get("thickness", 0.2), unit) / 2.0
        dx, dy = end[0] - start[0], end[1] - start[1]
        L = math.hypot(dx, dy) or 1e-9
        nx, ny = -dy / L * t * s, dx / L * t * s
        p1, p2 = pt(start[0] + nx, start[1] + ny), pt(end[0] + nx, end[1] + ny)
        p3, p4 = pt(start[0] - nx, start[1] - ny), pt(end[0] - nx, end[1] - ny)
        els.append({"type": "polyline2d", "name": w.get("id", "PW") + "-L",
                    "points": [p1, p2], "layer": LAYER_WALL})
        els.append({"type": "polyline2d", "name": w.get("id", "PW") + "-R",
                    "points": [p3, p4], "layer": LAYER_WALL})
        # 门窗平面符号（简化为洞口短线）
        for oi, o in enumerate(w.get("openings", [])):
            sx = to_m(o.get("startX", 0.0), unit) * s
            wid = to_m(o.get("width", 1.0), unit) * s
            ux, uy = dx / L, dy / L
            a = pt(start[0] + sx * ux, start[1] + sx * uy)
            b = pt(start[0] + (sx + wid) * ux, start[1] + (sx + wid) * uy)
            lay = LAYER_DOOR if o.get("type") == "door" else LAYER_WINDOW
            els.append({"type": "polyline2d", "name": w.get("id", "P") + "-O",
                        "points": [a, b], "layer": lay})
            # 门窗编号（M/C + 分米码）：沿墙法线外移到洞口中心外侧
            if annotations:
                kind = o.get("type", "window")
                prefix = "M" if kind == "door" else "C"
                ow_m = to_m(o.get("width", 1.0), unit)
                oh_m = to_m(o.get("height", 2.1 if kind == "door" else 1.5), unit)
                code = annotations.door_window_code(
                    prefix, round(ow_m * 1000.0), round(oh_m * 1000.0))
                el_wall = {"start": [start[0] / s + ox, start[1] / s + oy],
                           "end": [end[0] / s + ox, end[1] / s + oy],
                           "thickness": to_m(w.get("thickness", 0.2), unit)}
                opening = {"startX": to_m(o.get("startX", 0.0), unit), "width": ow_m}
                _append_annot(els,
                              annotations.door_window_label(el_wall, opening, code),
                              "%s-O%d" % (w.get("id", "W"), oi))
                _swing = getattr(annotations, "door_swing", None) if kind == "door" \
                    else getattr(annotations, "window_lines", None)
                if _swing:
                    _append_annot(els, _swing(el_wall, opening),
                                  "%s-O%d" % (w.get("id", "W"), oi))

    # 柱轮廓
    for c in columns:
        bp = c.get("basePoint", [0, 0])
        w_ = to_m(c.get("width", 0.4), unit) * s / 2
        d = to_m(c.get("depth", 0.4), unit) * s / 2
        cx, cy = bp[0], bp[1]
        els.append({"type": "polyline2d", "name": c.get("id", "PC") + "-O",
                    "points": [pt(cx - w_, cy - d), pt(cx + w_, cy - d),
                               pt(cx + w_, cy + d), pt(cx - w_, cy + d), pt(cx - w_, cy - d)],
                    "closed": True, "layer": LAYER_COLUMN})

    # 房间轮廓（2D 方形线框，brief 单位 → 米）
    for r in rooms:
        outline = r.get("outline", [])
        if len(outline) >= 3:
            els.append({"type": "polyline2d", "name": r.get("name", "ROOM") + "-OUT",
                        "points": [pt(p[0], p[1]) for p in outline], "closed": True,
                        "layer": LAYER_WALL})

    # 房间标注：三行标签（名称/面积/标高）置于 outline 质心；
    # annotations 缺失时回退旧单行名称文字
    cur_elev = 0.0 if level_elev is None else level_elev
    top_elev = max([to_m(r.get("elevation", 0.0), unit)
                    for r in brief.get("rooms", [])] or [0.0])
    for ri, r in enumerate(rooms):
        outline = r.get("outline", [])
        xs = [p[0] for p in outline]
        ys = [p[1] for p in outline]
        if not xs:
            continue
        rname = r.get("name", "房间")
        if annotations and len(outline) >= 3:
            area, ccx, ccy = polygon_area_centroid(outline, s)
            _append_annot(els,
                          annotations.room_label(rname, area,
                                                 to_m(r.get("elevation", 0.0), unit),
                                                 ccx + ox, ccy + oy),
                          "L%g-%s-%d" % (cur_elev, r.get("name", "ROOM"), ri))
        else:
            els.append({"type": "text2d", "name": rname + "-TXT",
                        "text": rname,
                        "insertPoint": pt((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2),
                        "height": round(0.25, 6), "layer": LAYER_TEXT})

    # 总尺寸标注（外包矩形）——bbox 已是米，直接落点，勿再按 brief 单位换算
    bbox = compute_outline_bbox(rooms, unit)
    if bbox:
        x0, y0, x1, y1 = bbox
        mx = lambda v: round(v + ox, 6)
        my = lambda v: round(v + oy, 6)
        els.append({"type": "polyline2d", "name": "DIM-W",
                    "points": [[mx(x0), my(y0 - 0.6)], [mx(x1), my(y0 - 0.6)]], "layer": LAYER_DIM})
        els.append({"type": "text2d", "name": "DIM-W-TXT",
                    "text": "%d" % round((x1 - x0) * s),
                    "insertPoint": [mx((x0 + x1) / 2), my(y0 - 0.9)],
                    "height": round(0.25, 6), "layer": LAYER_DIM})
        els.append({"type": "polyline2d", "name": "DIM-H",
                    "points": [[mx(x0 - 0.6), my(y0)], [mx(x0 - 0.6), my(y1)]], "layer": LAYER_DIM})
        els.append({"type": "text2d", "name": "DIM-H-TXT",
                    "text": "%d" % round((y1 - y0) * s),
                    "insertPoint": [mx(x0 - 1.2), my((y0 + y1) / 2)],
                    "height": round(0.25, 6), "rotation": math.pi / 2, "layer": LAYER_DIM})

        # 标高符号：每层一处（建筑右下角，标高=层 elevation；最高层双线三角）
        if annotations:
            _append_annot(els,
                          annotations.elev_symbol(mx(x1 + 1.2), my(y0), cur_elev,
                                                  topmost=abs(cur_elev - top_elev) < 1e-6),
                          "L%g" % cur_elev)
            # 剖切符号：仅首层放一次「1」，位置=建筑 X 向中线（呼应
            # build_sections 的「1-1 剖面」：x=midx 剖切、向 +X 投影）
            if abs(cur_elev) < 1e-9:
                midx, ymid = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                half = 0.5  # 剖切位置线纸面10mm → 模型1.0m（1:100）
                _append_annot(els,
                              annotations.section_mark([mx(midx), my(ymid - half)],
                                                       [mx(midx), my(ymid + half)], 1),
                              "L0-SECT")

    return els


def build_box_el(box, unit):
    """通用实体方块（brief 单位 → 米）；供结构/外围护/机电立管使用。"""
    s = 1000.0 if unit == "mm" else 1.0
    bp = box.get("basePoint", [0, 0, 0])
    return {
        "type": "box",
        "name": box.get("name", "BOX"),
        "basePoint": [round(bp[0] / s, 6), round(bp[1] / s, 6),
                      round((bp[2] if len(bp) > 2 else 0) / s, 6)],
        "width": round(to_m(box.get("width", 1.0), unit), 6),
        "depth": round(to_m(box.get("depth", 1.0), unit), 6),
        "height": round(to_m(box.get("height", 1.0), unit), 6),
        "layer": box.get("layer", "0"),
        "angle": round(box.get("angle", 0.0), 6),
    }


def build_grid_el(grid, unit):
    """轴网：延伸尺寸线 + 轴号（A/B/C… 与 1/2/3…），图层 A-GRID。
    annotations 可用时轴号进 φ8 圆圈（轴端 ext+0.3+r 处，drawing-standards
    §5.2）；字母编号跳过 I/O/Z。缺失时回退旧裸文字。"""
    s = 1000.0 if unit == "mm" else 1.0
    els = []
    xs = [round(v / s, 6) for v in grid.get("x", [])]
    ys = [round(v / s, 6) for v in grid.get("y", [])]
    if not xs or not ys:
        return els
    ext = 1.5
    r_b = 0.4  # 轴号圆半径：纸面4mm @1:100 → 模型0.4m
    y0, y1 = min(ys), max(ys)
    x0, x1 = min(xs), max(xs)
    for i, x in enumerate(xs):
        els.append({"type": "polyline2d", "name": "GRID-X%d" % (i + 1),
                    "points": [[x, y0 - ext], [x, y1 + ext]], "layer": "A-GRID"})
        if annotations:
            els.extend(annotations.axis_bubble(x, y1 + ext + 0.3 + r_b, str(i + 1)))
        else:
            els.append({"type": "text2d", "name": "GRID-X%d-TXT" % (i + 1),
                        "text": str(i + 1), "insertPoint": [x, y1 + ext + 0.3],
                        "height": 0.4, "layer": "A-GRID"})
    for i, y in enumerate(ys):
        label = annotations.axis_letter(i) if annotations else chr(ord("A") + i)
        els.append({"type": "polyline2d", "name": "GRID-Y%d" % (i + 1),
                    "points": [[x0 - ext, y], [x1 + ext, y]], "layer": "A-GRID"})
        if annotations:
            els.extend(annotations.axis_bubble(x1 + ext + 0.3 + r_b, y, label))
        else:
            els.append({"type": "text2d", "name": "GRID-Y%d-TXT" % (i + 1),
                        "text": label, "insertPoint": [x1 + ext + 0.3, y],
                        "height": 0.4, "layer": "A-GRID"})
    return els




def build_schedule_csv(brief, unit):
    """门窗统计表（按类型聚合），返回 CSV 文本（含表头）。"""
    s = 1000.0 if unit == "mm" else 1.0
    rows = {}
    for w in brief.get("walls", []):
        for o in w.get("openings", []) or []:
            kind = "门" if o.get("type") == "door" else "窗"
            wl = float(o.get("width", 0)) / s if unit == "m" else float(o.get("width", 0))
            hl = float(o.get("height", 0)) / s if unit == "m" else float(o.get("height", 0))
            wl = round(float(o.get("width", 0)) * (1.0 if unit == "mm" else 1000.0))
            hl = round(float(o.get("height", 0)) * (1.0 if unit == "mm" else 1000.0))
            sl = round(float(o.get("sill", 0)) * (1.0 if unit == "mm" else 1000.0))
            key = (kind, wl, hl, sl)
            rows[key] = rows.get(key, 0) + 1
    lines = ["类别,宽(mm),高(mm),窗台(mm),数量,洞口面积(m2)"]
    for (kind, wl, hl, sl), n in sorted(rows.items(), key=lambda x: (x[0][0], -x[0][1])):
        area = round(wl * hl / 1e6, 3)
        lines.append("%s,%d,%d,%d,%d,%.3f" % (kind, wl, hl, sl, n, area))
    return (chr(10).join(lines) + chr(10)).replace(chr(10), chr(13) + chr(10))



def build_sheet_elements(brief, elements3d, unit, sheet="A1", scale=100, per=4, project_name="", tables=None):
    """把平面/立面/剖面视图套进带图框标题栏的图纸，返回纯 2D 元素列表。"""
    project = brief.get("project", {})
    floors = int(project.get("floors", project.get("floorCount", 1)) or 1)
    fh = to_m(project.get("floorHeight", 0.0), unit) or 3.0
    heights = sorted({round(to_m(r.get("elevation", 0.0), unit), 6) for r in brief.get("rooms", [])})
    if not heights:
        heights = [lv * fh for lv in range(floors)]
    views = []
    for i, h in enumerate(heights):
        views.append(("L%d 平面" % i, "平面", build_plan_elements(brief, unit, level_elev=h)))
    views.append(("四立面", "立面", build_elevations(elements3d, unit, floors, fh)))
    views.append(("1-1 剖面", "剖面", build_sections(elements3d, unit, floors, fh)))
    meta = {"project": project_name or project.get("name", "工程"),
            "stage": project.get("stage", "方案"),
            "northAngle": project.get("northAngle", 0)}
    return sheets.layout_sheets(views, meta, sheet=sheet, scale=scale, per_sheet=per,
                                tables=tables)

def build_plan_sheet(brief, unit, cols=None):
    """把每一层平面排成一张图（网格布局 + 层标题），返回 2D 元素列表。"""
    s = 1000.0 if unit == "mm" else 1.0
    heights = sorted({round(to_m(el.get("elevation", 0.0), unit), 6)
                      for el in brief.get("rooms", [])})
    if not heights:
        heights = [0.0]
    bbox = compute_outline_bbox(brief.get("rooms", []), unit) or (0.0, 0.0, 10.0, 10.0)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    margin_x, margin_y = bw * 0.35 + 3.0, bh * 0.35 + 3.0
    tile_w, tile_h = bw + margin_x, bh + margin_y
    n = len(heights)
    cols = cols or max(1, int(math.ceil(math.sqrt(n))))
    els = []
    for i, h in enumerate(heights):
        cx = (i % cols) * tile_w
        cy = (i // cols) * tile_h
        els.extend(build_plan_elements(brief, unit, level_elev=h, offset=(cx, cy)))
        els.append({"type": "text2d", "name": "SHEET-L%d-TITLE" % i,
                    "text": "L%d  平面图  1:100" % i,
                    "insertPoint": [round(cx + 0.5, 6), round(cy + bh + 1.2, 6)],
                    "height": 0.6, "layer": LAYER_TEXT})
    return els




def _aabb(el):
    """近似求元素的世界 AABB（米）。角度只影响平面投影，取 4 角旋转后包围。"""
    t = el.get("type")
    bp = el.get("basePoint") or el.get("position") or [0, 0, 0]
    bx, by = float(bp[0]), float(bp[1])
    bz = float(bp[2]) if len(bp) > 2 else float(el.get("elevation", 0.0))
    ang = float(el.get("angle", 0.0) or 0.0)
    if t == "wall":
        L = float(el.get("length", 0.0))
        h = float(el.get("height", 3.0))
        dx, dy = L * math.cos(ang), L * math.sin(ang)
        xs = [bx, bx + dx]
        ys = [by, by + dy]
        return min(xs), max(xs), min(ys), max(ys), bz, bz + h
    if t in ("column", "beam", "box", "door", "window"):
        w = float(el.get("width", 0.4))
        d = float(el.get("depth", el.get("length", 0.4)))
        h = float(el.get("height", 3.0))
        cx, cy = bx + w / 2.0, by + d / 2.0
        corners = [(bx, by), (bx + w, by), (bx + w, by + d), (bx, by + d)]
        if abs(ang) > 1e-9:
            ca, sa = math.cos(ang), math.sin(ang)
            corners = [(cx + (x - cx) * ca - (y - cy) * sa,
                        cy + (x - cx) * sa + (y - cy) * ca) for x, y in corners]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        return min(xs), max(xs), min(ys), max(ys), bz, bz + h
    if t in ("floor", "roof"):
        pts = el.get("outline") or []
        if not pts:
            return 0, 0, 0, 0, 0, 0
        xs = [float(pt[0]) for pt in pts]
        ys = [float(pt[1]) for pt in pts]
        th = float(el.get("thickness", 0.15))
        return min(xs), max(xs), min(ys), max(ys), bz, bz + th
    return bx, bx, by, by, bz, bz


def _rect(x0, y0, x1, y1, name, layer, ox=0.0, oy=0.0):
    return {"type": "polyline2d", "name": name, "closed": True, "layer": layer,
            "points": [[round(x0 + ox, 6), round(y0 + oy, 6)],
                       [round(x1 + ox, 6), round(y0 + oy, 6)],
                       [round(x1 + ox, 6), round(y1 + oy, 6)],
                       [round(x0 + ox, 6), round(y1 + oy, 6)]]}


def build_elevations(elements, unit, floors=1, floor_height=3.0):
    """由 3D 构件投影出 4 个立面（南/北/东/西）+ 楼层线 + 标题。

    立面是示意投影（AABB 矩形），不是精确消隐轮廓；足够当立面方案设计图。
    """
    s = 1000.0 if unit == "mm" else 1.0
    solids = [e for e in elements if e.get("type") in ("wall", "column", "beam", "box", "floor", "roof")]
    solids = [e for e in solids if not (e.get("layer", "").startswith("A-SITE"))]
    if not solids:
        return []
    boxes = [_aabb(e) for e in solids]
    minx = min(b[0] for b in boxes); maxx = max(b[1] for b in boxes)
    miny = min(b[2] for b in boxes); maxy = max(b[3] for b in boxes)
    minz = min(b[4] for b in boxes); maxz = max(b[5] for b in boxes)
    W = maxx - minx
    D = maxy - miny
    H = maxz - minz
    gap = max(W, D) * 0.35 + 6.0
    title_h = 2.5
    els = []
    facades = [
        ("ELEV-SOUTH", "S", True),
        ("ELEV-NORTH", "N", True),
        ("ELEV-EAST", "E", False),
        ("ELEV-WEST", "W", False),
    ]
    for i, (tag, axis, is_ns) in enumerate(facades):
        ox = i * (max(W, D) + gap)
        for (mnx, mxx, mny, mxy, mnz, mxz), el in zip(boxes, solids):
            if is_ns:
                x0, x1 = (mnx - minx), (mxx - minx)
                if axis == "N":
                    x0, x1 = (maxx - mxx), (maxx - mnx)
            else:
                x0, x1 = (mny - miny), (mxy - miny)
                if axis == "E":
                    x0, x1 = (maxy - mxy), (maxy - mny)
            z0, z1 = (mnz - minz), (mxz - minz)
            els.append(_rect(x0, z0, x1, z1, "%s-%s" % (tag, el.get("name", i)), "A-ELEV", ox, 0))
            # 墙上门窗洞（仅外立面墙）
            if el.get("type") == "wall" and el.get("openings"):
                L = float(el.get("length", 1.0))
                for j, o in enumerate(el["openings"]):
                    ow = float(o.get("width", 0))
                    oh = float(o.get("height", 0))
                    sx = float(o.get("startX", 0))
                    sill = float(o.get("sill", 0))
                    if ow <= 0 or oh <= 0:
                        continue
                    wx0 = (sx / L) * (x1 - x0); wx1 = ((sx + ow) / L) * (x1 - x0)
                    wz0 = sill - minz; wz1 = (sill + oh) - minz
                    if is_ns and axis == "N":
                        wx0, wx1 = (x1 - x0) - wx1, (x1 - x0) - wx0
                    els.append(_rect(wx0, wz0, wx1, wz1, "%s-%s-O%d" % (tag, el.get("name", i), j),
                                     "A-DOOR" if o.get("type") == "door" else "A-WINDOW", ox, 0))
        # 楼层线
        for lv in range(floors + 1):
            z = lv * floor_height - minz
            if z < 0 or z > H + 1e-6:
                continue
            els.append({"type": "polyline2d", "name": "%s-FL%d" % (tag, lv),
                        "points": [[round(ox - 1.5, 6), round(z, 6)],
                                   [round(ox + (W if is_ns else D) + 1.5, 6), round(z, 6)]],
                        "layer": "A-DIM"})
            els.append({"type": "text2d", "name": "%s-FL%d-TXT" % (tag, lv),
                        "text": "%.3f" % (lv * floor_height),
                        "insertPoint": [round(ox - 3.2, 6), round(z - 0.15, 6)],
                        "height": 0.4, "layer": "A-DIM"})
        els.append({"type": "text2d", "name": "%s-TITLE" % tag,
                    "text": "%s 立面  1:100" % tag.replace("ELEV-", ""),
                    "insertPoint": [round(ox, 6), round(H + title_h, 6)],
                    "height": 1.0, "layer": "A-TEXT"})
    return els


def build_sections(elements, unit, floors=1, floor_height=3.0):
    """横向 1-1 剖（沿 X 中切，投影 Y-Z）：示意剖切。"""
    s = 1000.0 if unit == "mm" else 1.0
    solids = [e for e in elements if e.get("type") in ("wall", "column", "beam", "box", "floor", "roof")]
    solids = [e for e in solids if not (e.get("layer", "").startswith("A-SITE"))]
    if not solids:
        return []
    boxes = [_aabb(e) for e in solids]
    minx = min(b[0] for b in boxes); maxx = max(b[1] for b in boxes)
    miny = min(b[2] for b in boxes); maxy = max(b[3] for b in boxes)
    minz = min(b[4] for b in boxes); maxz = max(b[5] for b in boxes)
    midx = (minx + maxx) / 2.0
    cut = 0.12
    els = []
    for (mnx, mxx, mny, mxy, mnz, mxz), el in zip(boxes, solids):
        hatched = (mnx - cut) <= midx <= (mxx + cut)   # 被剖切到
        D = maxy - miny
        if hatched:
            els.append(_rect(mny - miny, mnz - minz, mxy - miny, mxz - minz,
                             "SECT-%s" % el.get("name", ""), "A-WALL"))
        else:
            els.append(_rect(mny - miny, mnz - minz, mxy - miny, mxz - minz,
                             "SECT-BG-%s" % el.get("name", ""), "A-ELEV"))
    for lv in range(floors + 1):
        z = lv * floor_height - minz
        if z < 0 or z > maxz - minz + 1e-6:
            continue
        els.append({"type": "polyline2d", "name": "SECT-FL%d" % lv,
                    "points": [[-1.5, round(z, 6)], [round(D + 1.5, 6), round(z, 6)]],
                    "layer": "A-DIM"})
        els.append({"type": "text2d", "name": "SECT-FL%d-TXT" % lv,
                    "text": "%.3f" % (lv * floor_height), "insertPoint": [-3.2, round(z - 0.15, 6)],
                    "height": 0.4, "layer": "A-DIM"})
    els.append({"type": "text2d", "name": "SECT-TITLE", "text": "1-1 剖面  1:100",
                "insertPoint": [0.0, round(maxz - minz + 2.5, 6)], "height": 1.0, "layer": "A-TEXT"})
    return els


def build_stairs_solid_el(stairs, unit, floor_height_m):
    """楼梯 → 3D 实体台阶（box 逐级堆叠）。

    底标高 = fromFloor × 层高（或显式 elevation）；第 i 级（1..risers）为
    width × run × (i × rise) 的实心方块，相邻级无缝衔接，侧视即标准踏步剖面。
    用通用 box 而非楼梯族：Revit / AutoCAD 两侧都直接出 Solid3D，不依赖族库。
    """
    els = []
    fh = floor_height_m if floor_height_m and floor_height_m > 0 else 3.0
    for st in stairs:
        risers = int(st.get("risers", 0))
        if risers < 2:
            continue
        run = to_m(st.get("run", 0.25), unit)
        rise = to_m(st.get("rise", 0.19), unit)
        width = to_m(st.get("width", 1.0), unit)
        x = to_m(st.get("x", 0.0), unit)
        y = to_m(st.get("y", 0.0), unit)
        if "elevation" in st:
            base_z = to_m(st["elevation"], unit)
        else:
            base_z = int(st.get("fromFloor", 0) or 0) * fh
        name = st.get("name", "S")
        for i in range(1, risers + 1):
            els.append({
                "type": "box",
                "name": "%s-R%d" % (name, i),
                "basePoint": [round(x, 6), round(y + run * (i - 1), 6), round(base_z, 6)],
                "width": round(width, 6),
                "depth": round(run, 6),
                "height": round(rise * i, 6),
                "layer": LAYER_STAIR,
            })
    return els


def build_stairs_el(stairs, unit):
    """楼梯 → 2D 踏步线 + 标注（polyline2d/text2d，扩展字段）。

    输入楼梯来自 P1 布局 brief（含 x/y 起点与 riser/tread 参数，brief 单位）；
    内部已 to_m() 统一为米，落点不要再按 brief 单位二次换算。
    """
    s = 1000.0 if unit == "mm" else 1.0
    els = []

    def pt(x, y):
        return [round(x, 6), round(y, 6)]

    for st in stairs:
        risers = int(st.get("risers", 0))
        if risers < 2:
            continue
        run = to_m(st.get("run", 0.25), unit)
        rise = to_m(st.get("rise", 0.19), unit)
        width = to_m(st.get("width", 1.0), unit)
        x = to_m(st.get("x", 0.0), unit)
        y = to_m(st.get("y", 0.0), unit)
        going = run * (risers - 1)
        name = st.get("name", "S")
        # 踏步横线（沿进深方向排列，从 y 起每 run 一条）
        for i in range(risers):
            yy = y + run * i
            els.append({"type": "polyline2d", "name": "%s-T%d" % (name, i),
                        "points": [pt(x, yy), pt(x + width, yy)],
                        "layer": LAYER_STAIR})
        els.append({"type": "text2d", "name": "%s-TXT" % name,
                    "text": "%s %d级 %d×%d" % (name, risers,
                                               round(run * s), round(rise * s)),
                    "insertPoint": pt(x, y - 0.4),
                    "height": round(0.2, 6), "layer": LAYER_STAIR})
    return els


def convert(brief, dwg_path, with_plan=False, cli_unit="", plan_sheet=False,
            with_elevations=False, elev_true=False):
    unit = resolve_unit(brief, cli_unit)
    project = brief.get("project", {})
    floor_height = to_m(project.get("floorHeight", 0.0), unit) or 0.0

    elements = []

    # 房间 → floor
    for room in brief.get("rooms", []):
        elements.append(build_floor_el(room, unit))

    # 墙（含 openings 挂接）
    walls = brief.get("walls", [])
    walls_by_id = {w.get("id", w.get("name")): w for w in walls}
    for w in walls:
        elements.append(build_wall_el(w, unit))

    # 独立门窗：有 wallRef 挂接墙，否则独立薄板
    standalone_dw = attach_door_window(
        brief.get("doors", []), brief.get("windows", []), walls_by_id, unit)
    elements.extend(standalone_dw)

    # 结构 / 外围护 / 机电方块
    for box in brief.get("boxes", []):
        elements.append(build_box_el(box, unit))

    # 柱 / 梁
    for col in brief.get("columns", []):
        elements.append(build_column_el(col, unit))
    for beam in brief.get("beams", []):
        elements.append(build_beam_el(beam, unit, floor_height))

    # 屋顶
    if brief.get("roof"):
        bbox = compute_outline_bbox(brief.get("rooms", []), unit)
        elements.append(build_roof_el(brief["roof"], unit, bbox, floor_height))

    # 楼梯 3D 实体（与 --plan 无关，始终生成）
    elements.extend(build_stairs_solid_el(brief.get("stairs", []), unit, floor_height))

    # 2D 平面
    if plan_sheet:
        elements.extend(build_plan_sheet(brief, unit))
        elements.extend(build_grid_el(brief.get("grid", {}), unit))
    elif with_plan:
        elements.extend(build_plan_elements(brief, unit))
        elements.extend(build_grid_el(brief.get("grid", {}), unit))
        elements.extend(build_stairs_el(brief.get("stairs", []), unit))

    if with_elevations or elev_true:
        floors = int(project.get("floors", project.get("floorCount", 1)) or 1)
        fh = floor_height if floor_height > 0 else 3.0
        if elev_true:
            elements.extend(elevations2.build_elevations_true(elements, unit))
        else:
            elements.extend(build_elevations(elements, unit, floors, fh))
            elements.extend(build_sections(elements, unit, floors, fh))

    request = {"elements": elements, "savePath": dwg_path}
    # 语义层（Revit 专用，CAD 忽略未知键）
    if brief.get("levels"):
        _sc = 1000.0 if unit == "mm" else 1.0
        request["levels"] = [{"name": lv.get("name", "L"),
                              "elevation": round(float(lv.get("elevation", 0.0)) / _sc, 6)}
                             for lv in brief["levels"]]
    if brief.get("roomSpaces"):
        _sc = 1000.0 if unit == "mm" else 1.0
        request["rooms"] = [{
            "name": r.get("name", ""), "number": r.get("number", ""),
            "level": int(r.get("level", 0) or 0),
            "center": [round(c / _sc, 6) for c in r.get("center", [0, 0])],
            "outline": [[round(p[0] / _sc, 6), round(p[1] / _sc, 6)] for p in r.get("outline", [])],
        } for r in brief["roomSpaces"]]
    if brief.get("grid"):
        _sc = 1000.0 if unit == "mm" else 1.0
        _g = brief["grid"]
        request["grids"] = {"x": [round(v / _sc, 6) for v in _g.get("x", [])],
                            "y": [round(v / _sc, 6) for v in _g.get("y", [])]}
    return request, unit


def load_intent(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def resolve_source(args):
    """返回 (brief_or_intent, is_intent)。校验参数互斥。"""
    if args.brief and args.intent:
        raise SystemExit("--brief 与 --intent 不能同时使用")
    if not args.brief and not args.intent:
        raise SystemExit("必须提供 --brief 或 --intent 之一")
    if args.intent:
        return load_intent(args.intent), True
    return load_brief(args.brief), False


def run_compliance_check(brief, layout, out_path, unit):
    """P0 规范审查：由 brief（普通/布局）构建审查视图，输出违规清单 JSON。"""
    if layout is not None:
        ctx = context_from_layout(layout, brief.get("project", {}))
    else:
        ctx = context_from_brief(brief)
    violations = compliance_check(ctx)
    summary = compliance_summarize(violations)
    report = {
        "checkedAt": "brief_to_cad --check",
        "unit": ctx.unit,
        "summary": summary,
        "violations": compliance_to_dict(violations),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def _main(argv=None):
    args = parse_args(argv)
    if args.env_check:
        sys.exit(env_check())
    source, is_intent = resolve_source(args)

    layout = None
    brief = source
    if is_intent:
        from planner import plan_building
        result = plan_building(source)
        if result.get("errors"):
            print("[brief_to_cad] intent 预校验未通过，未生成 request：")
            for e in result["errors"]:
                print("  ✗ " + e)
            return 1
        brief = result["brief"]
        layout = result["layout"]
        for n in result.get("notes", []):
            print("  · " + n)

    source_path = args.intent if is_intent else args.brief
    if args.dwg:
        dwg_path = args.dwg.replace("\\", "/")
    else:
        base = os.path.splitext(os.path.basename(source_path))[0] if source_path != "-" else "model"
        # 集中配置的输出目录；cfg 缺失时退到系统临时目录，绝不写入技能目录
        out_dir = cfg.resolve(cfg.get("paths.dwgOutputDir")) if cfg is not None else ""
        if not out_dir:
            out_dir = tempfile.gettempdir()
        os.makedirs(out_dir, exist_ok=True)
        dwg_path = os.path.join(out_dir, base + ".dwg").replace("\\", "/")

    request, unit = convert(brief, dwg_path, with_plan=args.plan, cli_unit=args.unit,
                            plan_sheet=args.plan_sheet, with_elevations=args.elevations,
                            elev_true=args.elev_true)

    if args.sheets:
        _tables = []
        try:
            import schedules as _sch
            _r = request.get("rooms")
            if _r:
                _tables.append(("房间面积表", _sch.build_room_schedule(request), 5,
                                [0.020, 0.030, 0.070, 0.040, 0.040]))
            _tables.append(("工程量快估", _sch.build_quantity_takeoff(request), 6,
                            [0.055, 0.045, 0.022, 0.040, 0.040, 0.085]))
        except Exception as _e:
            print("  [warn] 表格生成跳过: %s" % _e)
        request["elements"] = build_sheet_elements(
            brief, request["elements"], unit, sheet=args.sheet_size, scale=args.sheet_scale,
            per=args.sheet_per,
            project_name=os.path.splitext(os.path.basename(args.brief or args.intent or ""))[0],
            tables=_tables or None)

    if args.plan_only and args.plan_sheet:
        request["elements"] = [e for e in request["elements"]
                               if e["type"] in ("polyline2d", "text2d")]
    if args.revit_drawings:
        _dr = {"dir": os.path.abspath(args.revit_drawings)}
        if args.revit_sheets:
            _dr["sheets"] = True
            _dr["scale"] = args.sheet_scale
        request["drawings"] = _dr

    blocked = False
    if args.validate != "off":
        _fix = (args.validate == "fix") and not args.sheets
        request, pf = preflight.check_request(
            request, target=args.target, fix=_fix)
        pf_path = os.path.join(os.path.dirname(os.path.abspath(args.request)), "preflight.json")
        with open(pf_path, "w", encoding="utf-8") as f:
            json.dump(pf, f, ensure_ascii=False, indent=2)
        s_ = pf["summary"]
        print("  发单校验 : %s target=%s 容差%.2fmm，构件 %d -> %d，修复 %d，error %d，warning %d"
              % ("通过" if s_["passed"] else "未通过", s_["target"],
                 s_["toleranceM"] * 1000.0, s_["elementsIn"], s_["elementsOut"],
                 s_["fixed"], s_["errors"], s_["warnings"]))
        for v in pf["violations"]:
            if v["severity"] == "error" or v["rule"] in ("dropped", "polyline_short_segment"):
                print("    [%s] %s %s: %s" % (v["severity"], v["rule"], v["element"], v["message"]))
        print("  校验报告 : %s" % pf_path)
        if not s_["passed"]:
            if args.allow_invalid:
                print("  [warn] 校验未通过，--allow-invalid 指定继续写出 request")
            else:
                print("  已拦截：未写出 request（修好上面 error，或加 --allow-invalid 强制）")
                blocked = True

    if not blocked:
        with open(args.request, "w", encoding="utf-8") as f:
            json.dump(request, f, ensure_ascii=False, indent=2)
        if args.schedule:
            csv = build_schedule_csv(brief, unit)
            with open(args.schedule, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv)
            print("  门窗表    : %s" % args.schedule)
        if args.area_schedule:
            csv = schedules.build_room_schedule(request)
            with open(args.area_schedule, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv)
            print("  面积表    : %s" % args.area_schedule)
        if args.quantity:
            csv = schedules.build_quantity_takeoff(request)
            with open(args.quantity, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv)
            print("  工程量    : %s" % args.quantity)

    if blocked:
        return 1

    # P0 规范审查（生成→审查闭环）
    if args.check:
        check_out = args.check_out or os.path.join(
            os.path.dirname(os.path.abspath(args.request)), "violations.json")
        report = run_compliance_check(brief, layout, check_out, unit)
        print("  规范审查 : %s" % ("通过 ✅" if report["summary"]["passed"] else
                                 "发现 %d 个问题（%d error / %d warning）"
                                 % (report["summary"]["total"],
                                    report["summary"]["errors"],
                                    report["summary"]["warnings"])))
        for v in report["violations"]:
            print("    [%s/%s] %s — %s" % (v["severity"], v["category"],
                                           v["ruleName"], v["message"]))
            if v.get("fixHint"):
                print("      回改建议: %s" % v["fixHint"])
        print("  审查报告 : %s" % check_out)

    # 摘要输出
    from collections import Counter
    counts = Counter(el["type"] for el in request["elements"])
    extended_used = sorted(t for t in counts if t in EXTENDED_TYPES)
    total_openings = sum(len(el.get("openings", [])) for el in request["elements"]
                         if el["type"] == "wall")

    print("[brief_to_cad] 完成")
    print("  单位换算 : brief unit=%s -> 插件单位=m" % unit)
    print("  构件统计 : %s" % dict(counts))
    if total_openings:
        print("  门窗洞   : 共 %d 个（写入 wall.openings，需插件扩展支持布尔减法）" % total_openings)
    if extended_used:
        print("  扩展字段 : %s 需插件扩展（docs/plugin-extension.md）" % ", ".join(extended_used))
    print("  请求文件 : %s" % args.request)
    print("  DWG 路径 : %s" % dwg_path)
    if args.plan_sheet:
        print("  2D 平面  : 已生成（每层一张，网格排版）")
    else:
        print("  2D 平面  : %s" % ("已生成" if args.plan else "未生成（加 --plan 或 --plan-sheet）"))

    if total_openings or extended_used:
        print("  ⚠ 开洞/屋顶/2D 图元依赖 CadJsonPlugin 的扩展能力（见 docs/plugin-extension.md）；"
              "旧版插件会忽略这些未知键，仅创建基础构件。")
    return 0


INPUT_ERRORS = (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError)


def main(argv=None):
    try:
        return _main(argv)
    except INPUT_ERRORS as exc:
        print("[brief_to_cad] 输入错误：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
