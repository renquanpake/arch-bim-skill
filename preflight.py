#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preflight.py — 发单前的几何校验与自动修复（纯标准库，零 GUI）

目标：把"进到目标软件才报错"的问题前移到 request.json 阶段。

两类容差：
  cad   1e-6 m —— AutoCAD 对极短线段基本无感
  revit 0.0016 m —— Revit ShortCurveTolerance(≈0.8mm) 的 2 倍安全边界

自动修复（fix）：
  * 零长度/零截面墙、柱、梁、箱体 → 丢弃
  * polyline2d 中短于容差的线段 → 丢弃该段；剩余不足 2 点 → 丢弃整个元素
  * 厚度/高度缺失或 ≤0 → 丢弃
  * 洞口越界（沿墙/超高）→ 夹紧到墙段范围并降级为 warning；放不下才丢弃
  * 被前一洞口完全覆盖的重复洞口 → 删除（opening_overlap）
不可修复的记为 error（洞口部分重叠、未知类型、非法 outline、负标高）。

附加检测规则（v2，warning 为主、不拦单）：
  * opening_overlap       同一墙洞口区间重叠（fix 删除完全覆盖项，部分重叠仍 error）
  * wall_collinear_overlap 近共线同层墙段沿轴重叠 ≥50mm（Revit 墙连接报错前兆）
  * slab_below_grade / element_z_out_of_range  依据顶层 levels 推断建筑竖向区间，
                          构件整体低于下限或顶面远超上限（无 levels 则跳过）
  * opening_height_over_wall 洞口 sill+height 超过该墙 height（fix 夹紧，否则 error）
  * zero_area_solid       柱/梁/箱宽/深 ≤20mm 或整体极小（warning 保留；仅厚度薄的
                          大面积构件视为合法薄层豁免；完全退化仍 error）

用法：
  python preflight.py request.json [--target both|cad|revit] [--fix] [--out pf.json]
