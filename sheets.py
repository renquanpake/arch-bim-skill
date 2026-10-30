# -*- coding: utf-8 -*-
"""sheets.py — 施工图套图：图框(A1/A2/A3) + 标题栏(图签) + 真比例排版。

规格来自 docs/cad-detailing-guide.md §6：
  图号 A-101 平面 / A-201 立面 / A-301 剖面；图签：工程名/图名/图号/比例/日期/设计阶段。
纸面坐标一律用"米"表示毫米（A1 = 0.841×0.594，1mm = 0.001），视图按 1/scale 真缩放
后放入图框，因此 DWG 出图即可按比例打印。元素仅用 polyline2d / text2d，
CAD 与 Revit 插件都能画。

docs/drawing-standards.md §5.4/5.7 纸面符号（单位直接写米，φ24mm = 0.024）：
  north_symbol  指北针（φ24 圆 + 三角针 + 「北」，rot_deg 绕圆心旋转）
  caption       图名粗下划线 + 比例文字
  layout_sheets 对「平面」类视图自动放指北针 + 图名下划线（§8 落地顺序第 2 条）。
旋转角度来源：meta["northAngle"]（真北相对图面北，顺时针为正，度；缺省 0）。
"""

import datetime
import math
from typing import List, Dict, Tuple

SHEETS = {"A1": (0.841, 0.594), "A2": (0.594, 0.420), "A3": (0.420, 0.297)}
MARGIN = 0.025          # 图框留边
TITLE_W, TITLE_H = 0.22, 0.07   # 标题栏尺寸
CS_W = 0.10                     # 会签栏宽度
PLINE = "A-SHEET"
PAPER_K = 1000.0        # 米 → 纸面毫米的除数（视图坐标按 /scale 缩放）


def _rect(x0, y0, x1, y1, name, layer=PLINE, closed=True):
    return {"type": "polyline2d", "name": name, "closed": closed, "layer": layer,
            "points": [[round(x0, 5), round(y0, 5)], [round(x1, 5), round(y0, 5)],
                       [round(x1, 5), round(y1, 5)], [round(x0, 5), round(y1, 5)]]}


def _line(x0, y0, x1, y1, name, layer=PLINE):
    return {"type": "polyline2d", "name": name, "layer": layer,
            "points": [[round(x0, 5), round(y0, 5)], [round(x1, 5), round(y1, 5)]]}


def _text(x, y, s, name, h=0.006, rot=0.0, layer="A-TEXT"):
    return {"type": "text2d", "name": name, "text": s, "insertPoint": [round(x, 5), round(y, 5)],
            "height": round(h, 6), "rotation": round(rot, 6), "layer": layer}


def _bbox(els):
    xs = [p[0] for e in els if e["type"] == "polyline2d" for p in e.get("points", [])]
    ys = [p[1] for e in els if e["type"] == "polyline2d" for p in e.get("points", [])]
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _fit(els, scale_k, bx, by, w, h):
    """把一组模型空间(米)元素缩放 scale_k 并居中放入框 (bx,by,w,h)。"""
    x0, y0, x1, y1 = _bbox(els)
    vw, vh = (x1 - x0) * scale_k, (y1 - y0) * scale_k
    pad = 0.02
    fit = 1.0
    if vw > w - 2 * pad or vh > h - 2 * pad:
        fit = min((w - 2 * pad) / (vw + 1e-9), (h - 2 * pad) / (vh + 1e-9))
    k = scale_k * fit
    ox = bx + (w - vw * fit) / 2.0 - x0 * k
    oy = by + (h - vh * fit) / 2.0 - y0 * k
    out = []
    for e in els:
        if e["type"] == "polyline2d":
            e = dict(e)
            e["points"] = [[round(px * k + ox, 5), round(py * k + oy, 5)] for px, py in e["points"]]
        elif e["type"] == "text2d":
            e = dict(e)
            ip = e["insertPoint"]
            e["insertPoint"] = [round(ip[0] * k + ox, 5), round(ip[1] * k + oy, 5)]
            e["height"] = round(max(0.0025, e.get("height", 0.25) * k), 6)
        else:
            continue
        out.append(e)
    return out


# ------------------------------------------------- 纸面符号（drawing-standards §5.4/5.7）

PAPER = 0.001  # 纸面 1mm 对应的"米"数


