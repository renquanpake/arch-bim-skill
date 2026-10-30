#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ifc_export.py — request.json（米制几何 + 语义层）→ IFC4

输出标准 IFC4，Revit / BlenderBIM / 任意 IFC 查看器可直接打开：
  IfcProject / IfcSite / IfcBuilding / IfcBuildingStorey
  IfcWall / IfcSlab / IfcRoof / IfcColumn / IfcBeam / IfcDoor / IfcWindow
  IfcOpeningElement（墙体真开洞：IfcRelVoidsElement + IfcBooleanResult CSG）
  IfcDoor/IfcWindow（IfcRelFillsElement 填充洞口，OverallWidth/Height 属性）
  IfcStair / IfcRailing / IfcFooting / IfcCovering / IfcFurniture
  IfcSanitaryTerminal / IfcPipeSegment / IfcDuctSegment / IfcCableCarrierSegment
  IfcSpace（房间，带名称/编号 + Pset_SpaceCommon: NetArea/LongName/Number）
  IfcPropertySet（Pset_WallCommon / Pset_SlabCommon / Pset_DoorCommon /
                  Pset_WindowCommon / Pset_SpaceCommon / Marvis_Element 自定义）

用法：
  python ifc_export.py request.json [out.ifc] [--storey-height 2.8]
  out 省略时用 request.savePath（扩展名换成 .ifc）。

集成说明（ifcopenshell 0.9.0 实测）：
  - api.geometry.add_boolean(first_item=IfcFacetedBrep, second_items=[...],
    operator="DIFFERENCE") 会把产品 representation 的 item 就地替换为
    IfcBooleanResult，实现真 CSG 挖洞。mesh 需 force_faceted_brep=True。
  - api.geometry.add_door_representation / add_window_representation 在 0.9.0
    对 IFC4 基础 context 崩溃（访问 context.TargetView，IFC4 中该属性只在
    IfcGeometricRepresentationSubContext 上，且无 subcontext usecase 可用）。
    因此门窗走网面长方体回退：世界坐标放对位置 + OverallWidth/OverallHeight
    + IfcRelFillsElement，语义完整、查看器可渲染。
  - IfcRelVoidsElement / IfcRelFillsElement 无现成 usecase，用
    model.create_entity 直建（IFC4 合法关系，GlobalId 用 ifcopenshell.guid.new()）。