退出码：0 通过（含已修复）；1 存在未修复 error。
"""

import argparse
import json
import math
import os
import sys

MIN_ELEV = -5.0  # 允许基础/散水/台阶等埋地构件（-5m 以内）

TOL = {
    "cad": 1e-6,
    "revit": 0.0016,
}

DEDUPE_TYPES = {"wall", "floor", "roof", "column", "beam", "box", "door", "window", "instance"}

VALID_TYPES = {
    "wall", "floor", "roof", "column", "beam", "box",
    "door", "window", "polyline2d", "text2d", "instance",
}

FT_PER_M = 1.0 / 0.3048

# --- v2 规则阈值 ---
SOLID_THIN_M = 0.02                 # 柱/梁/箱任意维度 ≤20mm → zero_area_solid warning
COLLINEAR_ANG_RAD = math.radians(1.0)   # 共线判定：方向差 ≤1°（mod π）
COLLINEAR_Z_TOL = 0.05              # 共线判定：底标高差 ≤50mm 视为同层
COLLINEAR_MIN_OVERLAP = 0.05        # 沿轴投影重叠 ≥50mm 才报
COLLINEAR_MAX_REPORT = 12           # 共线对最多列 12 条，其余汇总
Z_LEVEL_SUSPECT_M = 200.0           # levels 标高绝对值 >200m 疑似单位错误 → 跳过 z 检查


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _seg_len(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _poly_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _v(sev, rule, element, message, hint=""):
    return {"severity": sev, "rule": rule, "element": element,
            "message": message, "fixHint": hint}


def _open_interval(o, tol):
    """洞口 → 沿墙区间 [startX, startX+width]；非法返回 None。"""
    if not isinstance(o, dict):
        return None
    sx = _num(o.get("startX"))
    w = _num(o.get("width"))
    if w <= tol:
        return None
    return (sx, sx + w)


def _iv_overlap(a, b, eps):
    return b[0] < a[1] - eps and b[1] > a[0] + eps


def _iv_contains(a, b, eps):
    """b 完全被 a 覆盖"""
    return a[0] - eps <= b[0] and b[1] <= a[1] + eps


def _flag_opening_overlaps(name, openings, tol, out, severity):
    """同一墙上洞口区间重叠。severity=error（report 模式）或 warning（fix 兜底）。"""
    ivs = []
    for i, o in enumerate(openings or []):
        iv = _open_interval(o, tol)
        if iv is not None:
            ivs.append((i, iv))
    for x in range(len(ivs)):
        for y in range(x + 1, len(ivs)):
            i, a = ivs[x]
            j, b = ivs[y]
            if _iv_overlap(a, b, tol):
                out["violations"].append(_v(
                    severity, "opening_overlap", "%s#opening%d/%d" % (name, i, j),
                    "洞口区间 [%.3f, %.3f] 与 [%.3f, %.3f] 在墙上重叠"
                    % (a[0], a[1], b[0], b[1]),
                    "把后一个洞口沿墙错开，或用 --fix 删除被完全覆盖的洞口"))


def _fix_wall_openings(el, name, length, height, tol, out):
    """fix 模式洞口处理：越界夹紧 → 完全覆盖的洞删除 → 残余部分重叠记 error。
    返回可能被修改的 el（不改动入参对象）。"""
    raw = el.get("openings")
    if not isinstance(raw, list) or not raw:
        return el
    eps = tol
    kept = []
    mutated = False
    for i, o in enumerate(raw):
        tag = "%s#opening%d" % (name, i)
        if not isinstance(o, dict):
            out["violations"].append(_v("warning", "opening_invalid", tag,
                                        "洞口不是对象，已丢弃", "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        sx = _num(o.get("startX"))
        w = _num(o.get("width"))
        h = _num(o.get("height"))
        sill = _num(o.get("sill"))
        if w <= tol:
            out["violations"].append(_v("warning", "opening_degenerate", tag,
                                        "洞口宽度 %.4fm ≤ 容差，已丢弃" % w, "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        if h <= tol:
            out["violations"].append(_v("warning", "opening_degenerate", tag,
                                        "洞口高度 %.4fm ≤ 容差，已丢弃" % h, "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        # --- 沿墙夹紧 ---
        nsx, nw = sx, w
        if nsx < 0:
            nsx = 0.0
        if nsx > length - eps:
            out["violations"].append(_v("warning", "opening_out_of_wall", tag,
                                        "起点 startX=%.3f 已超出墙长 %.3f，洞口无法容纳，已丢弃"
                                        % (sx, length), "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        if nsx + nw > length:
            nw = length - nsx
        if nw <= tol:
            out["violations"].append(_v("warning", "opening_out_of_wall", tag,
                                        "洞口宽度 %.3fm 超过墙长 %.3fm，已丢弃" % (w, length),
                                        "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        if abs(nsx - sx) > eps or abs(nw - w) > eps:
            out["violations"].append(_v("warning", "opening_out_of_wall", tag,
                                        "洞口夹紧到墙段范围 startX %.3f->%.3f width %.3f->%.3f"
                                        % (sx, nsx, w, nw), "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
        # --- 竖向夹紧（对 per-wall height 生效）---
        nsl, nh = sill, h
        if nsl < 0:
            nsl = 0.0
        if nsl >= height - eps:
            out["violations"].append(_v("warning", "opening_height_over_wall", tag,
                                        "sill=%.3f 已达墙高 %.3f，洞口无法容纳，已丢弃"
                                        % (sill, height), "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        if nsl + nh > height:
            nh = height - nsl
        if nh <= tol:
            out["violations"].append(_v("warning", "opening_height_over_wall", tag,
                                        "洞口超高 sill=%.3f h=%.3f 墙高=%.3f，无剩余空间，已丢弃"
                                        % (sill, h, height), "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        if abs(nsl - sill) > eps or abs(nh - h) > eps:
            out["violations"].append(_v("warning", "opening_height_over_wall", tag,
                                        "洞口超高已夹紧 sill %.3f->%.3f height %.3f->%.3f（墙高 %.3f）"
                                        % (sill, nsl, h, nh, height), "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
        oo = dict(o)
        oo["startX"] = round(nsx, 6)
        oo["width"] = round(nw, 6)
        oo["sill"] = round(nsl, 6)
        oo["height"] = round(nh, 6)
        kept.append(oo)
    # --- 重叠处理：先删完全被覆盖的，剩余部分重叠记 error ---
    kept.sort(key=lambda o: (o["startX"], -o["width"]))
    final = []
    for o in kept:
        iv = (o["startX"], o["startX"] + o["width"])
        cover = None
        for k in final:
            if _iv_contains((k["startX"], k["startX"] + k["width"]), iv, eps):
                cover = k
                break
        if cover is not None:
            out["violations"].append(_v("warning", "opening_overlap",
                                        "%s#opening(%s)" % (name, o.get("type", "?")),
                                        "洞口 [%.3f, %.3f] 被 [%s] [%.3f, %.3f] 完全覆盖，已删除"
                                        % (iv[0], iv[1], cover.get("type", "?"),
                                           cover["startX"], cover["startX"] + cover["width"]),
                                        "由 preflight 自动修复"))
            out["fixed"] += 1
            mutated = True
            continue
        final.append(o)
    _flag_partial_overlaps(name, final, eps, out)
    if mutated:
        el = dict(el)
        el["openings"] = final
    return el


def _flag_partial_overlaps(name, openings, eps, out):
    for x in range(len(openings)):
        for y in range(x + 1, len(openings)):
            a = openings[x]
            b = openings[y]
            ia = (a["startX"], a["startX"] + a["width"])
            ib = (b["startX"], b["startX"] + b["width"])
            if _iv_overlap(ia, ib, eps):
                out["violations"].append(_v(
                    "error", "opening_overlap",
                    "%s#opening%d/%d" % (name, x, y),
                    "洞口区间 [%.3f, %.3f] 与 [%.3f, %.3f] 部分重叠，无法安全自动挪开"
                    % (ia[0], ia[1], ib[0], ib[1]),
                    "手动把其中一个洞口沿墙错开（preflight 只自动删除完全被覆盖的洞口）"))


def _check_wall(el, tol, out, fix=False):
    """返回 (ok, el)。fix=True 时洞口问题夹紧/删除并降为 warning；
    部分重叠始终 error（保守）。"""
    name = el.get("name", "wall")
    length = _num(el.get("length"))
    thickness = _num(el.get("thickness"), 0.2)
    height = _num(el.get("height"), 3.0)
    if length <= tol:
        out["violations"].append(_v("error", "wall_degenerate", name,
                                    "墙段长度 %.6fm ≤ 容差 %.4fm" % (length, tol),
                                    "删除该墙段或修正 start/end"))
        return False, el
    if thickness <= tol or height <= tol:
        out["violations"].append(_v("error", "wall_degenerate", name,
                                    "墙厚/墙高非法 (t=%.4f h=%.4f)" % (thickness, height),
                                    "补正 thickness / height"))
        return False, el
    openings = el.get("openings")
    if fix:
        el = _fix_wall_openings(el, name, length, height, tol, out)
    else:
        for i, o in enumerate(openings or []):
            if not isinstance(o, dict):
                out["violations"].append(_v("error", "opening_invalid",
                                            "%s#opening%d" % (name, i),
                                            "洞口不是对象", "修正 JSON"))
                continue
            sx = _num(o.get("startX"))
            w = _num(o.get("width"))
            h = _num(o.get("height"))
            sill = _num(o.get("sill"))
            tag = "%s#opening%d" % (name, i)
            if w <= tol:
                out["violations"].append(_v("error", "opening_degenerate", tag,
                                            "洞口宽度 %.4fm 非法" % w, "删除该洞口"))
            if sx < -tol or sx + w > length + tol:
                out["violations"].append(_v("error", "opening_out_of_wall", tag,
                                            "洞口越界 startX=%.3f w=%.3f 墙长=%.3f" % (sx, w, length),
                                            "夹紧到墙段范围内（startX ∈ [0, L-w]）"))
            if sill < -tol or sill + h > height + tol:
                out["violations"].append(_v("error", "opening_out_of_height", tag,
                                            "洞口超高 sill=%.3f h=%.3f 墙高=%.3f" % (sill, h, height),
                                            "降低 sill 或 h，保证 sill+h ≤ 墙高"))
        _flag_opening_overlaps(name, openings, tol, out, "error")
    return True, el


def _check_slab(el, tol, out):
    name = el.get("name", el.get("type"))
    pts = el.get("outline") or []
    if len(pts) < 3:
        out["violations"].append(_v("error", "slab_outline_invalid", name,
                                    "outline 顶点数 %d < 3" % len(pts), "补正 outline"))
        return False
    cleaned = []
    for p in pts:
        if len(p) < 2:
            continue
        q = [_num(p[0]), _num(p[1])]
        if not cleaned or _seg_len(cleaned[-1], q) > tol:
            cleaned.append(q)
    area = _poly_area(cleaned) if len(cleaned) >= 3 else 0.0
    if area <= tol:
        out["violations"].append(_v("error", "slab_degenerate", name,
                                    "轮廓面积 %.6f m² 过小" % area, "检查 outline 是否闭合/顺序"))
        return False
    if _num(el.get("thickness"), 0.15) <= tol:
        out["violations"].append(_v("error", "slab_degenerate", name, "厚度非法", "补正 thickness"))
        return False
    return True


def _check_box(el, tol, out):
    name = el.get("name", el.get("type"))
    w = _num(el.get("width"))
    d = _num(el.get("depth"), _num(el.get("length")))
    h = _num(el.get("height"))
    if min(w, d, h) <= tol:
        out["violations"].append(_v("error", "box_degenerate", name,
                                    "尺寸非法 w=%.4f d=%.4f h=%.4f" % (w, d, h),
                                    "补正 width/depth(height)/height"))
        return False
    mn = min(w, d, h)
    if tol < mn <= SOLID_THIN_M:
        # 刻意薄层豁免：仅厚度方向薄（h≤20mm）且投影面积 ≥0.25m² 视为面层/防水/垫层等
        # 合法薄构件；宽度或深度方向薄、或投影也极小 → 疑似占位垃圾，报 warning
        thin_footprint = w <= SOLID_THIN_M or d <= SOLID_THIN_M
        intentional_layer = (h <= SOLID_THIN_M) and (w * d >= 0.25)
        if thin_footprint or not intentional_layer:
            out["violations"].append(_v("warning", "zero_area_solid", name,
                                        "柱/梁/箱最小尺寸 %.4fm ≤ %.0fmm（w=%.4f d=%.4f h=%.4f），"
                                        "疑似占位元素，Revit 布尔运算可能失败"
                                        % (mn, SOLID_THIN_M * 1000, w, d, h),
                                        "确认是薄构件（玻璃/钢板）还是误填尺寸；如是垃圾请删除"))
    bp = el.get("basePoint") or [0, 0, 0]
    z = _num(bp[2]) if len(bp) > 2 else _num(el.get("elevation"))
    if z < MIN_ELEV:
        out["violations"].append(_v("error", "negative_elevation", name,
                                    "底标高 %.3f m < %.1f m（超出埋地范围）" % (z, MIN_ELEV),
                                    "抬高 elevation / basePoint.z"))
    return True


def _fix_polyline(el, tol, out):
    name = el.get("name", "polyline2d")
    pts = el.get("points") or []
    clean = []
    for p in pts:
        if len(p) < 2:
            continue
        q = [_num(p[0]), _num(p[1])]
        if not clean or _seg_len(clean[-1], q) > tol:
            clean.append(q)
    closed = bool(el.get("closed"))
    if closed and len(clean) >= 2 and _seg_len(clean[-1], clean[0]) <= tol:
        clean.pop()
    if len(clean) < 2:
        out["violations"].append(_v("warning", "polyline_degenerate", name,
                                    "有效点不足 2 个（原 %d 点）" % len(pts), "已丢弃该元素"))
        out["fixed"] += 1
        return None
    if len(clean) != len(pts):
        out["violations"].append(_v("warning", "polyline_short_segment", name,
                                    "剔除 %d 个过短线段（容差 %.4fm）" % (len(pts) - len(clean), tol),
                                    "已自动剔除"))
        out["fixed"] += 1
        el = dict(el)
        el["points"] = clean
    return el


def _check_text(el, tol, out):
    name = el.get("name", "text2d")
    if not str(el.get("text", "")).strip():
        out["violations"].append(_v("error", "text_empty", name, "文字为空", "补正 text"))
        return False
    if _num(el.get("height"), 0.25) <= tol:
        out["violations"].append(_v("error", "text_degenerate", name, "字高非法", "补正 height"))
        return False
    return True


def _check_collinear_walls(elements, tol, out):
    """近共线且沿轴投影重叠的墙段对 → warning（不删）。
    Revit 中重叠墙会触发墙连接/重叠报错，AutoCAD 侧也会画成双线。"""
    walls = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        if str(el.get("type", "")).strip().lower() != "wall":
            continue
        length = _num(el.get("length"))
        if length <= tol:
            continue
        bp = el.get("basePoint") or [0, 0, 0]
        walls.append({
            "name": el.get("name", "wall"),
            "x": _num(bp[0]), "y": _num(bp[1]),
            "z": _num(bp[2]) if len(bp) > 2 else _num(el.get("elevation")),
            "ang": _num(el.get("angle")),
            "len": length,
            "th": _num(el.get("thickness"), 0.2),
        })
    n = len(walls)
    if n < 2:
        return
    # 角度单位启发式：|angle| 超过 2π 的样本存在 → 视为度数，否则弧度
    deg_mode = any(abs(w["ang"]) > 2.0 * math.pi + 1e-6 for w in walls)
    k = math.pi / 180.0 if deg_mode else 1.0
    min_ov = max(COLLINEAR_MIN_OVERLAP, 10 * tol)
    total, shown = 0, 0
    for i in range(n):
        wi = walls[i]
        ai = wi["ang"] * k
        ux, uy = math.cos(ai), math.sin(ai)
        for j in range(i + 1, n):
            wj = walls[j]
            if abs(wi["z"] - wj["z"]) > COLLINEAR_Z_TOL:
                continue
            aj = wj["ang"] * k
            da = (ai - aj) % math.pi
            da = min(da, math.pi - da)
            if da > COLLINEAR_ANG_RAD:
                continue
            dx, dy = wj["x"] - wi["x"], wj["y"] - wi["y"]
            perp = abs(-uy * dx + ux * dy)          # 轴线垂直距离
            if perp > 0.5 * (wi["th"] + wj["th"]) + tol:
                continue                             # 墙身未相交，是平行双墙不是共线重叠
            d = dx * ux + dy * uy                    # j 起点在 i 方向轴上的投影
            overlap = min(wi["len"], d + wj["len"]) - max(0.0, d)
            if overlap < min_ov:
                continue                             # 仅端点相接（正常 L/T 连接）
            total += 1
            if shown < COLLINEAR_MAX_REPORT:
                shown += 1
                out["violations"].append(_v(
                    "warning", "wall_collinear_overlap",
                    "%s/%s" % (wi["name"], wj["name"]),
                    "近共线墙段沿轴重叠 %.2fm（夹角差 %.1f° 轴线距 %.3fm 标高差 %.2fm），"
                    "Revit 墙连接会报错或产生残缺交接"
                    % (overlap, math.degrees(da), perp, abs(wi["z"] - wj["z"])),
                    "合并为一段、缩短其中一段，或错开轴线"))
    if total > shown:
        out["violations"].append(_v("warning", "wall_collinear_overlap", "(汇总)",
                                    "另有 %d 对共线重叠墙段未列出" % (total - shown),
                                    "见前 %d 条明细" % shown))


def _z_bounds(request):
    """由顶层 levels[{name,elevation}] 推建筑竖向区间 [lo, hi]（米）。
    无 levels 或数值疑似非米单位时返回 None（跳过该项检查）。"""
    levels = request.get("levels")
    if not isinstance(levels, list) or not levels:
        return None
    elevs = []
    for lv in levels:
        if isinstance(lv, dict):
            elevs.append(_num(lv.get("elevation")))
    if not elevs:
        return None
    if any(abs(e) > Z_LEVEL_SUSPECT_M for e in elevs):
        return None  # 疑似 mm 混入，宁可不检查也不误报
    elevs = sorted(set(elevs))
    diffs = [b - a for a, b in zip(elevs, elevs[1:]) if b - a > 0.05]
    story = sorted(diffs)[len(diffs) // 2] if diffs else 3.0
    lo = elevs[0] - max(2.0, 0.5 * story)      # 最低标高再留 2m（垫层/基础）
    hi = elevs[-1] + 2.0 * story + 1.0          # 最高层 + 两层净高 + 余量（屋顶构架/女儿墙）
    return lo, hi


def _check_z_range(el, bounds, tol, out):
    """构件竖向区间超出 levels 推断范围 → warning。"""
    t = str(el.get("type", "")).strip().lower()
    if t not in ("wall", "column", "beam", "box", "floor", "roof"):
        return
    lo, hi = bounds
    name = el.get("name", t)
    if t in ("floor", "roof"):
        elev = _num(el.get("elevation"))
        zb = elev - _num(el.get("thickness"), 0.15)
        zt = elev
    else:
        bp = el.get("basePoint") or [0, 0, 0]
        zb = _num(bp[2]) if len(bp) > 2 else _num(el.get("elevation"))
        h = _num(el.get("height"))
        zt = zb + h if h > 0 else zb
    eps = tol
    if zb < lo - eps:
        rule = "slab_below_grade" if t in ("floor", "roof") else "element_z_out_of_range"
        out["violations"].append(_v("warning", rule, name,
                                    "底标高 %.2fm 低于 levels 推断下限 %.2fm（区间 [%.2f, %.2f]m）"
                                    % (zb, lo, zb, zt),
                                    "核对 levels/构件 elevation：可能在基础底 -5m 以下或漏配楼层"))
    elif zt > hi + eps:
        out["violations"].append(_v("warning", "element_z_out_of_range", name,
                                    "顶标高 %.2fm 远超 levels 推断建筑总高上限 %.2fm（区间 [%.2f, %.2f]m）"
                                    % (zt, hi, zb, zt),
                                    "构件可能放错楼层标高，核对 elevation/basePoint.z"))


def check_request(request, target="both", fix=True):
    """返回 (fixed_request, report)。report.summary.passed 表示无未修复 error。"""
    tol = TOL["revit"] if target == "revit" else TOL["cad"]
    if target == "both":
        tol = max(TOL["cad"], TOL["revit"])

    out = {"violations": [], "fixed": 0}
    elements = request.get("elements") or []
    kept = []
    seen = {}
    stats = {}

    for idx, el in enumerate(elements):
        if not isinstance(el, dict):
            out["violations"].append(_v("error", "element_invalid", "#%d" % idx,
                                        "元素不是对象", "修正 JSON"))
            kept.append(el)
            continue
        t = str(el.get("type", "")).strip().lower()
        name = el.get("name") or t or ("#%d" % idx)
        if t not in VALID_TYPES:
            out["violations"].append(_v("error", "unknown_type", name,
                                        "未知元素类型 '%s'" % t,
                                        "改用协议支持的类型（见 docs/protocol.md）"))
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1 and t in DEDUPE_TYPES:
            if fix:
                new_name = "%s~%d" % (name, seen[name])
                el = dict(el)
                el["name"] = new_name
                out["violations"].append(_v("warning", "duplicate_renamed", name,
                                            "重名构件已改名为 %s" % new_name, "已自动重命名"))
                out["fixed"] += 1
            else:
                out["violations"].append(_v("warning", "duplicate_name", name,
                                            "构件名重复（第 %d 次）" % seen[name],
                                            "建议保证 name 唯一，便于结果对账"))

        mark = len(out["violations"])
        ok = True
        if t == "wall":
            ok, el = _check_wall(el, tol, out, fix=fix)
        elif t in ("floor", "roof"):
            ok = _check_slab(el, tol, out)
        elif t in ("column", "beam", "box"):
            ok = _check_box(el, tol, out)
        elif t == "text2d":
            ok = _check_text(el, tol, out)
        elif t == "polyline2d":
            if fix:
                fixed = _fix_polyline(el, tol, out)
                if fixed is None:
                    ok = False
                else:
                    el = fixed
            elif len(el.get("points") or []) < 2:
                ok = False
                out["violations"].append(_v("error", "polyline_degenerate", name,
                                            "有效点不足 2 个", "补正 points"))

        if not ok:
            if fix and t in ("wall", "column", "beam", "box", "polyline2d"):
                # 自动修复：把"错误"降级为"已丢弃"警告，避免修复后仍判失败
                dropped = out["violations"][mark:] or []
                out["violations"] = out["violations"][:mark]
                reason = dropped[0]["message"] if dropped else "退化几何"
                out["violations"].append(_v("warning", "dropped", name,
                                            "已丢弃退化元素（type=%s）：%s" % (t, reason),
                                            "由 preflight 自动修复"))
                out["fixed"] += 1
            continue
        kept.append(el)
        stats[t] = stats.get(t, 0) + 1

    # --- 跨元素检查（只读、只产 warning，不改 kept 内容）---
    _check_collinear_walls(kept, tol, out)
    zbounds = _z_bounds(request)
    if zbounds is not None:
        for el in kept:
            if isinstance(el, dict):
                _check_z_range(el, zbounds, tol, out)

    errors = [v for v in out["violations"] if v["severity"] == "error"]
    warnings = [v for v in out["violations"] if v["severity"] == "warning"]
    by_rule = {}
    for v in out["violations"]:
        by_rule[v["rule"]] = by_rule.get(v["rule"], 0) + 1
    report = {
        "summary": {
            "passed": len(errors) == 0,
            "target": target,
            "toleranceM": tol,
            "total": len(out["violations"]),
            "errors": len(errors),
            "warnings": len(warnings),
            "fixed": out["fixed"],
            "elementsIn": len(elements),
            "elementsOut": len(kept),
            "byType": stats,
            "byRule": by_rule,
            "levelsChecked": zbounds is not None,
        },
        "violations": out["violations"],
    }
    fixed_request = dict(request)
    fixed_request["elements"] = kept
    return fixed_request, report


def _atomic_write_json(path, obj):
    """原子写：同目录唯一临时文件 → flush → os.replace；失败时清理 tmp，不留半截文件。"""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description="request.json 发单前几何校验")
    p.add_argument("request", help="request.json 路径")
    p.add_argument("--target", choices=("both", "cad", "revit"), default="both")
    p.add_argument("--fix", action="store_true", help="自动修复可修复问题（默认开）")
    p.add_argument("--no-fix", action="store_true", help="只报告不修复")
    p.add_argument("--out", default="", help="报告输出路径（默认同目录 preflight.json）")
    args = p.parse_args(argv)

    try:
        with open(args.request, "r", encoding="utf-8-sig") as f:
            req = json.load(f)
    except OSError as e:
        sys.stderr.write("[preflight] 无法读取请求文件 %s: %s\n" % (args.request, e))
        return 1
    except ValueError as e:
        sys.stderr.write("[preflight] 请求文件不是合法 JSON %s: %s\n" % (args.request, e))
        return 1

    fixed, rep = check_request(req, target=args.target, fix=not args.no_fix)
    out_path = args.out
    if not out_path:
        out_path = os.path.join(os.path.dirname(os.path.abspath(args.request)), "preflight.json")
    try:
        _atomic_write_json(out_path, rep)
    except OSError as e:
        sys.stderr.write("[preflight] 无法写入报告 %s: %s\n" % (out_path, e))
        return 1

    s = rep["summary"]
    print("[preflight] %s target=%s tol=%.4fm elements %d -> %d，修复 %d，error %d，warning %d"
          % ("通过" if s["passed"] else "未通过", s["target"], s["toleranceM"],
             s["elementsIn"], s["elementsOut"], s["fixed"], s["errors"], s["warnings"]))
    notable = ("dropped", "polyline_short_segment", "opening_overlap",
               "opening_out_of_wall", "opening_height_over_wall",
               "wall_collinear_overlap", "slab_below_grade",
               "element_z_out_of_range", "zero_area_solid")
    for v in rep["violations"]:
        if v["severity"] == "error" or v["rule"] in notable:
            print("  [%s] %s %s: %s" % (v["severity"], v["rule"], v["element"], v["message"]))
    print("  报告: %s" % out_path)
    return 0 if s["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
