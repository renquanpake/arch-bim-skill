#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spine_column.py — Spine-Column 确定性布局模块（精简移植）
================================================================

参考移植：reference/ai-architect-agent/backend/app/planner/
  planner.py（spine 搜索 + 两列房间 + slot）
  zoning.py（每层一个 Circulation 空间，assign_sides 面积平衡分列）
  layout.py（distribute 水填充深度分配）
  geometry.py（layout_column 行堆叠、min_depth、split_spine slot 放置）
  geometry/stairs.py（riser/tread 确定性计算）

设计原则：
  1. LLM 只出 intent（总面积 / 房间清单 / 类型 / 连接 / 入口 / 楼梯需求），
     本模块用确定性代码分配房间深度与位置，不做随机或启发式猜测；
  2. 布局保证：所有房间贴 Spine（走廊）可达（门开向 spine），
     左右列房间贴外墙可开窗；
  3. 楼梯 riser/tread 按层高确定性计算（rise ∈ [150,220]mm，目标 190mm）；
  4. 布局前做意图预校验（面积/长宽/楼层合法性），错误直接返回不落布局；
  5. 输出与现有 brief 结构兼容（unit 对齐、坐标 mm），再走 brief_to_cad.py
     既有链路生成 request.json —— 不破坏现有 DWG 闭环。

坐标系约定（米，内部计算全部用米）：
  建筑左下角 (0,0)；前墙 y=0，后墙 y=depth；左右列分别在 spine 两侧。
  rect 表示为 [x0, y0, x1, y1]。

intent 输入示例见 examples/intent.sample.json。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 布局常量（米；来源 ai-architect-agent geometry.py / stairs.py）
# ---------------------------------------------------------------------------
MIN_COLUMN = 2.6                # 单列最小宽度
SPINE_ACCESS = 1.3              # 房间门洞 + 门前缓冲
DEFAULT_RUN = 0.28              # 商业默认踏面
RESIDENTIAL_RUN = 0.25          # 住宅默认踏面
TARGET_RISE = 0.19
MAX_RISE = 0.22
MIN_RISE = 0.15
DEFAULT_MIN_DEPTH = {
    "garage": 5.0, "wc": 1.5, "storage": 1.2, "ensuite": 1.5,
    "bathroom": 1.6, "utility": 1.6, "shower_room": 1.6, "laundry": 1.6,
}
BASE_MIN_DEPTH = 2.4            # 其余房间默认最小深度
SPINE_EXTRA = 0.2               # spine 两侧墙厚之和
WALL_T = 0.24                   # 外墙/内墙厚（米）

# 房间类型 → 门洞宽度（米；含 900 扇宽，净宽约 830+，满足 P0 规则）
DOOR_WIDTH_BY_TYPE = {
    "wc": 0.9, "ensuite": 0.9, "bathroom": 0.9, "shower_room": 0.9,
    "storage": 0.9, "utility": 0.9, "bedroom": 0.9, "study": 0.9,
    "garage": 2.6, "living": 1.2, "dining": 1.2, "kitchen": 1.2,
}
ENTRANCE_DOOR_WIDTH = 1.5
DOOR_HEIGHT = 2.1
WINDOW_WIDTH_BY_TYPE = {
    "bedroom": 1.5, "living": 1.8, "dining": 1.5, "study": 1.2,
    "kitchen": 1.2, "garage": 0.0,
}
WINDOW_HEIGHT = 1.5
WINDOW_SILL = 0.9

# 排序优先级：garage 最前（贴前墙），front 组其次，any 中间，rear 组最后
ROW_PRIORITY = {
    "garage": 0, "entrance": 1, "hall": 1, "lobby": 1, "landing": 1,
    "living": 2, "dining": 2, "kitchen": 2, "study": 2, "office": 2,
    "bedroom": 3, "bathroom": 3, "wc": 3, "ensuite": 3, "utility": 3,
    "laundry": 3, "storage": 4, "other": 5,
}
SLOT_TYPES = {"wc", "storage", "utility", "laundry", "ensuite"}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class RoomPlan:
    id: str
    name: str
    type: str
    floor: int
    target_area: float          # m²
    preferred_side: str = "any"  # left / right / front / rear / any
    side: str = ""               # left / right / slot / spine
    rect: Optional[List[float]] = None  # [x0,y0,x1,y1] 米
    window_on_exterior: bool = True


@dataclass
class StairPlan:
    from_floor: int
    to_floor: int
    width: float                 # m
    run: float                   # m
    risers: int
    rise: float                  # m
    x: float = 0.0
    y: float = 0.0

    @property
    def going_length(self) -> float:
        return self.run * (self.risers - 1)


