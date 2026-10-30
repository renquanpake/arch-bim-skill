# -*- coding: utf-8 -*-
"""elevations2.py — 立面投影（背面剔除 + 共享边去重），比 AABB 示意更接近真消隐。

方法：
  1) 每个实体 → 8 顶点 6 四边形（box 语义；wall 用 basePoint+length+angle；
     floor/roof 用 outline 棱柱）
  2) 对立面方向做背面剔除（保留朝向观察者的面）
  3) 只取可见面的边，共享边（相邻可见面共边）只画一次
  4) 投影到 2D 并排版（4 个立面横向排开），出 polyline2d

局限：不做线段级遮挡裁剪（不做"被前面实体挡住的部分"），
     因此是"可见面线框"级消隐，非严密切割。对方案阶段立面足够。
"""

import math

LAYER = "A-ELEV"
SKIP_LAYERS = ("A-SITE", "A-GRID", "A-TEXT", "A-SHEET", "A-DIM")


def _rot(px, py, cx, cy, ang):
    ca, sa = math.cos(ang), math.sin(ang)
    dx, dy = px - cx, py - cy
    return cx + dx * ca - dy * sa, cy + dx * sa + dy * ca


def _box_verts(x, y, z, w, d, h, ang=0.0):
    cx, cy = x + w / 2.0, y + d / 2.0
    base = [(x, y), (x + w, y), (x + w, y + d), (x, y + d)]
    if abs(ang) > 1e-9:
        base = [_rot(px, py, cx, cy, ang) for px, py in base]
    return [(px, py, z) for px, py in base] + [(px, py, z + h) for px, py in base]


BOX_FACES = ((0, 1, 2, 3), (4, 5, 6, 7),
             (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))


def _ang(el):
    a = float(el.get("angle", 0.0) or 0.0)
    if abs(a) > 2 * math.pi + 1e-6:      # 度数
        a = math.radians(a)
    return a


def _poly_faces(outline, z, th):
    """闭合多边形棱柱：顶/底多边形 + 侧面四边形。"""
    n = len(outline)
    verts = [(p[0], p[1], z) for p in outline] + [(p[0], p[1], z + th) for p in outline]
    faces = [tuple(range(n)), tuple(range(n, 2 * n))[::-1]]
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, j + n, i + n))
    return verts, faces


def _solid(el):
    t = (el.get("type") or "").lower()
    if t in ("wall", "column", "beam", "box", "door", "window"):
        bp = el.get("basePoint") or [0, 0, 0]
        x, y = float(bp[0]), float(bp[1])
        z = float(bp[2]) if len(bp) > 2 else float(el.get("elevation", 0.0))
        ang = _ang(el)
        if t == "wall":
            w, d, h = float(el.get("length", 0)), float(el.get("thickness", 0.2)), float(el.get("height", 3))
        elif t == "beam":
            w = float(el.get("length", 0)) or float(el.get("width", 0.3))
            d = float(el.get("width", 0.3)) if el.get("length") else float(el.get("depth", 0.3))
            h = float(el.get("height", 0.5))
            if el.get("start") and el.get("end"):
                s, e = el["start"], el["end"]
                L = math.hypot(e[0] - s[0], e[1] - s[1])
                ang = math.atan2(e[1] - s[1], e[0] - s[0])
                x, y, w = float(s[0]), float(s[1]), L
        else:
            w, d, h = float(el.get("width", 0.4)), float(el.get("depth", 0.4)), float(el.get("height", 3))
        if min(w, d, h) <= 1e-6:
            return None
        return _box_verts(x, y, z, w, d, h, ang), BOX_FACES
    if t in ("floor", "roof"):
        pts = [(float(p[0]), float(p[1])) for p in (el.get("outline") or []) if len(p) >= 2]
        if len(pts) < 3:
            return None
        return _poly_faces(pts, float(el.get("elevation", 0.0)), float(el.get("thickness", 0.15)))
    return None


def _face_normal(v, f):
    a, b, c = v[f[0]], v[f[1]], v[f[2]]
    u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
    L = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1e-9
    return n[0] / L, n[1] / L, n[2] / L


FACADES = {
    "S": (0.0, -1.0, False, lambda p: (p[0], p[2])),
    "N": (0.0, 1.0, True, lambda p: (p[0], p[2])),
    "E": (1.0, 0.0, True, lambda p: (p[1], p[2])),
    "W": (-1.0, 0.0, False, lambda p: (p[1], p[2])),
}


def build_elevations_true(elements, unit="m", facades=("S", "N", "E", "W")):
    """返回 4 个立面的可见面线框（polyline2d + 标题 text2d）。坐标单位米。"""
    solids = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        if str(el.get("layer", "")).startswith(SKIP_LAYERS):
            continue
        s = _solid(el)
        if s:
            solids.append(s)
    if not solids:
        return []

    # 全体包围盒（用于排版间距）
    xs = [v[0] for verts, _ in solids for v in verts]
    ys = [v[1] for verts, _ in solids for v in verts]
    zs = [v[2] for verts, _ in solids for v in verts]
    W, D, H = max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)
    gap = max(W, D) * 0.25 + 4.0
    ox = 0.0
    els = []

    for tag in facades:
        vx, vy, mirror, proj = FACADES[tag]
        edges = {}
        for verts, faces in solids:
            for f in faces:
                n = _face_normal(verts, f)
                if n[0] * vx + n[1] * vy >= -1e-9:   # 背向观察者 → 剔除
                    continue
                pts = [proj(verts[i]) for i in f]
                for k in range(len(pts)):
                    a, b = pts[k], pts[(k + 1) % len(pts)]
                    if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.02:   # 过滤极短边
                        continue
                    if mirror:
                        a = (-a[0], a[1]); b = (-b[0], b[1])
                    key = tuple(sorted([(round(a[0], 3), round(a[1], 3)),
                                        (round(b[0], 3), round(b[1], 3))]))
                    edges[key] = (a, b)
        if not edges:
            continue
        # 该立面 bbox → 排版偏移
        exs = [p[0] for e in edges.values() for p in e]
        ezs = [p[1] for e in edges.values() for p in e]
        base_x = ox - min(exs)
        base_z = -min(ezs)
        i = 0
        for (a, b) in edges.values():
            els.append({"type": "polyline2d", "name": "%s-E%d" % (tag, i),
                        "layer": LAYER,
                        "points": [[round(a[0] + base_x, 5), round(a[1] + base_z, 5)],
                                   [round(b[0] + base_x, 5), round(b[1] + base_z, 5)]]})
            i += 1
        width = max(exs) - min(exs)
        els.append({"type": "polyline2d", "name": "%s-GL" % tag, "layer": "A-DIM",
                    "points": [[round(0.0, 5), 0.0], [round(width, 5), 0.0]]})
        els.append({"type": "text2d", "name": "%s-TITLE" % tag,
                    "text": "%s 立面（可见面线框）" % tag,
                    "insertPoint": [0.0, round(H + 2.0, 5)],
                    "height": 0.8, "layer": "A-TEXT"})
        ox += width + gap
    return els
