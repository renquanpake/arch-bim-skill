#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
annotations.py — 模型空间标注符号（标注包 v1）
=============================================

给 brief_to_cad 管线补「标高符号 / 轴号圆圈 / 房间三行标签 / 门窗编号 /
剖切符号」，配方来源 docs/drawing-standards.md §5（GB/T 50001-2017）。

坐标约定（关键）：
  * 所有尺寸用 **纸面 mm** 书写，经 `_mm(mm, scale)` 换算成**模型米**：
    `_mm(3, 100) = 3*0.001*100 = 0.3 m`，即 1:100 打印时正好 3mm 纸面。
  * 产出元素只含 polyline2d / text2d，字段与 build_grid_el 现有输出一致
    （polyline2d: points + 可选 closed/layer；text2d: text/insertPoint/
    height/rotation/layer）。模型空间真实米，函数内不再 ÷100。
  * 图层名按任务约定：A-ELEV / A-GRID / A-TEXT / A-TAG / A-SECT
    （drawing-standards §7 CTB 映射的模型空间对应）。

依赖：Python 3.8+，仅标准库（math）。不启动 Revit/AutoCAD。
"""

import math


def _mm(mm, scale=100):
    """纸面 mm → 模型米（1:scale）。1:100 时 3mm → 0.3m，打印回纸面 3mm。"""
    return mm * 0.001 * scale


def _pt(x, y):
    return [round(x, 6), round(y, 6)]


def _fmt_elev(v):
    """标高数字：米、三位小数；0 写 ±0.000，负值带 -。"""
    if abs(v) < 1e-6:
        return "±0.000"
    return "%.3f" % v


def _est_width(s, h):
    """文本宽度粗估（CJK≈1.0字高、其余≈0.55字高），用于居中偏移。"""
    w = 0.0
    for ch in str(s):
        w += h * (1.0 if ord(ch) >= 0x2000 else 0.55)
    return w


def _text(name, s, x, y, h, layer, rot=0.0):
    el = {"type": "text2d", "name": name, "text": str(s),
          "insertPoint": _pt(x, y), "height": round(h, 6), "layer": layer}
    if rot:
        el["rotation"] = round(rot, 6)
    return el


# ---------------------------------------------------------------- 标高符号
def elev_symbol(x, y, value, scale=100, topmost=False, layer="A-ELEV"):
    """标高符号：尖角指下的等腰直角三角形（高3mm、顶边宽6mm）+
    横线（左顶点向右总长17mm）+ 三位小数数字（高3.5mm，横线上方）。
    (x, y) 为尖角顶点（标高位置）。topmost=最高层平面 → 再画内缩双线三角。
    配方：drawing-standards §5.1。"""
    h = _mm(3, scale)                       # 三角形高
    hw = _mm(3, scale)                      # 顶边半宽（顶边全宽 6mm）
    pts = [[x, y], [x - hw, y + h], [x + hw, y + h]]
    els = [{"type": "polyline2d", "name": "ELEV-T", "closed": True,
            "points": [_pt(*p) for p in pts], "layer": layer}]
    if topmost:
        # 内缩三角：顶点上移1.1mm、底两角内缩至 ±1.9mm（§5.1 双线形式）
        pts2 = [[x, y + _mm(1.1, scale)],
                [x - _mm(1.9, scale), y + h],
                [x + _mm(1.9, scale), y + h]]
        els.append({"type": "polyline2d", "name": "ELEV-T2", "closed": True,
                    "points": [_pt(*p) for p in pts2], "layer": layer})
    # 横线：自左顶点 (x-hw, y+h) 向右总长 17mm
    x_left = x - hw
    x_right = x_left + _mm(17, scale)
    els.append({"type": "polyline2d", "name": "ELEV-L",
                "points": [_pt(x_left, y + h), _pt(x_right, y + h)],
                "layer": layer})
    # 数字：高3.5mm，注于横线上方（三角形右侧）
    els.append(_text("ELEV-N", _fmt_elev(value),
                     x + _mm(1.5, scale), y + h + _mm(0.8, scale),
                     _mm(3.5, scale), layer))
    return els


# ---------------------------------------------------------------- 轴号圆圈
def axis_bubble(cx, cy, label, scale=100, layer="A-GRID"):
    """轴号气泡：φ8mm 圆（16 边形近似，closed polyline2d）+ 居中编号
    （字高 5mm）。配方：drawing-standards §5.2。"""
    r = _mm(4, scale)
    n = 16
    pts = [_pt(cx + r * math.cos(2 * math.pi * i / n),
               cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    circle = {"type": "polyline2d", "name": "AXIS-C-%s" % label,
              "closed": True, "points": pts, "layer": layer}
    h = _mm(5, scale)
    # 居中粗对齐：x 左移 len×1.5mm、y 下移 2.5mm（§5.2 惯例）
    text = _text("AXIS-T-%s" % label, label,
                 cx - len(str(label)) * _mm(1.5, scale),
                 cy - _mm(2.5, scale), h, layer)
    return [circle, text]


# ---------------------------------------------------------------- 房间标签
def room_label(name, area_m2, elev, cx, cy, scale=100, layer="A-TEXT"):
    """房间三行标签，(cx, cy) 质心居中堆叠：
    名称（高5mm）/ "N=xx.x m²"（高3mm）/ "▽x.xxx"（高3mm），行距 3.5mm。
    配方：drawing-standards §5.3。area_m2/elev 传 None 时跳过对应行。"""
    gap = _mm(3.5, scale)

    def _cx(s, h):  # 水平居中（宽≈0.62字高×字符数，§5.3）
        return cx - h * 0.62 * len(str(s)) / 2.0

    els = [_text("RM-N", name, _cx(name, _mm(5, scale)), cy + gap,
                 _mm(5, scale), layer)]
    if area_m2 is not None:
        t = "N=%.1f m²" % area_m2
        els.append(_text("RM-A", t, _cx(t, _mm(3, scale)), cy,
                         _mm(3, scale), layer))
    if elev is not None:
        t = "▽" + _fmt_elev(elev)
        els.append(_text("RM-E", t, _cx(t, _mm(3, scale)), cy - gap,
                         _mm(3, scale), layer))
    return els


# ---------------------------------------------------------------- 门窗编号
def door_window_code(prefix, width_mm, height_mm):
    """门窗编号：类别代号 + 四位分米码（前两位洞宽、后两位洞高）。
    门 M / 窗 C：900×2100 → "M0921"，1500×1500 → "C1515"。
    （§1.10 / §5.5；width_mm/height_mm 单位 mm。）"""
    return "%s%02d%02d" % (prefix, int(round(float(width_mm) / 100.0)),
                           int(round(float(height_mm) / 100.0)))


def door_window_label(el_wall, opening, code, scale=100, layer="A-TAG"):
    """把编号文字沿墙法线方向外移到洞口中心外侧（§5.5）。
    el_wall: {"start":[x,y], "end":[x,y], "thickness":米}，模型米坐标；
    opening: {"startX":米, "width":米}，沿 start→end 方向（模型米）。
    返回 [text2d]，字高 3.5mm 水平书写。"""
    st = el_wall.get("start", [0.0, 0.0])
    en = el_wall.get("end", [0.0, 0.0])
    dx, dy = en[0] - st[0], en[1] - st[1]
    L = math.hypot(dx, dy) or 1e-9
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    sx = float(opening.get("startX", 0.0))
    ow = float(opening.get("width", 0.0))
    cxm = st[0] + ux * (sx + ow / 2.0)
    cym = st[1] + uy * (sx + ow / 2.0)
    h = _mm(3.5, scale)
    # 外移：墙半厚 + 纸面8mm（1:100 → 0.8m），防压墙线
    off = float(el_wall.get("thickness", 0.24)) / 2.0 + _mm(8, scale)
    x, y = cxm + nx * off, cym + ny * off
    return [_text("DW-TAG", code, x - _est_width(code, h) / 2.0, y, h, layer)]


# ---------------------------------------------------------------- 剖切符号
def _thick_line(x0, y0, x1, y1, w, name, layer):
    """细长矩形（closed polyline2d）模拟粗实线——polyline2d 无宽度字段，
    几何加粗兜底（§5.6 / §7）。"""
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy) or 1e-9
    nx, ny = -dy / L * w / 2.0, dx / L * w / 2.0
    return {"type": "polyline2d", "name": name, "closed": True, "layer": layer,
            "points": [_pt(x0 + nx, y0 + ny), _pt(x1 + nx, y1 + ny),
                       _pt(x1 - nx, y1 - ny), _pt(x0 - nx, y0 - ny)]}


def section_mark(p0, p1, number, scale=100, layer="A-SECT"):
    """剖切符号三件套（§5.6）：
      1. 剖切位置线 p0→p1：粗实线 b（0.7mm，细长矩形模拟）；
      2. 两端投影方向线：垂直于剖切线、同侧，中实线 0.5b≈0.35mm（单线），
         长 5mm；
      3. 编号：字高 5mm，注于方向线末端外侧。
    p0/p1 为模型米坐标 [x, y]。"""
    x0, y0 = p0[0], p0[1]
    x1, y1 = p1[0], p1[1]
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy) or 1e-9
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    els = [_thick_line(x0, y0, x1, y1, _mm(0.7, scale), "SEC-P", layer)]
    dl = _mm(5, scale)          # 方向线长
    h = _mm(5, scale)           # 编号字高
    for i, (px, py) in enumerate(((x0, y0), (x1, y1))):
        ex, ey = px + nx * dl, py + ny * dl
        els.append({"type": "polyline2d", "name": "SEC-D%d" % i,
                    "points": [_pt(px, py), _pt(ex, ey)], "layer": layer})
        tx = ex + nx * _mm(4, scale) - _est_width(number, h) / 2.0
        ty = ey + ny * _mm(4, scale) - _mm(1.75, scale)
        els.append(_text("SEC-N%d" % i, number, tx, ty, h, layer))
    return els


# 字母轴编号跳过 I/O/Z（易混 1/0/2，§5.2 通行规定）
AXIS_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXY"


def axis_letter(i):
    """第 i 根字母轴编号（0→A），跳过 I/O/Z；超界回退 A1、A2…。"""
    if i < len(AXIS_LETTERS):
        return AXIS_LETTERS[i]
    return "A%d" % (i + 1)


if __name__ == "__main__":
    # 自测：打印各符号的元素构成与纸面尺寸核对
    def _show(tag, els):
        print("%-18s %d els" % (tag, len(els)),
              sorted({e["type"] for e in els}),
              sorted({e.get("layer") for e in els}))
    _show("elev_symbol", elev_symbol(0, 0, 3.0))
    _show("elev(topmost)", elev_symbol(0, 0, 0.0, topmost=True))
    _show("axis_bubble", axis_bubble(0, 0, "A"))
    _show("room_label", room_label("客厅", 23.456, 3.0, 5, 5))
    _show("dw_code", [_text("t", door_window_code("M", 900, 2100), 0, 0, 0.35, "A-TAG")])
    _show("dw_label", door_window_label(
        {"start": [0, 0], "end": [6, 0], "thickness": 0.24},
        {"startX": 1.2, "width": 0.9}, "M0921"))
    _show("section_mark", section_mark([6, 2], [6, 8], 1))
    assert door_window_code("M", 900, 2100) == "M0921"
    assert door_window_code("C", 1500, 1500) == "C1515"
    assert _mm(3, 100) == 0.3 and _mm(3) == 0.3
    print("OK: 3mm@1:100 ->", _mm(3, 100), "m; codes M0921/C1515")


def _wall_frame(el_wall):
    st = el_wall.get("start", [0.0, 0.0]); en = el_wall.get("end", [0.0, 0.0])
    dx, dy = en[0] - st[0], en[1] - st[1]
    L = math.hypot(dx, dy) or 1e-9
    return st, dx / L, dy / L, -dy / L, dx / L, L


def door_swing(el_wall, opening, scale=100, radius=10, layer="A-DOOR"):
    """门扇开启线 + 1/4 圆弧（铰链在洞口起点，向墙法线一侧开）。返回 polyline2d 列表。"""
    st, ux, uy, nx, ny, L = _wall_frame(el_wall)
    sx = float(opening.get("startX", 0.0)); ow = float(opening.get("width", 0.0))
    if ow <= 0.01:
        return []
    hx = st[0] + ux * sx; hy = st[1] + uy * sx          # 铰链
    ex = st[0] + ux * (sx + ow); ey = st[1] + uy * (sx + ow)   # 洞口另一端（闭合位）
    openx, openy = hx + nx * ow, hy + ny * ow          # 开启 90° 后门扇端
    leaf = {"type": "polyline2d", "name": "DOOR-SWING", "layer": layer,
            "points": [_pt(hx, hy), _pt(openx, openy)]}
    # 1/4 弧：从闭合方向(沿墙 +u) 旋到开启方向(+n)
    a0 = math.atan2(uy, ux); a1 = a0 + math.pi / 2.0
    arc = []
    for k in range(radius + 1):
        a = a0 + (a1 - a0) * k / radius
        arc.append(_pt(hx + ow * math.cos(a), hy + ow * math.sin(a)))
    arc_el = {"type": "polyline2d", "name": "DOOR-ARC", "layer": layer, "points": arc}
    return [leaf, arc_el]


def window_lines(el_wall, opening, scale=100, layer="A-WINDOW"):
    """窗三线：洞口内沿墙方向画 3 条平行细线 + 两端 jamb 短刻度。"""
    st, ux, uy, nx, ny, L = _wall_frame(el_wall)
    sx = float(opening.get("startX", 0.0)); ow = float(opening.get("width", 0.0))
    if ow <= 0.01:
        return []
    t = float(el_wall.get("thickness", 0.24))
    fr = min(t / 2.0 - 0.01, _mm(100, scale))
    if fr <= 0:
        fr = t / 3.0
    els = []
    for off in (-fr, 0.0, fr):
        p0 = (st[0] + ux * sx + nx * off, st[1] + uy * sx + ny * off)
        p1 = (st[0] + ux * (sx + ow) + nx * off, st[1] + uy * (sx + ow) + ny * off)
        els.append({"type": "polyline2d", "name": "WIN-LINE", "layer": layer,
                    "points": [_pt(p0[0], p0[1]), _pt(p1[0], p1[1])]})
    for edge in (sx, sx + ow):
        for sgn in (-1, 1):
            p0 = (st[0] + ux * edge + nx * (t / 2) * sgn,
                  st[1] + uy * edge + ny * (t / 2) * sgn)
            p1 = (st[0] + ux * edge + nx * fr * sgn,
                  st[1] + uy * edge + ny * fr * sgn)
            els.append({"type": "polyline2d", "name": "WIN-JAMB", "layer": layer,
                        "points": [_pt(p0[0], p0[1]), _pt(p1[0], p1[1])]})
    return els


def index_symbol(cx, cy, detail_no, sheet_no="", scale=100, layer="A-ANNO"):
    """索引符号：细实线圆 φ10 + 水平直径线 + 上详图号/下图纸号。"""
    r = _mm(5, scale)
    pts = [_pt(cx + r * math.cos(2 * math.pi * k / 24),
               cy + r * math.sin(2 * math.pi * k / 24)) for k in range(24)]
    els = [{"type": "polyline2d", "name": "IDX-C", "closed": True, "layer": layer, "points": pts},
           {"type": "polyline2d", "name": "IDX-L", "layer": layer,
            "points": [_pt(cx - r, cy), _pt(cx + r, cy)]},
           _text("IDX-TOP", str(detail_no), cx - _est_width(str(detail_no), _mm(3, scale)) / 2.0,
                 cy + _mm(0.5, scale), _mm(3, scale), layer)]
    low = str(sheet_no) if sheet_no != "" else "—"
    els.append(_text("IDX-BOT", low, cx - _est_width(low, _mm(3, scale)) / 2.0,
                     cy - _mm(4.5, scale), _mm(3, scale), layer))
    return els