def _poly_circle(cx, cy, r, n=24, name="CIRCLE", layer=PLINE):
    """正 n 边形近似圆（16~24 边，closed polyline2d）。§5.2/5.4 通用件。"""
    n = max(8, int(n))
    pts = [[round(cx + r * math.cos(2.0 * math.pi * i / n), 5),
            round(cy + r * math.sin(2.0 * math.pi * i / n), 5)] for i in range(n)]
    return {"type": "polyline2d", "name": name, "closed": True, "points": pts, "layer": layer}


def _thick_line(x0, y0, x1, y1, w=0.001, name="TL", layer=PLINE):
    """细长矩形（closed polyline2d）模拟粗实线——polyline2d 无宽度字段，
    几何加粗兜底（§5.6/5.7；打印侧配合 CTB 图层线宽，§7）。"""
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy) or 1e-9
    nx, ny = -dy / L * w / 2.0, dx / L * w / 2.0
    return {"type": "polyline2d", "name": name, "closed": True, "layer": layer,
            "points": [[round(x0 + nx, 5), round(y0 + ny, 5)],
                       [round(x1 + nx, 5), round(y1 + ny, 5)],
                       [round(x1 - nx, 5), round(y1 - ny, 5)],
                       [round(x0 - nx, 5), round(y0 - ny, 5)]]}


def _rot(p, cx, cy, rad):
    """点 p 绕 (cx, cy) 旋转 rad（CAD rotation 约定：逆时针为正）。"""
    if not rad:
        return [p[0], p[1]]
    c, s = math.cos(rad), math.sin(rad)
    dx, dy = p[0] - cx, p[1] - cy
    return [cx + dx * c - dy * s, cy + dx * s + dy * c]


def _text_w(s, h):
    """文本宽度粗估（CJK≈1.0 字高、其余≈0.55 字高），同 annotations._est_width 口径。"""
    return sum(h * (1.0 if ord(ch) >= 0x2000 else 0.55) for ch in str(s))


def north_symbol(cx, cy, r=0.012, rot_deg=0.0, layer="A-NORTH"):
    """指北针（§5.4）：φ24 圆（24 边形 closed）+ 等腰三角针（针尖出圆外 ≈4mm 指北）
    + 「北」字（高 6mm，针尖上方）。纸面坐标（米）。
    rot_deg：整套绕 (cx, cy) 旋转，逆时针为正（CAD 约定）——真北偏图面北顺时针 X°
    时传 -X（meta["northAngle"] 为顺时针正值，调用侧取负换算，见 layout_sheets）。
    「北」字位置随针旋转、字形保持水平可读。小图幅 φ20 传 r=0.010。"""
    ext = r / 3.0        # 针尖出圆外（r=0.012 → 0.004，§1.8 的 3~5mm 取中）
    half_w = 0.15 * r    # 针底半宽（→ ±0.0018）
    tail = 0.5 * r       # 针底距圆心（→ 0.006）
    a = math.radians(rot_deg or 0.0)
    p_tip = _rot([cx, cy + r + ext], cx, cy, a)
    p_l = _rot([cx - half_w, cy - tail], cx, cy, a)
    p_r = _rot([cx + half_w, cy - tail], cx, cy, a)
    p_t = _rot([cx, cy + r + ext + 2.0 * PAPER], cx, cy, a)  # 「北」基线：针尖上 2mm
    els = [_poly_circle(cx, cy, r, n=24, name="NORTH-C", layer=layer),
           {"type": "polyline2d", "name": "NORTH-N", "closed": True, "layer": layer,
            "points": [[round(q[0], 5), round(q[1], 5)] for q in (p_tip, p_l, p_r)]}]
    els.append(_text(p_t[0] - 0.5 * 0.006, p_t[1], "北", "NORTH-T", 0.006, layer=layer))
    return els


