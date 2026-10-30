# -*- coding: utf-8 -*-
"""structure.py — 按 UniFormat 2010 补全建筑的"结构 + 外壳 + 机电立管"几何。

覆盖（LOD 200/250 级别，全部用 box/柱/梁表达，不依赖族库）：
  A10 基础：独立基础 + 基础地梁
  B10 上部结构：柱（逐层）+ 梁（逐层，双向）
  B20 外围护：女儿墙、雨篷、入口台阶、散水
  D10 垂直运输：电梯井（井道墙 + 机房板）
  D20/D30/D40/D50 机电：给水/排水/消防立管 + 通风竖井

所有坐标由调用方给的米制参数生成，返回 brief 单位（unit）。
"""

from typing import List, Dict, Tuple


def _uniq(vals, tol=1e-6):
    out = []
    for v in sorted(vals):
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
    return out


def build_structure(envelope: Tuple[float, float, float, float],
                    spine: Tuple[float, float, float, float],
                    levels: int, floor_height: float, unit: str,
                    column_size: float = 0.4, beam_w: float = 0.3,
                    beam_h: float = 0.5) -> Dict[str, List[dict]]:
    """生成结构/外壳/机电构件的 brief 结构。

    envelope/spine 单位米；返回的坐标统一乘 s 转 brief 单位。
    """
    s = 1000.0 if unit == "mm" else 1.0
    x0, y0, x1, y1 = envelope
    sx0, sy0, sx1, sy1 = spine
    W, D = x1 - x0, y1 - y0
    roof_z = levels * floor_height

    columns: List[dict] = []
    beams: List[dict] = []
    boxes: List[dict] = []

    def r(v):
        return round(v * s, 2)

    # ---------- 轴网 ----------
    xs = _uniq([x0, sx0, sx1, x1])
    ys = _uniq([y0, (y0 + y1) / 2.0, y1])
    grid = {"x": [r(v) for v in xs], "y": [r(v) for v in ys]}

    # ---------- 柱：每层每个交点一根 ----------
    for lv in range(levels):
        z = lv * floor_height
        for ix, gx in enumerate(xs):
            for iy, gy in enumerate(ys):
                columns.append({
                    "id": "C-L%d-%d%d" % (lv, ix, iy),
                    "width": round(column_size * s, 2),
                    "depth": round(column_size * s, 2),
                    "height": round(floor_height * s, 2),
                    "basePoint": [r(gx), r(gy), r(z)],
                })

    # ---------- 梁：每层顶双向主梁 ----------
    for lv in range(levels):
        ztop = (lv + 1) * floor_height
        z = ztop - beam_h
        k = 0
        for gx in xs:
            k += 1
            beams.append({"id": "B-L%d-X%d" % (lv, k), "start": [r(gx), r(y0)],
                          "end": [r(gx), r(y1)], "width": round(beam_w * s, 2),
                          "height": round(beam_h * s, 2), "elevation": r(z)})
        for gy in ys:
            k += 1
            beams.append({"id": "B-L%d-Y%d" % (lv, k), "start": [r(x0), r(gy)],
                          "end": [r(x1), r(gy)], "width": round(beam_w * s, 2),
                          "height": round(beam_h * s, 2), "elevation": r(z)})

    # ---------- A10 基础：独基 + 地梁 ----------
    fw = 1.6
    for ix, gx in enumerate(xs):
        for iy, gy in enumerate(ys):
            boxes.append({
                "name": "FTG-%d%d" % (ix, iy), "layer": "A-FOUND",
                "basePoint": [r(gx - fw / 2), r(gy - fw / 2), r(-0.6)],
                "width": r(fw), "depth": r(fw), "height": r(0.6),
            })
    for k, gx in enumerate(xs):
        boxes.append({"name": "SG-X%d" % k, "layer": "A-FOUND",
                      "basePoint": [r(gx - 0.15), r(y0), r(-0.35)],
                      "width": r(0.3), "depth": r(D), "height": r(0.35)})
    for k, gy in enumerate(ys):
        boxes.append({"name": "SG-Y%d" % k, "layer": "A-FOUND",
                      "basePoint": [r(x0), r(gy - 0.15), r(-0.35)],
                      "width": r(W), "depth": r(0.3), "height": r(0.35)})

    # ---------- B20 外围护：女儿墙 ----------
    t = 0.2
    hh = 1.1
    boxes.append({"name": "PARAPET-S", "layer": "A-WALL",
                  "basePoint": [r(x0), r(y0 - t), r(roof_z)],
                  "width": r(W + 2 * t), "depth": r(t), "height": r(hh)})
    boxes.append({"name": "PARAPET-N", "layer": "A-WALL",
                  "basePoint": [r(x0), r(y1), r(roof_z)],
                  "width": r(W + 2 * t), "depth": r(t), "height": r(hh)})
    boxes.append({"name": "PARAPET-W", "layer": "A-WALL",
                  "basePoint": [r(x0 - t), r(y0), r(roof_z)],
                  "width": r(t), "depth": r(D), "height": r(hh)})
    boxes.append({"name": "PARAPET-E", "layer": "A-WALL",
                  "basePoint": [r(x1), r(y0), r(roof_z)],
                  "width": r(t), "depth": r(D), "height": r(hh)})

    # ---------- B20 入口雨篷 + 台阶 + 散水 ----------
    cx = (sx0 + sx1) / 2.0
    canopy_w, canopy_d = 4.0, 2.0
    boxes.append({"name": "CANOPY", "layer": "A-ROOF",
                  "basePoint": [r(cx - canopy_w / 2), r(y0 - canopy_d), r(3.0 - 0.15)],
                  "width": r(canopy_w), "depth": r(canopy_d), "height": r(0.15)})
    for k, px in enumerate((cx - canopy_w / 2 + 0.2, cx + canopy_w / 2 - 0.2)):
        boxes.append({"name": "CANOPY-POST-%d" % (k + 1), "layer": "A-COLUMN",
                      "basePoint": [r(px - 0.1), r(y0 - canopy_d + 0.1), r(0)],
                      "width": r(0.2), "depth": r(0.2), "height": r(3.0 - 0.15)})

    step_w = 2.4
    for k in range(3):
        boxes.append({"name": "STEP-%d" % (k + 1), "layer": "A-SITE",
                      "basePoint": [r(cx - step_w / 2), r(y0 - 0.9 + k * 0.3), r(-0.45 + k * 0.15)],
                      "width": r(step_w), "depth": r(0.3), "height": r(0.15)})

    aw = 0.8
    boxes.append({"name": "APRON-S", "layer": "A-SITE",
                  "basePoint": [r(x0 - aw), r(y0 - aw), r(-0.1)],
                  "width": r(W + 2 * aw), "depth": r(aw), "height": r(0.1)})
    boxes.append({"name": "APRON-N", "layer": "A-SITE",
                  "basePoint": [r(x0 - aw), r(y1), r(-0.1)],
                  "width": r(W + 2 * aw), "depth": r(aw), "height": r(0.1)})
    boxes.append({"name": "APRON-W", "layer": "A-SITE",
                  "basePoint": [r(x0 - aw), r(y0), r(-0.1)],
                  "width": r(aw), "depth": r(D), "height": r(0.1)})
    boxes.append({"name": "APRON-E", "layer": "A-SITE",
                  "basePoint": [r(x1), r(y0), r(-0.1)],
                  "width": r(aw), "depth": r(D), "height": r(0.1)})

    # ---------- D10 电梯井（spine 前部，避开后部楼梯）----------
    ew, ed, et = 2.2, 2.2, 0.2
    ex0 = sx0 + (sx1 - sx0 - ew) / 2.0
    ey0 = y0 + 0.3
    zs = [(ex0, ey0, ew, et), (ex0, ey0 + ed - et, ew, et)]
    for k, (bx, by, bw, bd) in enumerate(zs):
        boxes.append({"name": "ELEV-SHAFT-%d" % (k + 1), "layer": "A-WALL",
                      "basePoint": [r(bx), r(by), r(0)],
                      "width": r(bw), "depth": r(bd), "height": r(roof_z)})
    boxes.append({"name": "ELEV-SHAFT-W", "layer": "A-WALL",
                  "basePoint": [r(ex0), r(ey0), r(0)],
                  "width": r(et), "depth": r(ed), "height": r(roof_z)})
    boxes.append({"name": "ELEV-SHAFT-E", "layer": "A-WALL",
                  "basePoint": [r(ex0 + ew - et), r(ey0), r(0)],
                  "width": r(et), "depth": r(ed), "height": r(roof_z)})
    boxes.append({"name": "ELEV-MACHINE-ROOM", "layer": "A-WALL",
                  "basePoint": [r(ex0 - 0.3), r(ey0 - 0.3), r(roof_z)],
                  "width": r(ew + 0.6), "depth": r(ed + 0.6), "height": r(2.4)})

    # ---------- D20/D30/D40/D50 立管 + 竖井 ----------
    risers = [
        ("RISER-WATER", 0.12, "A-MEP-WATER"),
        ("RISER-DRAIN", 0.15, "A-MEP-DRAIN"),
        ("RISER-FIRE", 0.15, "A-MEP-FIRE"),
        ("RISER-ELEC", 0.10, "A-MEP-ELEC"),
    ]
    for k, (nm, size, layer) in enumerate(risers):
        px = ex0 + 0.15 + (ex0 - ex0) + (k * (size + 0.12))
        boxes.append({"name": nm, "layer": layer,
                      "basePoint": [r(ex0 + 0.15 + k * 0.3), r(ey0 + 0.05), r(0)],
                      "width": r(size), "depth": r(size), "height": r(roof_z)})
    boxes.append({"name": "SHAFT-HVAC", "layer": "A-MEP-HVAC",
                  "basePoint": [r(ex0 + ew + 0.2), r(ey0), r(0)],
                  "width": r(0.6), "depth": r(0.4), "height": r(roof_z)})

    return {"columns": columns, "beams": beams, "boxes": boxes, "grid": grid}