# ---------------------------------------------------------------------------
# 意图预校验（P1：布局前必做）
# ---------------------------------------------------------------------------
MAX_FILL = 1.10
MIN_FILL = 0.70


def validate_intent(intent: dict) -> List[str]:
    """返回错误列表；空列表表示意图合法。"""
    errors: List[str] = []
    project = intent.get("project", {})
    fp = intent.get("footprint", {})
    width = fp.get("width", 0.0)
    depth = fp.get("depth", 0.0)
    floors = int(project.get("floors", 1))
    if width <= 0 or depth <= 0:
        errors.append("footprint 宽/深必须为正数（当前 %.2f x %.2f m）" % (width, depth))
    if floors < 1:
        errors.append("楼层数 floors 必须 ≥ 1")
    if width and width < MIN_COLUMN * 2 + SPINE_ACCESS + SPINE_EXTRA:
        errors.append("建筑宽度 %.2fm 过窄，无法容纳双列 + spine（至少 %.2fm）"
                      % (width, MIN_COLUMN * 2 + SPINE_ACCESS + SPINE_EXTRA))

    rooms = intent.get("rooms", [])
    if not rooms:
        errors.append("房间清单 rooms 不能为空")
    entrance = intent.get("entranceRoom", "")
    room_ids = [r.get("id", "") for r in rooms]

    # 单位一致性：planner 内部统一以米计算（footprint/floorHeight/roof 均米）
    fh = project.get("floorHeight", 3.0)
    if fh <= 0:
        errors.append("floorHeight 必须 > 0")
    elif fh > 10:
        errors.append("floorHeight=%.1f 疑似以 mm 传入，planner 统一以米为单位（如 3.0）" % fh)
    roof = intent.get("roof", {})
    for k in ("thickness", "height", "overhang"):
        v = roof.get(k)
        if v is not None and v > 5:
            errors.append("roof.%s=%.1f 疑似以 mm 传入，planner 统一以米为单位（如 0.12）" % (k, v))
    for r in rooms:
        floor = int(r.get("floor", 0))
        if floor < 0 or floor >= floors:
            errors.append("房间 %s 所在楼层 %d 越界（共 %d 层）"
                          % (r.get("id", "?"), floor, floors))
        if r.get("targetArea", 0.0) <= 0:
            errors.append("房间 %s 目标面积 targetArea 必须 > 0" % r.get("id", "?"))
        if r.get("type") == "garage" and floor != 0:
            errors.append("车库 %s 必须位于首层" % r.get("id", "?"))
    if entrance and entrance not in room_ids:
        errors.append("入口房间 entranceRoom='%s' 不在房间清单中" % entrance)

    # 每层面积填充率检查：首层必须达到 MIN_FILL（确定性布局补足），
    # 其余楼层仅要求有房间（面积不足时按目标面积布置，允许留白）。
    by_floor: Dict[int, float] = {}
    for r in rooms:
        f = int(r.get("floor", 0))
        by_floor[f] = by_floor.get(f, 0.0) + r.get("targetArea", 0.0)
    if width and depth:
        fp_area = width * depth
        for f in range(floors):
            total = by_floor.get(f, 0.0)
            if total <= 0:
                errors.append("第 %d 层没有房间" % f)
            elif total > fp_area * MAX_FILL:
                errors.append("第 %d 层房间面积合计 %.0fm² 超出占地 %.0fm² 的 %d%% 上限"
                              % (f, total, fp_area, int(MAX_FILL * 100)))
            elif f == 0 and total < fp_area * MIN_FILL:
                errors.append("第 1 层房间面积合计 %.0fm² 不足占地 %.0fm² 的 %d%%（将自动放大房间补足）"
                              % (total, fp_area, int(MIN_FILL * 100)))

    # 楼梯连续性（基础项）
    stairs = intent.get("stairs", [])
    for s in stairs:
        if s.get("toFloor", 0) != s.get("fromFloor", 0) + 1:
            errors.append("楼梯 %s 必须恰好上升一层（from %d → to %d）"
                          % (s.get("name", "?"), s.get("fromFloor", 0), s.get("toFloor", 0)))
    return errors


# ---------------------------------------------------------------------------
# 确定性布局
# ---------------------------------------------------------------------------

def _min_depth(room_type: str) -> float:
    return DEFAULT_MIN_DEPTH.get(room_type, BASE_MIN_DEPTH)


def _door_width(room_type: str) -> float:
    return DOOR_WIDTH_BY_TYPE.get(room_type, 0.9)


