# -*- coding: utf-8 -*-
"""fittings.py — 门窗扇 / 阳台 / 洁具 / 电梯轿厢（UniFormat B20、C20、D10、D20 补全）。

全部用 box 表达；带角度的构件（门窗扇）依赖插件 box.angle（CAD/Revit 均已支持）。
输入坐标米制，输出 brief 单位。
"""

import math
from typing import List, Dict


def door_window_panels(walls: List[dict], unit: str) -> List[dict]:
    """按墙体洞口生成门窗扇（薄板，随墙旋转）。"""
    s = 1000.0 if unit == "mm" else 1.0
    boxes: List[dict] = []
    for w in walls:
        ops = w.get("openings") or []
        if not ops:
            continue
        sx, sy = w["start"][0] / s, w["start"][1] / s
        ex, ey = w["end"][0] / s, w["end"][1] / s
        dx, dy = ex - sx, ey - sy
        L = math.hypot(dx, dy)
        if L <= 1e-6:
            continue
        ang = math.atan2(dy, dx)
        ux, uy = dx / L, dy / L
        thick = w.get("thickness", 200.0) / s
        for i, o in enumerate(ops):
            ow = float(o.get("width", 0.0)) / s
            oh = float(o.get("height", 0.0)) / s
            sill = float(o.get("sill", 0.0)) / s
            if ow <= 0 or oh <= 0:
                continue
            pw = max(0.2, ow - 0.08)          # 扇比洞口略小
            ph = max(0.2, oh - 0.05)
            off = float(o.get("startX", 0.0)) / s
            cx = sx + ux * (off + ow / 2.0)
            cy = sy + uy * (off + ow / 2.0)
            tp = 0.05
            is_door = (o.get("type") == "door")
            boxes.append({
                "name": "%s-%s%d" % (w.get("id", "W"), "D" if is_door else "W", i + 1),
                "layer": "A-DOOR" if is_door else "A-WINDOW",
                "basePoint": [round((cx - pw / 2.0) * s, 2), round((cy - tp / 2.0) * s, 2),
                              round((sill + tp / 2.0) * s, 2)],
                "width": round(pw * s, 2),
                "depth": round(tp * s, 2),
                "height": round(ph * s, 2),
                "angle": round(ang, 6),
            })
    return boxes


def balconies(layout_rooms: List[dict], envelope, floors: int,
              floor_height: float, unit: str) -> List[dict]:
    """北侧每层一个挑阳台：板 + 三面栏板。"""
    s = 1000.0 if unit == "mm" else 1.0
    x0, y0, x1, y1 = envelope
    bw, bd, bt = 3.6, 1.2, 0.15
    bx = (x0 + x1) / 2.0 - bw / 2.0
    boxes: List[dict] = []
    for lv in range(1, floors):
        z = lv * floor_height
        boxes.append({"name": "BALCONY-L%d-SLAB" % lv, "layer": "A-FLOOR",
                      "basePoint": [round(bx * s, 2), round(y1 * s, 2), round(z * s, 2)],
                      "width": round(bw * s, 2), "depth": round(bd * s, 2),
                      "height": round(bt * s, 2)})
        rail_h, rail_t = 1.1, 0.08
        boxes.append({"name": "BALCONY-L%d-RAIL-N" % lv, "layer": "A-RAIL",
                      "basePoint": [round(bx * s, 2), round((y1 + bd - rail_t) * s, 2),
                                    round((z + bt) * s, 2)],
                      "width": round(bw * s, 2), "depth": round(rail_t * s, 2),
                      "height": round(rail_h * s, 2)})
        for k, rx in enumerate((bx, bx + bw - rail_t)):
            boxes.append({"name": "BALCONY-L%d-RAIL-%d" % (lv, k + 1), "layer": "A-RAIL",
                          "basePoint": [round(rx * s, 2), round(y1 * s, 2),
                                        round((z + bt) * s, 2)],
                          "width": round(rail_t * s, 2), "depth": round(bd * s, 2),
                          "height": round(rail_h * s, 2)})
    return boxes