def caption(x, y, title, scale_txt="", name="CAP", layer="A-TEXT"):
    """图名下划线 + 比例（§5.7）：图名 6mm 于 (x, y)（基线左对齐）；下方 3.5mm 粗
    下划线（1mm 几何加粗矩形，长=字宽+4mm，§1.3「线长=字宽」）；右侧「比例 1:100」
    3.5mm（字高比图名小 1~2 号，§1.9）。scale_txt 为空则不注比例。返回元素列表。"""
    tw = _text_w(title, 0.006) + 0.004
    els = [_text(x, y, title, name, 0.006, layer=layer),
           _thick_line(x - 0.002, y - 0.0035, x - 0.002 + tw, y - 0.0035,
                       w=0.001, name=name + "-UL", layer=PLINE)]
    if scale_txt:
        els.append(_text(x - 0.002 + tw + 0.006, y - 0.0025,
                         "比例 " + str(scale_txt), name + "-S", 0.0035, layer=layer))
    return els


# ------------------------------------------------- 图纸角落表格（面积表/工程量表等）

TABLE_ROW_H = 0.008      # 默认行高（纸面 8mm）
TABLE_TEXT_H = 0.004     # 单元格字高 4mm
TABLE_PAD = 0.001        # 单元格左内边距 1mm
TABLE_HEADER_K = 1.25    # 表头行高 = 1.25 × row_h（略高）
TABLE_TITLE_SLOT = 0.008  # 表名行占位（字高 5mm + 间距）


def table(x, y, header, rows, col_widths, row_h=TABLE_ROW_H,
          layer="A-SHEET", name="TBL"):
    """通用表格绘制器（纸面坐标，米；1mm=0.001）。(x, y)=表格左下角。
    header: [str,...]；rows: [[str,...],...]；col_widths: 各列宽(米)。
    画外框 + 表头分隔线 + 逐行单元格文字（左对齐、字高 4mm、内边距 1mm），
    表头行高 = 1.25×row_h。空文字单元格跳过。
    返回 polyline2d(layer=layer) + text2d(A-TEXT) 元素列表。"""
    els = []
    if not col_widths:
        return els
    ncol = len(col_widths)
    W = float(sum(col_widths))
    hh = row_h * TABLE_HEADER_K
    n = max(0, len(rows or []))
    H = hh + n * row_h
    if W <= 0 or H <= 0:
        return els
    xs = [x]
    for w in col_widths:
        xs.append(xs[-1] + float(w))
    els.append(_rect(x, y, x + W, y + H, name + "-FRAME", layer))
    if n:
        els.append(_line(x, y + H - hh, x + W, y + H - hh, name + "-HSEP", layer))
    for c in range(1, ncol):                     # 列分隔线（左右边即外框，不重复）
        els.append(_line(xs[c], y, xs[c], y + H, name + "-V%d" % c, layer))
    th = TABLE_TEXT_H
    for c, txt in enumerate((header or [])[:ncol]):
        if str(txt):
            els.append(_text(xs[c] + TABLE_PAD, y + H - hh + (hh - th) / 2.0,
                             str(txt), name + "-HD%d" % c, th, layer="A-TEXT"))
    for r, row in enumerate(rows or []):
        yb = y + H - hh - (r + 1) * row_h        # 本行底边
        for c, txt in enumerate((row or [])[:ncol]):
            if str(txt):
                els.append(_text(xs[c] + TABLE_PAD, yb + (row_h - th) / 2.0,
                                 str(txt), name + "-R%dC%d" % (r, c), th,
                                 layer="A-TEXT"))
    return els


def csv_to_table(csv_text, num_cols):
    """把 schedules.py 产出的 CSV 文本解析为 (header, rows)。
    处理 UTF-8-BOM 与 CRLF；支持带引号单元格（"a,b"、"" 转义）；
    按 num_cols 补齐/截断每行列数（num_cols<=0 时按首行列数）。空行跳过。"""
    if not csv_text:
        return [], []
    if csv_text.startswith("\ufeff"):
        csv_text = csv_text[1:]

    def split_line(line):
        cells, cur, quoted = [], [], False
        i = 0
        while i < len(line):
            ch = line[i]
            if quoted:
                if ch == '"':
                    if i + 1 < len(line) and line[i + 1] == '"':
                        cur.append('"')
                        i += 1
                    else:
                        quoted = False
                else:
                    cur.append(ch)
            elif ch == '"':
                quoted = True
            elif ch == ",":
                cells.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
            i += 1
        cells.append("".join(cur))
        return cells

    grid = []
    for ln in csv_text.splitlines():
        if ln.strip() == "":
            continue
        grid.append(split_line(ln))
    if not grid:
        return [], []
    ncol = int(num_cols) if num_cols and int(num_cols) > 0 else len(grid[0])
    out = []
    for row in grid:
        row = [c.strip() for c in row[:ncol]]
        if len(row) < ncol:
            row += [""] * (ncol - len(row))
        out.append(row)
    return out[0], out[1:]