def _window_width(room_type: str) -> float:
    return WINDOW_WIDTH_BY_TYPE.get(room_type, 1.2)


def _distribute(target_total: float, counts: List[float], minimum: float,
                max_total: float) -> List[float]:
    """水填充比例分配：给每个房间目标深度 = 面积/列宽，低于 minimum 的补齐，
    超出 max_total 时按比例压缩。返回深度列表（与 counts 同序）。"""
    n = len(counts)
    if n == 0:
        return []
    # 先给最小深度
    depths = [max(c, minimum) for c in counts]
    # 若总和超过可用深度，按比例压缩（不低于 minimum）
    total = sum(depths)
    if total > max_total and total > minimum * n:
        scale = max(0.0, (max_total - minimum * n) / max(1e-9, total - minimum * n))
        depths = [minimum + (d - minimum) * scale for d in depths]
    return depths


def _assign_sides(rooms: List[RoomPlan], connections: List[dict]) -> Dict[str, str]:
    """按面积平衡分左右列；open-plan 连接组尽量同侧；preferredSide 优先。"""
    side_of: Dict[str, str] = {}

    # 组：连接的房间同侧
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    ids = [r.id for r in rooms]
    for i in ids:
        parent.setdefault(i, i)
    for c in connections:
        a, b = c.get("roomA", ""), c.get("roomB", "")
        if a in parent and b in parent:
            union(a, b)

    groups: Dict[str, List[RoomPlan]] = {}
    for r in rooms:
        groups.setdefault(find(r.id), []).append(r)

    left_total = 0.0
    right_total = 0.0
    for root, members in groups.items():
        # 有显式 preferredSide 的成员决定组侧向
        forced = None
        for m in members:
            if m.preferred_side in ("left", "right"):
                forced = m.preferred_side
                break
        area = sum(m.target_area for m in members)
        if forced:
            side = forced
        else:
            # 放到当前累计面积较小的一侧，避免单侧空置
            side = "left" if left_total <= right_total else "right"
        for m in members:
            side_of[m.id] = side
        if side == "left":
            left_total += area
        else:
            right_total += area
    return side_of


def _order_rows(rooms: List[RoomPlan]) -> List[RoomPlan]:
    """行排序：garage 最前，front 组次之，any 居中，rear 靠后。"""
    def key(r: RoomPlan) -> Tuple[int, float]:
        pri = ROW_PRIORITY.get(r.type, ROW_PRIORITY["other"])
        # 面积大的靠前（同优先级下）
        return (pri, -r.target_area)
    return sorted(rooms, key=key)


def _layout_columns(rooms: List[RoomPlan], col_width: float, col_x0: float,
                    max_depth: float) -> None:
    """把一列房间从前到后堆叠，分配深度 rect。

    深度分配：先满足各房间最小深度，超出可用深度时在“最小深度之上”
    等比压缩；所有房间最小深度之和都放不下时按最小深度排并接受超界
    （由调用方/预校验提示用户加大占地）。
    """
    y = 0.0
    min_depths = [_min_depth(r.type) for r in rooms]
    if not rooms:
        return
    targets = [r.target_area / col_width for r in rooms]
    # 目标深度与最小深度取大
    depths = [max(t, md) for t, md in zip(targets, min_depths)]
    total = sum(depths)
    total_min = sum(min_depths)
    if total > max_depth and total_min < max_depth - 1e-9:
        # 压缩的是“最小之上的余量”，不跌破最小深度
        extra_budget = max_depth - total_min
        extras = [d - md for d, md in zip(depths, min_depths)]
        sum_extra = sum(extras)
        if sum_extra > 1e-9:
            scale = extra_budget / sum_extra
            depths = [md + (d - md) * scale for d, md in zip(depths, min_depths)]
    for r, d in zip(rooms, depths):
        r.rect = [col_x0, y, col_x0 + col_width, y + d]
        y += d


def calculate_stair(floor_height: float, run: Optional[float] = None,
                    building_type: str = "commercial") -> StairPlan:
    """确定性楼梯计算：riser 数取接近目标 190mm 且不超过 220mm 的值。

    floor_height 单位米；run 缺省按建筑类型（住宅 0.25 / 商业 0.28）。
    返回 risers / rise / run；宽度按类型（住宅 0.9 / 商业 1.1）。
    """
    if floor_height <= 0:
        raise ValueError("floor_height 必须 > 0")
    if run is None:
        run = RESIDENTIAL_RUN if building_type in ("residential", "apartment") else DEFAULT_RUN
    risers = max(2, round(floor_height / TARGET_RISE),
                 math.ceil(floor_height / MAX_RISE - 1e-9))
    rise = floor_height / risers
    width = 0.9 if building_type in ("residential", "apartment") else 1.1
    return StairPlan(from_floor=0, to_floor=1, width=width, run=run,
                     risers=risers, rise=rise)