"""

import argparse
import json
import math
import sys

import numpy as np
import ifcopenshell
import ifcopenshell.guid
import ifcopenshell.api as api
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.unit
import ifcopenshell.api.context
import ifcopenshell.api.aggregate
import ifcopenshell.api.geometry
import ifcopenshell.api.spatial
import ifcopenshell.api.attribute
import ifcopenshell.api.pset

TYPE_CLASS = {
    "wall": "IfcWall", "floor": "IfcSlab", "roof": "IfcRoof",
    "column": "IfcColumn", "beam": "IfcBeam",
    "door": "IfcDoor", "window": "IfcWindow",
}

LAYER_CLASS = [
    ("A-FOUND", "IfcFooting"),
    ("A-DOOR", "IfcDoor"),
    ("A-WINDOW", "IfcWindow"),
    ("A-STAIR", "IfcStair"),
    ("A-RAIL", "IfcRailing"),
    ("A-SANITARY", "IfcSanitaryTerminal"),
    ("A-FURN", "IfcFurniture"),
    ("A-FINISH", "IfcCovering"),
    ("A-MEP-WATER", "IfcPipeSegment"),
    ("A-MEP-DRAIN", "IfcPipeSegment"),
    ("A-MEP-FIRE", "IfcPipeSegment"),
    ("A-MEP-HVAC", "IfcDuctSegment"),
    ("A-MEP-ELEC", "IfcCableCarrierSegment"),
    ("A-ROOF", "IfcSlab"),
    ("A-COLUMN", "IfcColumn"),
    ("A-BEAM", "IfcBeam"),
    ("A-WALL", "IfcWall"),
    ("A-FLOOR", "IfcSlab"),
    ("A-EQUIP", "IfcBuildingElementProxy"),
    ("A-SITE", "IfcBuildingElementProxy"),
]

# 各产品类挂的标准 Pset 名（IFC4 bSDD 常用；未知属性名会作为自定义属性写入）
PSET_FOR_CLASS = {
    "IfcWall": "Pset_WallCommon",
    "IfcSlab": "Pset_SlabCommon",
    "IfcRoof": "Pset_RoofCommon",
    "IfcDoor": "Pset_DoorCommon",
    "IfcWindow": "Pset_WindowCommon",
    "IfcSpace": "Pset_SpaceCommon",
}


def class_for(el):
    t = (el.get("type") or "").lower()
    if t in TYPE_CLASS:
        return TYPE_CLASS[t]
    layer = el.get("layer") or ""
    for prefix, cls in LAYER_CLASS:
        if layer.startswith(prefix):
            return cls
    return "IfcBuildingElementProxy"


def _rot(cx, cy, ang, px, py):
    if not ang:
        return px, py
    ca, sa = math.cos(ang), math.sin(ang)
    dx, dy = px - cx, py - cy
    return cx + dx * ca - dy * sa, cy + dx * sa + dy * ca


def box_mesh(x, y, z, w, d, h, ang=0.0):
    cx, cy = x + w / 2.0, y + d / 2.0
    base = [(x, y), (x + w, y), (x + w, y + d), (x, y + d)]
    base = [_rot(cx, cy, ang, px, py) for px, py in base]
    verts = [(px, py, z) for px, py in base] + [(px, py, z + h) for px, py in base]
    faces = [(3, 2, 1, 0), (4, 5, 6, 7),
             (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return verts, faces


def slab_mesh(outline, z, th):
    n = len(outline)
    verts = [(p[0], p[1], z) for p in outline] + [(p[0], p[1], z + th) for p in outline]
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    faces += [(i, (i + 1) % n, (i + 1) % n + n, i + n) for i in range(n)]
    return verts, faces


def _wall_frame(el):
    """墙参数：basePoint(x,y,z)、length、thickness、height、angle（绕Z弧度）。"""
    bp = el.get("basePoint") or [0, 0, 0]
    return (float(bp[0]), float(bp[1]), float(bp[2] if len(bp) > 2 else 0),
            float(el.get("length", 0)), float(el.get("thickness", 0.2)),
            float(el.get("height", 3.0)), float(el.get("angle", 0) or 0))


def _opening_rect(wall, op, y_in, y_out):
    """洞口/门扇在墙局部坐标的 XY 矩形（含世界旋转，绕墙 footprint 中心）。

    y_in/y_out: 沿墙厚度方向的起止（局部，米）。opening 用外扩值，门扇用内缩值。
    """
    x, y, z, L, th, h, ang = wall
    cx, cy = x + L / 2.0, y + th / 2.0
    sx = float(op.get("startX", 0) or 0)
    ow = float(op.get("width", 0.9))
    pts = [(x + sx, y + y_in), (x + sx + ow, y + y_in),
           (x + sx + ow, y + y_out), (x + sx, y + y_out)]
    return [_rot(cx, cy, ang, px, py) for px, py in pts]


def opening_mesh(wall, op, pad=None):
    """IfcOpeningElement / 门扇 / 窗扇的长方体网格（世界坐标，米）。

    默认外扩 pad 保证贯穿墙厚（CSG 相交干净）；pad=负值则内缩（做门扇实体）。
    """
    x, y, z, L, th, h, ang = wall
    if pad is None:
        pad = max(th * 0.5, 0.05)   # 开口体：厚度方向两侧各外扩
    sill = float(op.get("sill", 0) or 0)
    oh = float(op.get("height", 2.1))
    pts = _opening_rect(wall, op, -pad, th + pad)
    return slab_mesh(pts, z + sill, max(oh, 0.01))


def leaf_mesh(wall, op, y_frac=0.3):
    """门/窗实体：厚度方向内缩 y_frac..1-y_frac，贴在墙内。"""
    x, y, z, L, th, h, ang = wall
    sill = float(op.get("sill", 0) or 0)
    oh = float(op.get("height", 2.1))
    inset = max(th * 0.05, 0.005)   # 沿墙长方向微内缩，避免与洞口面完全共面
    sx = float(op.get("startX", 0) or 0) + inset
    op_in = dict(op, startX=sx, width=max(float(op.get("width", 0.9)) - 2 * inset, 0.01))
    cx, cy = x + L / 2.0, y + th / 2.0
    pts = _opening_rect(wall, op_in, th * y_frac, th * (1 - y_frac))
    return slab_mesh(pts, z + sill, max(oh, 0.01))


def element_mesh(el):
    t = (el.get("type") or "").lower()
    if t == "wall":
        x, y, z, L, th, h, ang = _wall_frame(el)
        return box_mesh(x, y, z, L, th, h, ang)
    if t in ("floor", "roof"):
        pts = [(float(p[0]), float(p[1])) for p in (el.get("outline") or []) if len(p) >= 2]
        if len(pts) < 3:
            return None
        return slab_mesh(pts, float(el.get("elevation", 0)),
                         float(el.get("thickness", 0.15)))
    if t == "beam":
        s = el.get("start")
        e = el.get("end")
        bw = float(el.get("width", 0.3))
        bh = float(el.get("height", 0.5))
        z = float(el.get("elevation", 0))
        if s and e:
            L = math.hypot(e[0] - s[0], e[1] - s[1])
            ang = math.atan2(e[1] - s[1], e[0] - s[0])
            return box_mesh(float(s[0]), float(s[1]), z, L, bw, bh, ang)
        bp = el.get("basePoint") or [0, 0, 0]
        return box_mesh(float(bp[0]), float(bp[1]), float(bp[2] if len(bp) > 2 else z),
                        float(el.get("length", 1.0)), bw, bh, float(el.get("angle", 0) or 0))
    # column / box / door / window / instance：basePoint 为最小角点
    bp = el.get("basePoint") or el.get("position") or [0, 0, 0]
    return box_mesh(float(bp[0]), float(bp[1]), float(bp[2] if len(bp) > 2 else 0),
                    float(el.get("width", 0.4)), float(el.get("depth", 0.4)),
                    float(el.get("height", 3.0)), float(el.get("angle", 0) or 0))


def polygon_area(pts):
    """Shoelace 面积（绝对值，平方米）。"""
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def set_attrs(model, product, attributes):
    """安全写属性：ifcopenshell 0.9 的 IFC4 绑定属性名有漂移（如 IfcSpace 无
    Number、IfcRelVoidsElement 洞名 RelatedOpeningElement），先按
    get_attribute_names() 过滤再写，未知属性静默跳过（由调用方兜底进 Pset）。"""
    try:
        valid = set(product.get_attribute_names())
    except Exception:  # noqa: BLE001
        valid = set(attributes)
    filtered = {k: v for k, v in attributes.items() if k in valid and v is not None}
    if filtered:
        api.attribute.edit_attributes(model, product=product, attributes=filtered)
    return {k: v for k, v in attributes.items() if k not in filtered}


def add_pset_props(model, product, pset_name, properties):
    """挂 IfcPropertySet 并写属性；失败静默跳过（不影响主流程）。"""
    if not pset_name or not properties:
        return None
    try:
        ps = api.pset.add_pset(model, product=product, name=pset_name)
        api.pset.edit_pset(model, pset=ps, properties=properties)
        return ps
    except Exception as e:  # noqa: BLE001 — pset 是增强项，绝不能炸掉导出
        print("[ifc][warn] pset %s failed: %s" % (pset_name, e))
        return None


def _set_relation(model, ifc_class, relating_candidates, related_candidates,
                  relating_obj, related_obj):
    """直建 Ifc 关系实体，按属性名内省定位索引赋值。

    不同 schema 构建属性名漂移（如 IfcRelVoidsElement 在 ifcopenshell 0.9 的
    IFC4 构建中是 RelatedOpeningElement，官方 IFC4 文档名为 RelatedFeatureElement），
    kwargs 直传会 "Attribute index out of bounds"，因此 introspect → positional set。
    """
    rel = model.create_entity(ifc_class)
    names = rel.get_attribute_names()
    idx = {"GlobalId": 0}
    got_relating = got_related = False
    for i, n in enumerate(names):
        if n == "GlobalId":
            idx["GlobalId"] = i
        if not got_relating and n in relating_candidates:
            idx["relating"] = i
            got_relating = True
        if not got_related and n in related_candidates:
            idx["related"] = i
            got_related = True
    rel[idx["GlobalId"]] = ifcopenshell.guid.new()
    rel[idx["relating"]] = relating_obj
    rel[idx["related"]] = related_obj
    return rel


def make_void_relation(model, wall, opening):
    """IfcRelVoidsElement：wall ← opening（语义挖洞）。"""
    try:
        return _set_relation(
            model, "IfcRelVoidsElement",
            ("RelatingBuildingElement", "RelatingStructure"),
            ("RelatedFeatureElement", "RelatedOpeningElement"),
            wall, opening)
    except Exception as e:  # noqa: BLE001
        print("[ifc][warn] IfcRelVoidsElement failed: %s" % e)
        return None


def make_fills_relation(model, opening, element):
    """IfcRelFillsElement：opening ← door/window（构件填充洞口）。"""
    try:
        return _set_relation(
            model, "IfcRelFillsElement",
            ("RelatingOpeningElement",),
            ("RelatedBuildingElement",),
            opening, element)
    except Exception as e:  # noqa: BLE001
        print("[ifc][warn] IfcRelFillsElement failed: %s" % e)
        return None


def try_csg_cut(model, wall_rep, opening_reps):
    """真 CSG：IfcFacetedBrep(wall) − Σ IfcFacetedBrep(opening)。

    api.geometry.add_boolean 会把 wall_rep.Items 就地替换为 IfcBooleanResult 链。
    返回 True/False。失败时墙保留完整实体几何，洞口语义仍由 RelVoids 承载。
    """
    if not hasattr(api.geometry, "add_boolean"):
        return False
    try:
        first = wall_rep.Items[0]
        seconds = [r.Items[0] for r in opening_reps if r and r.Items]
        if not seconds or first.is_a() != "IfcFacetedBrep":
            return False
        api.geometry.add_boolean(model, first_item=first,
                                 second_items=seconds, operator="DIFFERENCE")
        return True
    except Exception as e:  # noqa: BLE001 — 布尔半途失败可能留下部分结果链，仍属合法 IFC
        print("[ifc][warn] CSG boolean failed (voids rel kept): %s" % e)
        return False


def export(request, out_path, storey_height=2.8):
    elements = request.get("elements") or []
    levels = request.get("levels") or [{"name": "L0", "elevation": 0.0}]
    rooms = request.get("rooms") or []

    model = api.project.create_file(version="IFC4")
    project = api.root.create_entity(model, ifc_class="IfcProject",
                                     name=request.get("project", {}).get("name", "Marvis Building"))
    length_unit = api.unit.add_si_unit(model, unit_type="LENGTHUNIT", prefix=None)
    api.unit.assign_unit(model, units=[length_unit])  # 单位=米
    ctx = api.context.add_context(model, context_type="Model")

    site = api.root.create_entity(model, ifc_class="IfcSite", name="Site")
    building = api.root.create_entity(model, ifc_class="IfcBuilding", name=project.Name)
    api.aggregate.assign_object(model, products=[site], relating_object=project)
    api.aggregate.assign_object(model, products=[building], relating_object=site)

    storeys = []
    for lv in levels:
        st = api.root.create_entity(model, ifc_class="IfcBuildingStorey",
                                    name=lv.get("name", "Level"))
        set_attrs(model, st, {"Elevation": float(lv.get("elevation", 0.0))})
        api.aggregate.assign_object(model, products=[st], relating_object=building)
        storeys.append(st)

    def storey_for(z):
        best = storeys[0]
        for st, lv in zip(storeys, levels):
            if z + 1e-3 >= float(lv.get("elevation", 0.0)):
                best = st
        return best

    def storey_name_for(z):
        st = storey_for(z)
        for s, lv in zip(storeys, levels):
            if s is st:
                return lv.get("name", "Level")
        return st.Name

    stats = {}

    def bump(key, n=1):
        stats[key] = stats.get(key, 0) + n

    for el in elements:
        t = (el.get("type") or "").lower()
        if t in ("polyline2d", "text2d"):
            continue
        mesh = element_mesh(el)
        if not mesh:
            continue
        verts, faces = mesh
        cls = class_for(el)
        openings = (el.get("openings") or []) if t == "wall" else []
        # 带洞口的墙用 IfcFacetedBrep，以便 add_boolean 做真 CSG 减法
        brep = bool(openings)
        prod = api.root.create_entity(model, ifc_class=cls, name=el.get("name"))
        rep = api.geometry.add_mesh_representation(model, context=ctx,
                                                   vertices=[verts], faces=[faces],
                                                   force_faceted_brep=brep)
        api.geometry.assign_representation(model, product=prod, representation=rep)
        z = min(v[2] for v in verts)
        st = storey_for(z)
        api.spatial.assign_container(model, products=[prod], relating_structure=st)
        bump(cls)

        # ── 墙：真开洞 + 门窗构件 ──────────────────────────────────────────
        if openings:
            wall = _wall_frame(el)
            opening_reps = []
            for op in openings:
                op_type = (op.get("type") or "opening").lower()
                ov, of_ = opening_mesh(wall, op)
                opening = api.root.create_entity(
                    model, ifc_class="IfcOpeningElement",
                    name="%s_%s" % (el.get("name") or "Wall", op_type))
                orep = api.geometry.add_mesh_representation(
                    model, context=ctx, vertices=[ov], faces=[of_],
                    force_faceted_brep=True)
                api.geometry.assign_representation(model, product=opening,
                                                   representation=orep)
                # 开口体自身外扩/超高，按洞口底标高归层
                oz = wall[2] + float(op.get("sill", 0) or 0)
                # 洞口不 assign_container：语义上属于墙（IfcRelVoidsElement）
                make_void_relation(model, prod, opening)
                opening_reps.append(orep)
                bump("IfcOpeningElement")

                if op_type in ("door", "window"):
                    dcls = "IfcDoor" if op_type == "door" else "IfcWindow"
                    dw = float(op.get("width", 0.9))
                    dh = float(op.get("height", 2.1 if op_type == "door" else 1.5))
                    leaf = api.root.create_entity(model, ifc_class=dcls,
                                                  name=op.get("name") or
                                                  "%s_%s" % (el.get("name") or "", op_type))
                    set_attrs(model, leaf,
                              {"OverallWidth": dw, "OverallHeight": dh,
                               "Tag": op.get("id")})
                    lv_, lf_ = leaf_mesh(wall, op)
                    lrep = api.geometry.add_mesh_representation(
                        model, context=ctx, vertices=[lv_], faces=[lf_])
                    api.geometry.assign_representation(model, product=leaf,
                                                       representation=lrep)
                    api.spatial.assign_container(model, products=[leaf],
                                                 relating_structure=storey_for(oz))
                    make_fills_relation(model, opening, leaf)
                    # 0.9.0 的 add_door/window_representation 对 IFC4 基础 context
                    # 崩溃（TargetView 只在 SubContext 上），故不尝试参数化 rep，
                    # 网扇 + Overall* + RelFills 已构成完整洞口构件语义。
                    bump(dcls)

            # 真 CSG 挖洞；失败也无妨（RelVoids 语义 + 洞口体仍在模型里）
            if try_csg_cut(model, rep, opening_reps):
                bump("IfcBooleanResult(CSG)")

        # ── Pset 属性 ─────────────────────────────────────────────────────
        props = {"Reference": el.get("name") or ""}
        if t in ("wall", "floor", "roof"):
            props["IsExternal"] = True
        if el.get("layer"):
            props["Layer"] = el["layer"]
        if el.get("material"):
            props["MaterialCategory"] = el["material"]
        props["Level"] = storey_name_for(z)
        pset_name = PSET_FOR_CLASS.get(cls)
        if pset_name and add_pset_props(model, prod, pset_name, props):
            bump("IfcPropertySet")
        custom = {k: v for k, v in props.items() if k in ("Layer", "MaterialCategory")}
        if custom and add_pset_props(model, prod, "Marvis_Element", custom):
            bump("IfcPropertySet")

    # ── IfcSpace：房间体量 + NetArea ──────────────────────────────────────
    for r in rooms:
        pts = [(float(p[0]), float(p[1])) for p in (r.get("outline") or []) if len(p) >= 2]
        if len(pts) < 3:
            continue
        lv = int(r.get("level", 0))
        base = float(levels[lv]["elevation"]) if 0 <= lv < len(levels) else 0.0
        # 房间净高：优先到上一层的层高，否则用传入默认值
        hh = storey_height
        if 0 <= lv < len(levels) - 1:
            hh = float(levels[lv + 1]["elevation"]) - base
            if hh <= 0.5:
                hh = storey_height
        verts, faces = slab_mesh(pts, base, max(hh, 0.5))
        sp = api.root.create_entity(model, ifc_class="IfcSpace", name=r.get("name"))
        # Number 在该 schema 绑定里不存在于 IfcSpace，set_attrs 自动过滤，
        # 编号统一落进 Pset_SpaceCommon.Number（见下）
        set_attrs(model, sp, {"LongName": r.get("name"), "Number": r.get("number")})
        rep = api.geometry.add_mesh_representation(model, context=ctx,
                                                   vertices=[verts], faces=[faces])
        api.geometry.assign_representation(model, product=sp, representation=rep)
        api.aggregate.assign_object(model, products=[sp],
                                    relating_object=storeys[min(max(lv, 0), len(storeys) - 1)])
        bump("IfcSpace")
        net = round(polygon_area(pts), 3)
        sp_props = {"Reference": r.get("number") or "",
                    "LongName": r.get("name") or "",
                    "Number": r.get("number") or "",
                    "NetArea": net,
                    "IsInterior": True}
        if r.get("level") is not None:
            sp_props["Level"] = levels[lv].get("name", "") if 0 <= lv < len(levels) else ""
        if add_pset_props(model, sp, "Pset_SpaceCommon", sp_props):
            bump("IfcPropertySet")

    model.write(out_path)
    return stats


def validate(path):
    """自验：重开文件，打印关键类型计数。IfcOpeningElement>0 才算洞口生效。"""
    m = ifcopenshell.open(path)
    types = ["IfcWall", "IfcOpeningElement", "IfcDoor", "IfcWindow", "IfcSpace",
             "IfcPropertySet", "IfcBuildingStorey", "IfcRelVoidsElement",
             "IfcRelFillsElement", "IfcBooleanResult", "IfcSlab", "IfcRoof",
             "IfcColumn", "IfcBeam"]
    counts = {t: len(m.by_type(t)) for t in types}
    return counts


def main(argv=None):
    p = argparse.ArgumentParser(description="request.json → IFC4")
    p.add_argument("request")
    p.add_argument("out", nargs="?", default=None)
    p.add_argument("--storey-height", type=float, default=2.8,
                   help="房间默认净高（米），levels 间隔可用时自动覆盖")
    args = p.parse_args(argv)
    with open(args.request, "r", encoding="utf-8-sig") as f:
        req = json.load(f)
    out = args.out
    if not out:
        sp = req.get("savePath") or "out.ifc"
        out = sp if sp.lower().endswith(".ifc") else (sp.rsplit(".", 1)[0] + ".ifc")
    stats = export(req, out, storey_height=args.storey_height)
    print("[ifc] %s" % out)
    for k, v in sorted(stats.items()):
        print("  %-28s %d" % (k, v))
    counts = validate(out)
    print("[validate] reopen OK: %s" % out)
    for k, v in counts.items():
        print("  %-28s %d" % (k, v))
    if counts["IfcOpeningElement"] > 0 and counts["IfcRelVoidsElement"] == 0:
        print("[ifc][error] 有洞口元素但无 IfcRelVoidsElement — 语义挖洞未生效！")
        return 2
    if counts["IfcOpeningElement"] == 0 and any(
            el.get("openings") for el in (req.get("elements") or [])
            if (el.get("type") or "").lower() == "wall"):
        print("[ifc][error] request 含墙洞但未生成 IfcOpeningElement！")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