def sanitary_fixtures(layout_rooms: List[dict], unit: str, floor_height: float = 3.0) -> List[dict]:
    """卫生间/公卫内布置坐便 + 台盆（简化体量）。"""
    s = 1000.0 if unit == "mm" else 1.0
    boxes: List[dict] = []
    for r in layout_rooms:
        if r.get("type") not in ("bathroom", "wc"):
            continue
        x0, y0, x1, y1 = r["rect"]
        z = int(r.get("floor", 0)) * floor_height
        boxes.append({"name": "%s-TOILET" % r["id"], "layer": "A-SANITARY",
                      "basePoint": [round((x0 + 0.25) * s, 2), round((y0 + 0.25) * s, 2),
                                    round(z * s, 2)],
                      "width": round(0.4 * s, 2), "depth": round(0.7 * s, 2),
                      "height": round(0.4 * s, 2)})
        boxes.append({"name": "%s-BASIN" % r["id"], "layer": "A-SANITARY",
                      "basePoint": [round((x1 - 0.8) * s, 2), round((y0 + 0.2) * s, 2),
                                    round((z + 0.8) * s, 2)],
                      "width": round(0.55 * s, 2), "depth": round(0.4 * s, 2),
                      "height": round(0.15 * s, 2)})
    return boxes


def elevator_car(spine, envelope, unit: str) -> List[dict]:
    """电梯轿厢（首层，置于井道内）。"""
    s = 1000.0 if unit == "mm" else 1.0
    sx0, sy0, sx1, sy1 = spine
    ew, ed = 2.2, 2.2
    ex0 = sx0 + (sx1 - sx0 - ew) / 2.0
    ey0 = sy0 + 0.3
    return [{
        "name": "ELEV-CAR", "layer": "A-EQUIP",
        "basePoint": [round((ex0 + 0.2) * s, 2), round((ey0 + 0.2) * s, 2), 0.0],
        "width": round((ew - 0.4) * s, 2), "depth": round((ed - 0.4) * s, 2),
        "height": round(2.4 * s, 2),
    }]