def plan_building(intent: dict) -> dict:
    """主入口：intent → {brief, layout, notes}。

    任何预校验错误直接以 {'errors': [...]} 返回，不产出 brief。
    """
    errors = validate_intent(intent)
    if errors:
        return {"errors": errors, "brief": None, "layout": None, "notes": []}

    project = intent.get("project", {})
    unit = (project.get("unit") or "mm").lower()
    floors = int(project.get("floors", 1))
    floor_height = float(project.get("floorHeight", 3.0))   # 米
    building_type = project.get("buildingType", "commercial")
    fp = intent.get("footprint", {})
    W = float(fp.get("width", 0.0))
    D = float(fp.get("depth", 0.0))
    entrance_id = intent.get("entranceRoom", "")
    roof_spec = intent.get("roof", {})

    notes: List[str] = []
    brief_rooms: List[dict] = []
    layout_rooms: List[dict] = []
    layout_doors: List[dict] = []
    layout_corridors: List[dict] = []
    layout_stairs: List[dict] = []
    fire_compartments: List[dict] = []
    s = 1000.0 if unit == "mm" else 1.0  # 米 → brief 单位

    # ---- 1. 组装各层 RoomPlan（复制，保留 intent 语义） ----
    by_floor: Dict[int, List[RoomPlan]] = {}
    for i, r in enumerate(intent.get("rooms", [])):
        rp = RoomPlan(
            id=r.get("id", "R%d" % i),
            name=r.get("name", r.get("id", "R%d" % i)),
            type=r.get("type", "other"),
            floor=int(r.get("floor", 0)),
            target_area=float(r.get("targetArea", 0.0)),
            preferred_side=r.get("preferredSide", "any"),
        )
        by_floor.setdefault(rp.floor, []).append(rp)

    # ---- 2. 每层：spine（Circulation）+ 分列 + 布局 ----
    for level in range(floors):
        level_rooms = by_floor.get(level, [])
        # 2.1 找出/创建 spine 房间
        spine_candidates = [r for r in level_rooms
                            if r.type in ("entrance", "hall", "lobby", "landing", "circulation")]
        if spine_candidates:
            spine = spine_candidates[0]
            spine.name = spine.name
        else:
            spine = RoomPlan(id="circulation-%d" % level, name="走道" if level == 0 else "楼梯间",
                             type="hall" if level == 0 else "landing",
                             floor=level, target_area=SPINE_ACCESS * 1.0)
            level_rooms.append(spine)
        spine.side = "spine"

        # 2.2 其余房间分左右列
        others = [r for r in level_rooms if r is not spine]
        connections = intent.get("connections", [])
        side_of = _assign_sides(others, connections)
        left = [r for r in others if side_of[r.id] == "left"]
        right = [r for r in others if side_of[r.id] == "right"]

        # 2.3 楼梯（level 0 → 1 及后续楼层）
        stair = None
        if level + 1 < floors:
            stair = calculate_stair(floor_height, None, building_type)
            stair.from_floor = level
            stair.to_floor = level + 1
        # spine 宽度：楼梯宽 + 双侧通道
        spine_w = SPINE_ACCESS + (stair.width if stair else 0.0) + SPINE_ACCESS
        spine_w = max(spine_w, 1.6)

        # 2.4 列宽：左右各 (W - spine_w) / 2；单侧空列时并入对侧
        col_w = max(0.0, (W - spine_w) / 2.0)
        if not left and right:
            col_w = max(0.0, W - spine_w)
        elif not right and left:
            col_w = max(0.0, W - spine_w)
        if col_w < MIN_COLUMN:
            notes.append("第 %d 层列宽 %.2fm 小于最小列宽 %.2fm，已按 %.2fm 处理"
                         % (level, col_w, MIN_COLUMN, MIN_COLUMN))
            col_w = MIN_COLUMN
            # 重算 spine 宽度避免重叠
            spine_w = max(spine_w, W - 2 * col_w)

        spine_x0 = (W - spine_w) / 2.0
        left_x0 = 0.0
        right_x0 = spine_x0 + spine_w

        # 2.5 布局左右列
        left_ordered = _order_rows(left)
        right_ordered = _order_rows(right)
        _layout_columns(left_ordered, col_w, left_x0, D)
        _layout_columns(right_ordered, col_w, right_x0, D)
        for r in left_ordered:
            r.side = "left"
        for r in right_ordered:
            r.side = "right"

        # 2.6 slot：小房间放 spine 后
        slot_room = None
        for r in others:
            if r.type in SLOT_TYPES and r not in left_ordered and r not in right_ordered:
                slot_room = r
                break
        # 本精简版：SLOT 房间已进分列流程，不再单独处理（避免复杂度）；
        # 若所有房间已分配则跳过。保证 spine 后部留给楼梯。

        # 2.7 spine rect：从前墙到后墙
        spine.rect = [spine_x0, 0.0, spine_x0 + spine_w, D]

        # 2.8 楼梯位置：spine 后部（y = D - stair.going_length 起）
        if stair:
            stair.y = max(0.0, D - stair.going_length)
            stair.x = spine_x0 + (spine_w - stair.width) / 2.0

        # ---- 3. 导出该层数据 ----
        for r in [spine] + left_ordered + right_ordered:
            x0, y0, x1, y1 = r.rect
            layout_rooms.append({
                "id": r.id, "name": r.name, "type": r.type, "floor": level,
                "side": r.side, "targetArea": r.target_area,
                "rect": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
            })
            # brief room（坐标转 brief 单位）；spine 含楼梯时楼板开洞（拆块）
            rects = [[x0, y0, x1, y1]]
            if r.side == "spine" and stair is not None:
                hole = [stair.x, stair.y,
                        stair.x + stair.width, stair.y + stair.going_length]
                rects = _rect_minus_hole([x0, y0, x1, y1], hole)
            for ri, (rx0, ry0, rx1, ry1) in enumerate(rects):
                brief_rooms.append({
                    "name": r.name if len(rects) == 1 else "%s-%d" % (r.name, ri + 1),
                    "type": r.type,
                    "outline": [
                        [round(rx0 * s, 2), round(ry0 * s, 2)],
                        [round(rx1 * s, 2), round(ry0 * s, 2)],
                        [round(rx1 * s, 2), round(ry1 * s, 2)],
                        [round(rx0 * s, 2), round(ry1 * s, 2)],
                    ],
                    "floorThickness": 0.15 * s,
                    "elevation": level * floor_height * s,
                })

        # 走廊视图：spine 即主走廊（入口端 = 出口）
        entrance_on_front = any(r.type in ("entrance", "hall", "lobby") for r in level_rooms) and level == 0
        layout_corridors.append({
            "name": spine.name, "width": spine_w, "length": D,
            "deadEnd": not entrance_on_front, "oneWay": spine_w < 1.4,
            "floor": level,
        })

        # 房间门（开向 spine）：非入口房间在 spine 墙开 1 门洞
        for r in left_ordered + right_ordered:
            layout_doors.append({
                "name": "%s-D" % r.name, "room": r.id, "floor": level,
                "width": _door_width(r.type), "leafWidth": _door_width(r.type),
            })
        # 入口大门（前墙）
        if level == 0:
            layout_doors.append({
                "name": "ENTRANCE-D", "room": "entrance", "floor": 0,
                "width": ENTRANCE_DOOR_WIDTH, "leafWidth": ENTRANCE_DOOR_WIDTH,
            })

        # 楼梯视图
        if stair:
            layout_stairs.append({
                "name": "S%d" % level, "fromFloor": stair.from_floor,
                "toFloor": stair.to_floor, "width": stair.width,
                "rise": stair.rise, "run": stair.run, "risers": stair.risers,
                "goingLength": stair.going_length,
                "x": stair.x, "y": stair.y,
            })

        # 防火分区（基础项：整层近似一个分区）
        fire_compartments.append({
            "name": "FC-L%d" % level, "area_m2": round(W * D, 1),
            "level": "ground" if level == 0 else "up",
        })

    # ---- 4. 生成墙 / 门窗洞 / 屋顶（brief 结构，兼容现有转换器） ----
    walls = _build_walls(layout_rooms, brief_rooms, layout_doors, unit, floor_height, floors)
    roof = None
    if roof_spec:
        roof = {
            "type": roof_spec.get("type", "gable"),
            "thickness": roof_spec.get("thickness", 0.12) * s,
            "height": roof_spec.get("height", 1.5) * s,
            "overhang": roof_spec.get("overhang", 0.3) * s,
        }
        if roof_spec.get("elevation") is not None:
            roof["elevation"] = roof_spec["elevation"] * s
        else:
            # 屋顶默认位于顶层墙顶标高：floors * 层高
            roof["elevation"] = floors * floor_height * s

    brief = {
        "project": {
            "name": project.get("name", "auto-layout"),
            "unit": unit,
            "floorHeight": floor_height * s,
            "floors": floors,
            "buildingType": building_type,
            "floorCount": floors,
        },
        "rooms": brief_rooms,
        "walls": walls,
        "doors": [],
        "windows": [],
        "columns": [],
        "beams": [],
        "stairs": [
            {
                "name": "S%d" % st["fromFloor"], "fromFloor": st["fromFloor"],
                "toFloor": st["toFloor"], "width": st["width"] * s,
                "rise": st["rise"] * s, "run": st["run"] * s,
                "risers": st["risers"],
                "x": st["x"] * s, "y": st["y"] * s,
            }
            for st in layout_stairs
        ],
    }
    if roof:
        brief["roof"] = roof

    # ---- 4.5 结构 / 外壳 / 机电（UniFormat A/B/D 补齐）----
    from .structure import build_structure
    lvl0 = [r for r in layout_rooms if int(r["floor"]) == 0] or layout_rooms
    env = (min(r["rect"][0] for r in lvl0), min(r["rect"][1] for r in lvl0),
           max(r["rect"][2] for r in lvl0), max(r["rect"][3] for r in lvl0))
    sp_rect = next((r["rect"] for r in lvl0 if r["side"] == "spine"), None)
    if sp_rect is None:
        mid = (env[0] + env[2]) / 2.0
        sp_rect = [mid - 0.8, env[1], mid + 0.8, env[3]]
    st = build_structure(env, sp_rect, floors, floor_height, unit)
    from .fittings import (door_window_panels, balconies, sanitary_fixtures, elevator_car,
                           stair_railings, furniture, ceiling_services, roof_drainage, site_works,
                           floor_finishes, appliances, mep_branches, site_extras, roof_buildup)
    boxes = list(st["boxes"])
    boxes += door_window_panels(walls, unit)
    boxes += balconies(layout_rooms, env, floors, floor_height, unit)
    boxes += sanitary_fixtures(layout_rooms, unit, floor_height)
    boxes += elevator_car(sp_rect, env, unit)
    boxes += stair_railings(layout_stairs, unit, floor_height)
    boxes += furniture(layout_rooms, floor_height, unit)
    boxes += ceiling_services(layout_rooms, sp_rect, floors, floor_height, unit)
    boxes += roof_drainage(env, floors * floor_height, unit)
    boxes += site_works(env, unit)
    boxes += floor_finishes(layout_rooms, floor_height, unit)
    boxes += appliances(layout_rooms, floor_height, unit)
    boxes += mep_branches(layout_rooms, sp_rect, floors, floor_height, unit)
    boxes += site_extras(env, unit)
    boxes += roof_buildup(env, floors * floor_height, unit)
    brief["columns"] = st["columns"]
    brief["beams"] = st["beams"]
    brief["boxes"] = boxes
    brief["grid"] = st["grid"]

    # 语义层：楼层标高 + 房间中心（供 Revit Level/Room）
    brief["levels"] = [{"name": "L%d" % lv, "elevation": round(lv * floor_height * s, 2)}
                       for lv in range(floors)]
    spaces = []
    for r in layout_rooms:
        rx0, ry0, rx1, ry1 = r["rect"]
        spaces.append({
            "name": r["name"],
            "number": "%d%02d" % (int(r["floor"]) + 1, len(spaces) % 99 + 1),
            "level": int(r["floor"]),
            "center": [round((rx0 + rx1) / 2.0 * s, 2), round((ry0 + ry1) / 2.0 * s, 2)],
            "outline": [[round(rx0 * s, 2), round(ry0 * s, 2)], [round(rx1 * s, 2), round(ry0 * s, 2)],
                        [round(rx1 * s, 2), round(ry1 * s, 2)], [round(rx0 * s, 2), round(ry1 * s, 2)]],
        })
    brief["roomSpaces"] = spaces

    layout = {
        "rooms": layout_rooms,
        "doors": layout_doors,
        "corridors": layout_corridors,
        "stairs": layout_stairs,
        "fireCompartments": fire_compartments,
        "footprint": {"width": W, "depth": D},
    }

    notes.append("Spine-Column 布局完成：spine=%s，左列 %d 房，右列 %d 房"
                 % (spine.name if floors else "", len(left_ordered), len(right_ordered)))
    return {"brief": brief, "layout": layout, "notes": notes, "errors": []}