def _place_tables(base_x, tables, y0=MARGIN * 0.5, limit=TITLE_H + MARGIN):
    """在图纸左下角（标题栏左侧，x=base_x+MARGIN）自下向上堆叠表格。
    tables: [(标题, csv_text, num_cols, 列宽表), ...]；标题画在各自表格上方。
    总高超出 limit（视图区底边 = TITLE_H + MARGIN）时压缩行高（≥5mm），
    表特别多仍放不下则允许向上出界——由调用方减行/换图幅。"""
    items = []
    for item in (tables or []):
        title, csv_text, num_cols, col_widths = (list(item) + [None] * 4)[:4]
        header, rows = csv_to_table(csv_text or "", num_cols)
        if header and col_widths:
            items.append((title, header, rows, list(col_widths)))
    if not items:
        return []
    rh = TABLE_ROW_H
    body_k = sum(TABLE_HEADER_K + len(rows) for _, _, rows, _ in items)
    avail = max(0.0, limit - y0 - TABLE_TITLE_SLOT * len(items))
    if body_k * rh > avail:                      # 放不下→压行高
        rh = max(0.005, avail / body_k)
    els, y = [], y0
    for ti, (title, header, rows, cw) in enumerate(items):
        h = (TABLE_HEADER_K + len(rows)) * rh
        els.extend(table(base_x + MARGIN, y, header, rows, cw,
                         row_h=rh, name="SHT-TBL%d" % ti))
        if title:
            els.append(_text(base_x + MARGIN, y + h + 0.002, str(title),
                             "SHT-TBL%d-T" % ti, 0.005, layer="A-TEXT"))
        y += h + TABLE_TITLE_SLOT
    return els


def _north_rot_deg(meta):
    """从 meta 读真北角（§5.4 northAngle：顺时针为正、度），转成 north_symbol 的
    逆时针 rot_deg。缺省 0，解析失败回退 0。"""
    v = meta.get("northAngle", meta.get("north_angle", 0.0))
    try:
        return -float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _title_block(sx, sy, meta: Dict[str, str]) -> List[dict]:
    """在图框右下角画标题栏（图号/图名/工程/比例/日期/阶段）。"""
    els = [_rect(sx, sy, sx + TITLE_W, sy + TITLE_H, "TB-FRAME", "A-SHEET")]
    rows = 3
    for r in range(1, rows):
        yy = sy + TITLE_H * r / rows
        els.append(_line(sx, yy, sx + TITLE_W, yy, "TB-H%d" % r))
    els.append(_line(sx + TITLE_W * 0.42, sy, sx + TITLE_W * 0.42, sy + TITLE_H, "TB-V"))
    f = meta.get
    els.append(_text(sx + 0.004, sy + TITLE_H * 5 / 6 - 0.004, f("project", ""), "TB-proj", 0.006, layer="A-TEXT"))
    els.append(_text(sx + 0.004, sy + TITLE_H * 3 / 6 - 0.004, f("title", ""), "TB-title", 0.006, layer="A-TEXT"))
    els.append(_text(sx + 0.004, sy + TITLE_H * 1 / 6 - 0.004, f("stage", "方案"), "TB-stage", 0.0045, layer="A-TEXT"))
    els.append(_text(sx + TITLE_W * 0.44, sy + TITLE_H * 5 / 6 - 0.005, f("number", ""), "TB-num", 0.007, layer="A-TEXT"))
    els.append(_text(sx + TITLE_W * 0.44, sy + TITLE_H * 3 / 6 - 0.004, f("scale", ""), "TB-scl", 0.005, layer="A-TEXT"))
    els.append(_text(sx + TITLE_W * 0.44, sy + TITLE_H * 1 / 6 - 0.004, f("date", ""), "TB-date", 0.0045, layer="A-TEXT"))
    # 会签栏：紧贴标题栏左侧，4 行角色 + 签名空列
    csx = sx - CS_W - MARGIN * 0.5
    roles = ["设计", "制图", "校对", "审核"]
    els.append(_rect(csx, sy, csx + CS_W, sy + TITLE_H, "CS-FRAME", "A-SHEET"))
    n = len(roles)
    for r in range(1, n):
        yy = sy + TITLE_H * r / n
        els.append(_line(csx, yy, csx + CS_W, yy, "CS-H%d" % r))
    els.append(_line(csx + CS_W * 0.45, sy, csx + CS_W * 0.45, sy + TITLE_H, "CS-V"))
    for r, role in enumerate(roles):
        ry = sy + TITLE_H * (n - r) / n - 0.0045
        els.append(_text(csx + 0.002, ry, role, "CS-R%d" % r, 0.0035, layer="A-TEXT"))
    return els