def stair_railings(layout_stairs: List[dict], unit: str, floor_height: float = 3.0) -> List[dict]:
    """楼梯两侧栏杆：每侧 4 段阶梯状扶手（box 只能水平，故按段抬升）。"""
    s = 1000.0 if unit == "mm" else 1.0
    boxes: List[dict] = []
    for st in layout_stairs:
        risers = int(st.get("risers", 0))
        if risers < 2:
            continue
        run = float(st.get("run", 0.25))
        rise = float(st.get("rise", 0.19))
        width = float(st.get("width", 1.2))
        x = float(st.get("x", 0.0))
        y = float(st.get("y", 0.0))
        base_z = float(st.get("fromFloor", 0)) * float(floor_height)
        segs = 4
        per = max(1, risers // segs)
        for side, sx in (("L", x), ("R", x + width - 0.06)):
            for k in range(segs):
                step0 = k * per
                boxes.append({
                    "name": "%s-RAIL-%s%d" % (st.get("name", "S"), side, k + 1),
                    "layer": "A-RAIL",
                    "basePoint": [round(sx * s, 2), round((y + run * step0) * s, 2),
                                  round((base_z + rise * step0 + 0.9) * s, 2)],
                    "width": round(0.06 * s, 2),
                    "depth": round(run * per * s, 2),
                    "height": round(max(0.9, rise * per + 0.9) * s, 2),
                })
    return boxes


def furniture(layout_rooms: List[dict], floor_height: float, unit: str) -> List[dict]:
    """按房间类型布置主要家具体量（每间 1 件，避免刷屏）。"""
    s = 1000.0 if unit == "mm" else 1.0
    spec = {
        "bedroom": ("BED", 1.5, 2.0, 0.5),
        "living": ("SOFA", 2.2, 0.9, 0.8),
        "dining": ("TABLE", 1.4, 0.8, 0.75),
        "study": ("DESK", 1.2, 0.6, 0.75),
        "kitchen": ("COUNTER", 2.4, 0.6, 0.9),
    }
    boxes: List[dict] = []
    for r in layout_rooms:
        item = spec.get(r.get("type"))
        if not item:
            continue
        tag, w, d, h = item
        x0, y0, x1, y1 = r["rect"]
        if (x1 - x0) < w or (y1 - y0) < d:
            continue
        z = int(r.get("floor", 0)) * floor_height
        boxes.append({"name": "%s-%s" % (r["id"], tag), "layer": "A-FURN",
                      "basePoint": [round((x0 + 0.2) * s, 2), round((y0 + 0.2) * s, 2),
                                    round(z * s, 2)],
                      "width": round(w * s, 2), "depth": round(d * s, 2),
                      "height": round(h * s, 2)})
    return boxes


def ceiling_services(layout_rooms: List[dict], spine, floors: int,
                     floor_height: float, unit: str) -> List[dict]:
    """每层顶棚设备 + 水平干管：灯具 / 喷淋 / 给排水消防干管 / 桥架 / 风管。"""
    s = 1000.0 if unit == "mm" else 1.0
    x0 = min(r["rect"][0] for r in layout_rooms)
    x1 = max(r["rect"][2] for r in layout_rooms)
    y0 = min(r["rect"][1] for r in layout_rooms)
    y1 = max(r["rect"][3] for r in layout_rooms)
    sx0, sy0, sx1, sy1 = spine
    boxes: List[dict] = []
    for lv in range(floors):
        zc = lv * floor_height + floor_height - 0.35
        # 灯具 2 个 / 喷淋 4 个
        for k in range(2):
            lx = (x0 + x1) / 2.0 + (k - 0.5) * 3.0
            boxes.append({"name": "LIGHT-L%d-%d" % (lv, k + 1), "layer": "A-MEP-ELEC",
                          "basePoint": [round((lx - 0.3) * s, 2), round(((y0 + y1) / 2.0 - 0.3) * s, 2),
                                        round(zc * s, 2)],
                          "width": round(0.6 * s, 2), "depth": round(0.6 * s, 2),
                          "height": round(0.1 * s, 2)})
        for k, (px, py) in enumerate(((x0 + 1.5, y0 + 1.5), (x1 - 1.5, y0 + 1.5),
                                      (x0 + 1.5, y1 - 1.5), (x1 - 1.5, y1 - 1.5))):
            boxes.append({"name": "SPRINK-L%d-%d" % (lv, k + 1), "layer": "A-MEP-FIRE",
                          "basePoint": [round((px - 0.06) * s, 2), round((py - 0.06) * s, 2),
                                        round((zc - 0.05) * s, 2)],
                          "width": round(0.12 * s, 2), "depth": round(0.12 * s, 2),
                          "height": round(0.05 * s, 2)})
        zr = lv * floor_height + floor_height - 0.5
        L = (sy1 - sy0)
        for k, (nm, w, layer, dx) in enumerate((
                ("WATER-MAIN", 0.10, "A-MEP-WATER", 0.15),
                ("DRAIN-MAIN", 0.15, "A-MEP-DRAIN", 0.45),
                ("FIRE-MAIN", 0.15, "A-MEP-FIRE", 0.75),
                ("CABLE-TRAY", 0.30, "A-MEP-ELEC", 1.10),
                ("DUCT-MAIN", 0.60, "A-MEP-HVAC", 1.55))):
            if sx0 + dx + w > sx1:
                continue
            boxes.append({"name": "%s-L%d" % (nm, lv), "layer": layer,
                          "basePoint": [round((sx0 + dx) * s, 2), round(sy0 * s, 2),
                                        round((zr - 0.3) * s, 2)],
                          "width": round(w * s, 2), "depth": round(L * s, 2),
                          "height": round((0.2 if "DUCT" not in nm else 0.3) * s, 2)})
    return boxes


def roof_drainage(envelope, roof_z: float, unit: str) -> List[dict]:
    """屋面排水：四周檐沟 + 四角落水管。"""
    s = 1000.0 if unit == "mm" else 1.0
    x0, y0, x1, y1 = envelope
    boxes: List[dict] = []
    gw = 0.2
    boxes.append({"name": "GUTTER-S", "layer": "A-ROOF",
                  "basePoint": [round(x0 * s, 2), round((y0 - gw) * s, 2), round((roof_z - gw) * s, 2)],
                  "width": round((x1 - x0 + 2 * gw) * s, 2), "depth": round(gw * s, 2),
                  "height": round(gw * s, 2)})
    boxes.append({"name": "GUTTER-N", "layer": "A-ROOF",
                  "basePoint": [round(x0 * s, 2), round(y1 * s, 2), round((roof_z - gw) * s, 2)],
                  "width": round((x1 - x0 + 2 * gw) * s, 2), "depth": round(gw * s, 2),
                  "height": round(gw * s, 2)})
    for k, (px, py) in enumerate(((x0 - 0.1, y0 - 0.1), (x1 - 0.1, y0 - 0.1),
                                  (x0 - 0.1, y1 - 0.1), (x1 - 0.1, y1 - 0.1))):
        boxes.append({"name": "DOWNPIPE-%d" % (k + 1), "layer": "A-ROOF",
                      "basePoint": [round(px * s, 2), round(py * s, 2), 0.0],
                      "width": round(0.1 * s, 2), "depth": round(0.1 * s, 2),
                      "height": round(roof_z * s, 2)})
    return boxes


def site_works(envelope, unit: str) -> List[dict]:
    """场地：南侧道路、两侧绿化带、围墙 + 大门柱。"""
    s = 1000.0 if unit == "mm" else 1.0
    x0, y0, x1, y1 = envelope
    W = x1 - x0
    boxes: List[dict] = []
    boxes.append({"name": "ROAD-S", "layer": "A-SITE",
                  "basePoint": [round((x0 - 6) * s, 2), round((y0 - 12) * s, 2), round(-0.15 * s, 2)],
                  "width": round((W + 12) * s, 2), "depth": round(6 * s, 2),
                  "height": round(0.15 * s, 2)})
    boxes.append({"name": "GREEN-W", "layer": "A-SITE",
                  "basePoint": [round((x0 - 6) * s, 2), round(y0 * s, 2), round(-0.1 * s, 2)],
                  "width": round(6 * s, 2), "depth": round((y1 - y0) * s, 2),
                  "height": round(0.1 * s, 2)})
    boxes.append({"name": "GREEN-E", "layer": "A-SITE",
                  "basePoint": [round(x1 * s, 2), round(y0 * s, 2), round(-0.1 * s, 2)],
                  "width": round(6 * s, 2), "depth": round((y1 - y0) * s, 2),
                  "height": round(0.1 * s, 2)})
    wall_h, wall_t = 2.0, 0.2
    cx = (x0 + x1) / 2.0
    boxes.append({"name": "FENCE-W", "layer": "A-SITE",
                  "basePoint": [round((x0 - 6) * s, 2), round((y0 - 12) * s, 2), 0.0],
                  "width": round(wall_t * s, 2), "depth": round(12 * s, 2),
                  "height": round(wall_h * s, 2)})
    boxes.append({"name": "FENCE-E", "layer": "A-SITE",
                  "basePoint": [round((x1 + 6 - wall_t) * s, 2), round((y0 - 12) * s, 2), 0.0],
                  "width": round(wall_t * s, 2), "depth": round(12 * s, 2),
                  "height": round(wall_h * s, 2)})
    for k, px in enumerate((cx - 1.2, cx + 1.2)):
        boxes.append({"name": "GATE-POST-%d" % (k + 1), "layer": "A-SITE",
                      "basePoint": [round((px - 0.2) * s, 2), round((y0 - 12) * s, 2), 0.0],
                      "width": round(0.4 * s, 2), "depth": round(0.4 * s, 2),
                      "height": round(2.4 * s, 2)})
    return boxes


def floor_finishes(layout_rooms: List[dict], floor_height: float, unit: str) -> List[dict]:
    """C30 楼面做法：每间房一层 20mm 饰面（踢掉楼梯井等重叠不影响体量表达）。"""
    s = 1000.0 if unit == "mm" else 1.0
    out = []
    for r in layout_rooms:
        x0, y0, x1, y1 = r["rect"]
        z = int(r.get("floor", 0)) * floor_height + 0.02
        out.append({"name": "%s-FIN" % r["id"], "layer": "A-FINISH",
                    "basePoint": [round(x0 * s, 2), round(y0 * s, 2), round(z * s, 2)],
                    "width": round((x1 - x0) * s, 2), "depth": round((y1 - y0) * s, 2),
                    "height": round(0.02 * s, 2)})
    return out


def appliances(layout_rooms: List[dict], floor_height: float, unit: str) -> List[dict]:
    """E 家电：厨房冰箱+灶台，卫生间洗衣机。"""
    s = 1000.0 if unit == "mm" else 1.0
    out = []
    for r in layout_rooms:
        x0, y0, x1, y1 = r["rect"]
        z = int(r.get("floor", 0)) * floor_height
        t = r.get("type")
        if t == "kitchen":
            out.append({"name": "%s-FRIDGE" % r["id"], "layer": "A-EQUIP",
                        "basePoint": [round((x1 - 0.9) * s, 2), round((y0 + 0.2) * s, 2), round(z * s, 2)],
                        "width": round(0.7 * s, 2), "depth": round(0.7 * s, 2), "height": round(1.8 * s, 2)})
            out.append({"name": "%s-STOVE" % r["id"], "layer": "A-EQUIP",
                        "basePoint": [round((x0 + 0.3) * s, 2), round((y1 - 0.85) * s, 2), round(z * s, 2)],
                        "width": round(0.6 * s, 2), "depth": round(0.6 * s, 2), "height": round(0.9 * s, 2)})
        elif t in ("bathroom", "wc"):
            out.append({"name": "%s-WASHER" % r["id"], "layer": "A-EQUIP",
                        "basePoint": [round((x1 - 0.85) * s, 2), round((y1 - 0.85) * s, 2), round(z * s, 2)],
                        "width": round(0.6 * s, 2), "depth": round(0.6 * s, 2), "height": round(0.85 * s, 2)})
    return out


def mep_branches(layout_rooms: List[dict], spine, floors: int, floor_height: float, unit: str) -> List[dict]:
    """机电支管/末端：湿房间给排水支管、每间送风口、每层消火栓+配电箱。"""
    s = 1000.0 if unit == "mm" else 1.0
    sx0, sy0, sx1, sy1 = spine
    out = []
    for r in layout_rooms:
        x0, y0, x1, y1 = r["rect"]
        lv = int(r.get("floor", 0))
        zc = lv * floor_height + floor_height - 0.6
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        # 送风口（每间）
        out.append({"name": "%s-DIFFUSER" % r["id"], "layer": "A-MEP-HVAC",
                    "basePoint": [round((cx - 0.15) * s, 2), round((cy - 0.15) * s, 2), round(zc * s, 2)],
                    "width": round(0.3 * s, 2), "depth": round(0.3 * s, 2), "height": round(0.1 * s, 2)})
        if r.get("type") in ("bathroom", "wc", "kitchen"):
            # 给水/排水支管：从走廊边沿伸入房间
            bx = min(max(sx0, x0), sx1)
            out.append({"name": "%s-WATER-BR" % r["id"], "layer": "A-MEP-WATER",
                        "basePoint": [round(min(bx, x0) * s, 2), round((cy - 0.05) * s, 2), round((lv * floor_height + 2.4) * s, 2)],
                        "width": round(max(0.2, abs(x0 - bx)) * s, 2), "depth": round(0.05 * s, 2), "height": round(0.05 * s, 2)})
            out.append({"name": "%s-DRAIN-BR" % r["id"], "layer": "A-MEP-DRAIN",
                        "basePoint": [round(min(bx, x0) * s, 2), round((cy + 0.05) * s, 2), round((lv * floor_height + 2.3) * s, 2)],
                        "width": round(max(0.2, abs(x0 - bx)) * s, 2), "depth": round(0.08 * s, 2), "height": round(0.08 * s, 2)})
    for lv in range(floors):
        z = lv * floor_height + 1.0
        out.append({"name": "HYDRANT-L%d" % lv, "layer": "A-MEP-FIRE",
                    "basePoint": [round((sx0 + 0.05) * s, 2), round((sy0 + 1.0) * s, 2), round(z * s, 2)],
                    "width": round(0.3 * s, 2), "depth": round(0.8 * s, 2), "height": round(0.8 * s, 2)})
        out.append({"name": "DB-L%d" % lv, "layer": "A-MEP-ELEC",
                    "basePoint": [round((sx0 + 0.05) * s, 2), round((sy1 - 1.2) * s, 2), round((z + 0.4) * s, 2)],
                    "width": round(0.15 * s, 2), "depth": round(0.5 * s, 2), "height": round(0.6 * s, 2)})
    return out


def site_extras(envelope, unit: str) -> List[dict]:
    """G 场地：停车位、行道树、景观小品。"""
    s = 1000.0 if unit == "mm" else 1.0
    x0, y0, x1, y1 = envelope
    out = []
    for k in range(6):
        px = x0 - 5.0 + k * 2.7
        out.append({"name": "PARK-%d" % (k + 1), "layer": "A-SITE",
                    "basePoint": [round(px * s, 2), round((y0 - 11.0) * s, 2), round(-0.12 * s, 2)],
                    "width": round(2.5 * s, 2), "depth": round(5.0 * s, 2), "height": round(0.02 * s, 2)})
    for k in range(4):
        py = y0 + (k + 0.5) * (y1 - y0) / 4.0
        out.append({"name": "TREE-W%d" % (k + 1), "layer": "A-SITE",
                    "basePoint": [round((x0 - 3.0) * s, 2), round((py - 0.25) * s, 2), 0.0],
                    "width": round(0.5 * s, 2), "depth": round(0.5 * s, 2), "height": round(3.0 * s, 2)})
        out.append({"name": "TREE-E%d" % (k + 1), "layer": "A-SITE",
                    "basePoint": [round((x1 + 2.5) * s, 2), round((py - 0.25) * s, 2), 0.0],
                    "width": round(0.5 * s, 2), "depth": round(0.5 * s, 2), "height": round(3.0 * s, 2)})
    return out


def roof_buildup(envelope, roof_z: float, unit: str) -> List[dict]:
    """B30 屋面构造层（平屋面）：保温 100 + 防水 20，叠在结构板上。"""
    s = 1000.0 if unit == "mm" else 1.0
    x0, y0, x1, y1 = envelope
    return [
        {"name": "ROOF-INSULATION", "layer": "A-ROOF",
         "basePoint": [round(x0 * s, 2), round(y0 * s, 2), round(roof_z * s, 2)],
         "width": round((x1 - x0) * s, 2), "depth": round((y1 - y0) * s, 2),
         "height": round(0.1 * s, 2)},
        {"name": "ROOF-WATERPROOF", "layer": "A-ROOF",
         "basePoint": [round(x0 * s, 2), round(y0 * s, 2), round((roof_z + 0.1) * s, 2)],
         "width": round((x1 - x0) * s, 2), "depth": round((y1 - y0) * s, 2),
         "height": round(0.02 * s, 2)},
    ]