def _rect_minus_hole(rect, hole):
    """矩形挖去一个内嵌矩形，返回最多 4 块（楼梯间楼板开洞用）。"""
    x0, y0, x1, y1 = rect
    hx0, hy0, hx1, hy1 = hole
    hx0, hy0 = max(hx0, x0), max(hy0, y0)
    hx1, hy1 = min(hx1, x1), min(hy1, y1)
    if hx1 <= hx0 or hy1 <= hy0:
        return [rect]
    out = []
    if hy0 - y0 > 1e-6:
        out.append([x0, y0, x1, hy0])
    if y1 - hy1 > 1e-6:
        out.append([x0, hy1, x1, y1])
    if hx0 - x0 > 1e-6:
        out.append([x0, hy0, hx0, hy1])
    if x1 - hx1 > 1e-6:
        out.append([hx1, hy0, x1, hy1])
    return out or [rect]


def _build_walls(layout_rooms: List[dict], brief_rooms: List[dict],
                 layout_doors: List[dict], unit: str,
                 floor_height: float = 3.0, floors: int = 1) -> List[dict]:
    """由布局矩形生成**逐层完整墙体**：外墙 4 段/层 + 所有房间隔墙（去重）。

    与旧实现相比：
      * 外墙按层切段（elevation = level*floor_height，height = floor_height），
        不再一根 90m 通高墙 —— 剖面/楼层统计才有意义；
      * 房间矩形的内部边全部生成隔墙（共享边只生成一次），不再只有 spine 两侧；
      * 门窗按层挂到对应墙段，偏移量按该墙段自身方向计算。
    所有坐标返回 brief 单位（mm/m 按 unit）。
    """
    s = 1000.0 if unit == "mm" else 1.0
    tol = 1e-6
    walls: List[dict] = []
    rect_by_id = {r["id"]: r["rect"] for r in layout_rooms}
    side_by_id = {r["id"]: r["side"] for r in layout_rooms}
    levels = sorted({int(r["floor"]) for r in layout_rooms})
    if not levels:
        return []
    floor_h = floor_height if floor_height > 0 else 3.0

    def emit(id_, start_pt, end_pt, thickness, elevation, height, openings=None):
        w = {
            "id": id_,
            "start": [round(start_pt[0] * s, 2), round(start_pt[1] * s, 2)],
            "end": [round(end_pt[0] * s, 2), round(end_pt[1] * s, 2)],
            "thickness": round(thickness * s, 2),
            "height": round(height * s, 2),
            "elevation": round(elevation * s, 2),
        }
        if openings:
            w["openings"] = openings
        walls.append(w)
        return w

    def edge_key(a, b):
        pa, pb = (round(a[0], 3), round(a[1], 3)), (round(b[0], 3), round(b[1], 3))
        return tuple(sorted([pa, pb]))

    def along(origin, direction, point):
        """点在墙段方向上的投影距离（米）"""
        dx = direction[0] - origin[0]
        dy = direction[1] - origin[1]
        L = math.hypot(dx, dy)
        if L <= tol:
            return 0.0
        return ((point[0] - origin[0]) * dx + (point[1] - origin[1]) * dy) / L

    for level in levels:
        rooms = [r for r in layout_rooms if int(r["floor"]) == level]
        if not rooms:
            continue
        x0 = min(r["rect"][0] for r in rooms)
        y0 = min(r["rect"][1] for r in rooms)
        x1 = max(r["rect"][2] for r in rooms)
        y1 = max(r["rect"][3] for r in rooms)
        z = level * floor_h
        tag = "L%d" % level

        # ---- 1) 外墙（4 段/层，方向固定：S 向右、E 向上、N 向左、W 向下）----
        ext = {}
        ext["S"] = emit("EXT-S-" + tag, (x0, y0), (x1, y0), WALL_T, z, floor_h)
        ext["E"] = emit("EXT-E-" + tag, (x1, y0), (x1, y1), WALL_T, z, floor_h)
        ext["N"] = emit("EXT-N-" + tag, (x1, y1), (x0, y1), WALL_T, z, floor_h)
        ext["W"] = emit("EXT-W-" + tag, (x0, y1), (x0, y0), WALL_T, z, floor_h)

        # ---- 2) 内部隔墙：所有房间边的并集（去重），跳过外墙边 ----
        edges = {}
        for r in rooms:
            rx0, ry0, rx1, ry1 = r["rect"]
            for a, b in ((( rx0, ry0), (rx1, ry0)), ((rx1, ry0), (rx1, ry1)),
                         ((rx1, ry1), (rx0, ry1)), ((rx0, ry1), (rx0, ry0))):
                on_env = ((abs(a[0] - x0) < 1e-4 and abs(b[0] - x0) < 1e-4) or
                          (abs(a[0] - x1) < 1e-4 and abs(b[0] - x1) < 1e-4) or
                          (abs(a[1] - y0) < 1e-4 and abs(b[1] - y0) < 1e-4) or
                          (abs(a[1] - y1) < 1e-4 and abs(b[1] - y1) < 1e-4))
                if on_env:
                    continue
                edges.setdefault(edge_key(a, b), (a, b))

        interior_index = {}
        for i, (k, (a, b)) in enumerate(sorted(edges.items())):
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.05:
                continue
            w = emit("IW-%s-%02d" % (tag, i), a, b, 0.12, z, floor_h)
            interior_index[k] = w

        # ---- 3) 房间门：挂到房间朝向 spine 的那道隔墙 ----
        for d in layout_doors:
            if d.get("room") == "entrance":
                continue
            if int(d.get("floor", 0)) != level:
                continue
            rect = rect_by_id.get(d.get("room"))
            side = side_by_id.get(d.get("room"))
            if not rect or side not in ("left", "right"):
                continue
            dx_edge = rect[2] if side == "left" else rect[0]
            a, b = (dx_edge, rect[1]), (dx_edge, rect[3])
            target = interior_index.get(edge_key(a, b))
            if not target:
                continue
            door_w = d.get("width", 0.9)
            center_y = rect[1] + 0.5
            off = along(a, b, (dx_edge, center_y)) - door_w / 2.0
            wall_len = math.hypot(target["end"][0] - target["start"][0],
                                  target["end"][1] - target["start"][1]) / s
            off = max(0.0, min(off, wall_len - door_w))
            target.setdefault("openings", []).append({
                "type": "door", "startX": round(off * s, 2),
                "width": round(door_w * s, 2),
                "height": round(DOOR_HEIGHT * s, 2), "sill": 0.0,
            })

        # ---- 4) 窗：贴外墙的房间按层开窗 ----
        for r in rooms:
            side = r["side"]
            if side not in ("left", "right"):
                continue
            rr = r["rect"]
            ww = _window_width_by_type(layout_rooms, r["id"])
            if ww <= 0:
                continue
            cy = (rr[1] + rr[3]) / 2.0
            if side == "left" and abs(rr[0] - x0) < 1e-4:
                wall, start_pt, dir_pt = ext["W"], (x0, y1), (x0, y0)
                center = (x0, cy)
            elif side == "right" and abs(rr[2] - x1) < 1e-4:
                wall, start_pt, dir_pt = ext["E"], (x1, y0), (x1, y1)
                center = (x1, cy)
            else:
                continue
            length = math.hypot(dir_pt[0] - start_pt[0], dir_pt[1] - start_pt[1])
            off = along(start_pt, dir_pt, center) - ww / 2.0
            off = max(0.0, min(off, length - ww))
            wall.setdefault("openings", []).append({
                "type": "window", "startX": round(off * s, 2),
                "width": round(ww * s, 2), "height": round(1.5 * s, 2),
                "sill": round(0.9 * s, 2),
            })

    # ---- 5) 入口门：首层南墙，开向 spine 中线 ----
    entrance_doors = [d for d in layout_doors if d.get("room") == "entrance"]
    if entrance_doors:
        lvl_rooms = [r for r in layout_rooms if int(r["floor"]) == 0]
        if lvl_rooms:
            x0 = min(r["rect"][0] for r in lvl_rooms)
            x1 = max(r["rect"][2] for r in lvl_rooms)
            y0 = min(r["rect"][1] for r in lvl_rooms)
            spine = next((r for r in lvl_rooms if r["side"] == "spine"), None)
            cx = ((spine["rect"][0] + spine["rect"][2]) / 2.0) if spine else (x0 + x1) / 2.0
            ed = entrance_doors[0]
            off = max(0.0, min(cx - x0 - ed["width"] / 2.0, (x1 - x0) - ed["width"]))
            south = next((w for w in walls if w["id"] == "EXT-S-L0"), None)
            if south:
                south.setdefault("openings", []).append({
                    "type": "door", "startX": round(off * s, 2),
                    "width": round(ed["width"] * s, 2),
                    "height": round(DOOR_HEIGHT * s, 2), "sill": 0.0,
                })

    return walls


def _window_width_by_type(layout_rooms: List[dict], room_id: str) -> float:
    for r in layout_rooms:
        if r["id"] == room_id:
            return WINDOW_WIDTH_BY_TYPE.get(r.get("type", "other"), 1.2)
    return 1.2