def layout_sheets(views, meta, sheet="A1", scale=100, per_sheet=4,
                  north=True, captioned=True, tables=None):
    """views: [(图名, 类别(平面/立面/剖面), 模型空间元素)]；返回带图框标题栏的多张图纸。
    north=True：「平面」类视图 cell 右上角自动放指北针（§5.4/§8），角度取
    meta["northAngle"]（顺时针度，缺省 0）。captioned=True：图名改用 caption()
    （6mm + 粗下划线 + 比例文字，§5.7）；False 则维持旧版 5mm 纯文字 CAP。
    tables=[(标题, csv_text, num_cols, 列宽表), ...]：所有视图放完后，在每张图纸
    左下角（标题栏左侧）自下向上堆叠表格（面积表/门窗表等，schedules CSV 可直接
    经 csv_to_table 喂入）。tables=None 时行为与旧版完全一致。"""
    sw, sh = SHEETS.get(sheet, SHEETS["A1"])
    k = 1.0 / scale
    date = meta.setdefault("date", datetime.date.today().isoformat())
    rot_n = _north_rot_deg(meta)
    els = []
    base_num = {"平面": 101, "立面": 201, "剖面": 301}
    sheets_n = math.ceil(len(views) / per_sheet)
    if sheets_n == 0 and tables:          # 纯图表页：views=[] 但带表格，仍出一张图
        sheets_n = 1
    for si in range(sheets_n):
        batch = views[si * per_sheet:(si + 1) * per_sheet]
        base_x = si * (sw + 0.03)
        if batch:
            cat0 = batch[0][1]
            number = "A-%d" % (base_num.get(cat0, 901) + si)
            sheet_title = batch[0][0] if len(batch) == 1 else ("%s 等 %d 图" % (batch[0][0], len(batch)))
        else:
            number = "A-90%d" % (si + 1)
            sheet_title = meta.get("title", "图表")
        els.append(_rect(base_x, 0.0, base_x + sw, sh, "SHEET%d-FRAME" % si, "A-SHEET"))
        cols = 2 if len(batch) > 1 else 1
        rows = math.ceil(len(batch) / cols) if batch else 1
        draw_h = sh - TITLE_H - 2 * MARGIN
        cw, ch = (sw - 2 * MARGIN) / cols, draw_h / rows
        for vi, (title, cat, vels) in enumerate(batch):
            cx = base_x + MARGIN + (vi % cols) * cw
            cy = sh - MARGIN - ((vi // cols) + 1) * ch
            els.extend(_fit(vels, k, cx, cy, cw, ch - 0.014))
            if captioned:
                els.extend(caption(cx + 0.004, cy + ch - 0.010, title,
                                   "1:%d" % scale, name="CAP%d-%d" % (si, vi)))
            else:
                els.append(_text(cx + 0.004, cy + ch - 0.010, title,
                                 "CAP%d-%d" % (si, vi), 0.005))
            if north and cat == "平面":
                els.extend(north_symbol(cx + cw - 0.030, cy + ch - 0.030,
                                        r=0.012, rot_deg=rot_n))
        m2 = dict(meta)
        m2.update({"title": sheet_title, "number": number,
                   "scale": "1:%d" % scale, "date": date,
                   "stage": meta.get("stage", "方案")})
        els.extend(_title_block(base_x + sw - MARGIN - TITLE_W, MARGIN * 0.5, m2))
        if tables:
            els.extend(_place_tables(base_x, tables))
    return els


if __name__ == "__main__":
    # 自测：纸面尺寸核对（§5.4 φ24 指北针 / §5.7 图名下划线，单位=纸面米）
    ok = abs(0.024 - 2 * 0.012) < 1e-12 and abs(0.004 - 0.012 / 3.0) < 1e-12
    assert ok, "φ24/出圆 4mm 基准不符"

    # 1) 指北针：圆半径、针尖距离、旋转保距、「北」字随动
    els = north_symbol(0.5, 0.3)
    circ, needle, north_t = els
    assert len(circ["points"]) == 24 and circ["closed"] and circ["layer"] == "A-NORTH"
    r_c = max(math.hypot(p[0] - 0.5, p[1] - 0.3) for p in circ["points"])
    assert abs(r_c - 0.012) < 1e-5, r_c
    tip = needle["points"][0]
    assert abs(math.hypot(tip[0] - 0.5, tip[1] - 0.3) - 0.016) < 1e-5   # r+ext=0.016
    assert needle["points"][1][0] < 0.5 < needle["points"][2][0]
    assert north_t["type"] == "text2d" and abs(north_t["height"] - 0.006) < 1e-9
    assert north_t["text"] == "北" and tip[1] < north_t["insertPoint"][1]  # 字在针尖上方
    e45 = north_symbol(0, 0, rot_deg=45.0)
    assert abs(math.hypot(e45[1]["points"][0][0], e45[1]["points"][0][1]) - 0.016) < 1e-5
    # 逆时针 45°：针尖从正北偏西 → x = -0.016·sin45°
    assert abs(e45[1]["points"][0][0] + 0.016 * math.sin(math.radians(45))) < 1e-5

    # 2) caption：字高/下划线长度/比例文字
    cap = caption(0.1, 0.5, "首层平面图", "1:100")
    assert cap[0]["height"] == 0.006 and cap[0]["text"] == "首层平面图"
    ul = cap[1]
    assert ul["closed"] and ul["layer"] == "A-SHEET"
    assert abs((max(p[0] for p in ul["points"]) - min(p[0] for p in ul["points"]))
               - (0.006 * 5 + 0.004)) < 1e-9                      # 线长=5 CJK 字宽+4mm
    assert cap[2]["text"] == "比例 1:100" and abs(cap[2]["height"] - 0.0035) < 1e-9
    assert len(caption(0, 0, "空图名")) == 2                       # 无比例文字

    # 3) layout_sheets 接线：平面有指北针、立面/剖面无；旧签名向后兼容
    vels = [_rect(0.0, 0.0, 12.0, 8.0, "W", "A-WALL")]
    views = [("首层平面图", "平面", vels), ("1-1 剖面", "剖面", vels)]
    out = layout_sheets(views, {"project": "测试工程", "northAngle": 30.0}, "A1", 100)
    assert sum(1 for e in out if e.get("name") == "NORTH-C") == 1
    assert sum(1 for e in out if e.get("name", "").startswith("CAP0-0-UL")) == 1
    assert sum(1 for e in out if e.get("name", "").startswith("CAP0-1-UL")) == 1
    t30 = [e for e in out if e.get("name") == "NORTH-N"][0]["points"][0]
    # northAngle=30(顺时针真北角) → 针尖右偏(x>0)
    assert t30[0] > 0.0, "northAngle 顺时针 30° 应使针尖右偏"

    # 关闭开关 + 旧参数顺序（brief_to_cad.py:700 关键字调用同理）
    out2 = layout_sheets(views, {"project": "x"}, north=False, captioned=False)
    assert not any(e.get("name") == "NORTH-C" for e in out2)
    assert any(e.get("name") == "CAP0-0" and abs(e["height"] - 0.005) < 1e-9 for e in out2)

    # 4) csv_to_table：BOM/CRLF/引号单元格/列数补齐
    csv = ("\ufeff层号,编号,名称,面积m2,周长m" + chr(13) + chr(10) +
           "0,01,客厅,24.5,20.0" + chr(13) + chr(10) +
           "0,02,\"卧室, 南\",12.0,14.5" + chr(13) + chr(10) +
           chr(13) + chr(10))
    header, rows = csv_to_table(csv, 5)
    assert header == ["层号", "编号", "名称", "面积m2", "周长m"], header
    assert len(rows) == 2 and rows[1][2] == "卧室, 南", rows       # 引号内逗号不切
    h3, r3 = csv_to_table("a,b,c\n1,2\n", 0)                       # num_cols<=0 → 按首行
    assert h3 == ["a", "b", "c"] and r3 == [["1", "2", ""]]
    assert csv_to_table("", 3) == ([], [])

    # 5) table()：外框/表头线/列线/单元格文字数量与位置
    t = table(0.1, 0.2, header, rows, [0.02, 0.02, 0.05, 0.025, 0.025])
    pls = [e for e in t if e["type"] == "polyline2d"]
    txts = [e for e in t if e["type"] == "text2d"]
    frame = [e for e in pls if e["name"].endswith("-FRAME")]
    assert len(frame) == 1 and frame[0]["closed"] and frame[0]["layer"] == "A-SHEET"
    fx = [p[0] for p in frame[0]["points"]]
    fy = [p[1] for p in frame[0]["points"]]
    W = 0.02 + 0.02 + 0.05 + 0.025 + 0.025
    H = TABLE_ROW_H * TABLE_HEADER_K + 2 * TABLE_ROW_H
    assert abs(max(fx) - min(fx) - W) < 1e-9 and abs(max(fy) - min(fy) - H) < 1e-9
    assert sum(1 for e in pls if e["name"].endswith("-HSEP")) == 1
    assert sum(1 for e in pls if "-V" in e["name"]) == 4           # ncol-1 条列线
    n_cells = sum(1 for c in header if c) + sum(1 for r in rows for c in r if c)
    assert len(txts) == n_cells == 5 * 3, (len(txts), n_cells)
    assert all(abs(e["height"] - 0.004) < 1e-9 for e in txts)
    assert all(e["layer"] == "A-TEXT" for e in txts)
    c0 = [e for e in txts if e["name"] == "TBL-HD0"][0]
    assert abs(c0["insertPoint"][0] - (0.1 + 0.001)) < 1e-9        # 1mm 内边距
    assert c0["insertPoint"][1] > 0.2 + H - TABLE_ROW_H * TABLE_HEADER_K  # 表头行内
    assert table(0, 0, ["a"], [], []) == []                          # 无列宽→空

    # 6) layout_sheets(tables=...)：左下角出表格；None 时行为不变；views=[] 出纯图表页
    tbls = [("面积表", csv, 5, [0.04, 0.03, 0.08, 0.04, 0.04]),
            ("门窗表", csv, 5, [0.04, 0.03, 0.08, 0.04, 0.04])]
    same = layout_sheets(views, {"project": "测试工程"}, "A1", 100)
    out3 = layout_sheets(views, {"project": "测试工程"}, "A1", 100, tables=tbls)
    assert sum(1 for e in out3 if e.get("name", "").endswith("-FRAME")
               and str(e.get("name", "")).startswith("SHT-TBL")) == 2
    assert sum(1 for e in out3 if e.get("name") == "SHT-TBL0-T") == 1  # 标题 _text
    assert sum(1 for e in out3 if e.get("name", "").startswith("NORTH-C")) == 1
    tb_x = max(p[0] for e in out3 if e.get("name") == "TB-FRAME" for p in e["points"]) - TITLE_W
    for e in out3:
        if e["type"] == "polyline2d" and str(e.get("name", "")).startswith("SHT-TBL") \
                and e["name"].endswith("-FRAME"):
            assert min(p[0] for p in e["points"]) >= MARGIN - 1e-9
            assert max(p[0] for p in e["points"]) < tb_x - 1e-9    # 在标题栏左侧
            assert min(p[1] for p in e["points"]) >= MARGIN * 0.5 - 1e-9  # 左下角起
    assert [e for e in out3 if not str(e.get("name", "")).startswith("SHT-TBL")] == same
    pure = layout_sheets([], {"project": "x", "title": "图表页"}, tables=tbls)
    assert any(e.get("name") == "SHEET0-FRAME" for e in pure)      # 纯图表页也出一张
    assert sum(1 for e in pure if e.get("name", "").endswith("TBL1-FRAME")) == 1

    print("OK: sheets 自测通过（指北针 φ24/针尖出圆 4mm/旋转，caption 下划线+比例，"
          "layout_sheets 平面自动指北针 %d 元素；table/csv_to_table/tables 堆叠 %d 元素）"
          % (len(out), len(out3)))
