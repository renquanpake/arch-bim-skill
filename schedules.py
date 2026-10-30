# -*- coding: utf-8 -*-
"""
schedules.py — 从 CAD request.json（单位=米）生成统计表，纯标准库。

两类输出：
  1. 房间面积表 build_room_schedule(request) -> csv:str
     列：层号,编号,名称,面积m2,周长m
     数据源优先 request["rooms"]（{name,number,level,outline}）；
     缺省时回退到 elements 里 type=floor/room 的轮廓（level 取 elevation）。
  2. 工程量快估 build_quantity_takeoff(request) -> csv:str
     按 类型+图层 汇总：混凝土体积(m3)、墙体净面积(m2, 扣洞口)、
     楼板/屋面面积(m2)与体积、洞口数（门/窗分列）。

用法：
  python schedules.py request.json out.csv --kind rooms
  python schedules.py request.json out.csv --kind quant

CSV 以 UTF-8-BOM 写出（Excel 直开），行间分隔符 = chr(13)+chr(10)。
注意：request.json 由 brief_to_cad.py 产出，长度字段已全部换算为米。
"""

import json
import math
import os
import sys

COMMA = ","
NL = chr(13) + chr(10)  # CRLF，不用字面转义避免源码问题

# 类型缺省图层（与 brief_to_cad.py 的 LAYER_* 常量对齐）
DEFAULT_LAYER = {
    "wall": "A-WALL",
    "column": "A-COLUMN",
    "beam": "A-BEAM",
    "box": "A-CONC",
    "floor": "A-FLOOR",
    "roof": "A-ROOF",
}


# ---------- 基础几何 ----------

def polygon_area_perimeter(outline):
    """Shoelace 面积 + 周长（米制轮廓；首尾重合点自动去重，不闭合则补闭合边）。"""
    pts = []
    for p in outline or []:
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if len(pts) >= 2 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
        pts.pop()
    n = len(pts)
    if n < 3:
        return 0.0, 0.0
    a = 0.0
    per = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
        per += math.hypot(x2 - x1, y2 - y1)
    return abs(a) / 2.0, per


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float(default)


def _num(v, nd):
    """定小数位并去掉无意义尾零（12.500 -> 12.5，0.00 -> 0）。"""
    s = ("%%.%df" % nd) % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _level_str(lv):
    if lv is None or lv == "":
        return "0"
    if isinstance(lv, float) and lv.is_integer():
        return str(int(lv))
    return str(lv)


def _csv_cell(v):
    s = str(v)
    if any(ch in s for ch in (',', '"', NL, chr(10), chr(13))):
        return '"' + s.replace('"', '""') + '"'
    return s


def _csv_text(rows):
    return NL.join(COMMA.join(_csv_cell(c) for c in row) for row in rows) + NL


# ---------- 1. 房间面积表 ----------

ROOM_HEADER = ["层号", "编号", "名称", "面积m2", "周长m"]


def _room_records(request):
    """优先 request['rooms']；回退 elements 的 floor/room 轮廓。"""
    rooms = request.get("rooms") or []
    recs = []
    if rooms:
        for r in rooms:
            recs.append({
                "level": r.get("level", r.get("elevation", 0)),
                "number": r.get("number", ""),
                "name": r.get("name", ""),
                "outline": r.get("outline", []),
            })
    else:
        for el in request.get("elements") or []:
            if el.get("type") in ("room", "floor") and el.get("outline"):
                recs.append({
                    "level": el.get("level", el.get("elevation", 0)),
                    "number": el.get("number", ""),
                    "name": el.get("name", ""),
                    "outline": el["outline"],
                })
    for rec in recs:
        rec["area"], rec["perimeter"] = polygon_area_perimeter(rec["outline"])
    return recs


def build_room_schedule(request):
    """request(dict) -> CSV 文本：每间面积/周长 + 按层小计 + 总计。"""
    recs = _room_records(request)
    # 仅按层号排序（sort 稳定，层内保持输入顺序）
    recs.sort(key=lambda r: _f(r["level"], 0.0))

    rows = [ROOM_HEADER]
    total_a = total_p = 0.0
    by_level = []
    for rec in recs:
        lv = _level_str(rec["level"])
        if by_level and by_level[-1][0] == lv:
            by_level[-1][1] += rec["area"]
            by_level[-1][2] += rec["perimeter"]
            by_level[-1][3] += 1
        else:
            by_level.append([lv, rec["area"], rec["perimeter"], 1])
        rows.append([lv, rec["number"], rec["name"],
                     _num(rec["area"], 2), _num(rec["perimeter"], 2)])
        total_a += rec["area"]
        total_p += rec["perimeter"]

    if len(by_level) > 1:  # 多层时才出层小计，单层直接看总计
        for lv, a, p, cnt in by_level:
            rows.append([lv, "", "%s 小计(%d间)" % (lv, cnt), _num(a, 2), _num(p, 2)])
    if recs:
        rows.append(["总计", "", "%d 间" % len(recs), _num(total_a, 2), _num(total_p, 2)])
    if not recs:
        rows.append(["", "", "(无房间数据)", "", ""])
    return _csv_text(rows)


# ---------- 2. 工程量快估 ----------

QTY_HEADER = ["类别", "图层", "数量", "面积m2", "体积m3", "备注"]


def _group_add(groups, key, layer, cnt=0, area=0.0, vol=0.0, note=""):
    g = groups.setdefault((key, layer), {"cnt": 0, "area": 0.0, "vol": 0.0, "notes": []})
    g["cnt"] += cnt
    g["area"] += area
    g["vol"] += vol
    if note and note not in g["notes"]:
        g["notes"].append(note)


def build_quantity_takeoff(request):
    """request(dict) -> CSV 文本：按 类型+图层 汇总工程量，末尾加混凝土小计与洞口统计。"""
    elements = request.get("elements") or []
    groups = {}
    conc_vol = 0.0          # 混凝土：柱+梁+基础(box)
    openings = {}           # type -> [count, area]
    skipped = 0

    for el in elements:
        et = el.get("type", "")
        layer = el.get("layer") or DEFAULT_LAYER.get(et, "0")
        if et == "column":
            v = _f(el.get("width")) * _f(el.get("depth")) * _f(el.get("height"))
            _group_add(groups, "柱(混凝土)", layer, 1, 0.0, v)
            conc_vol += v
        elif et == "beam":
            # beam 可能是 length 或 depth 表达第三维
            d = el.get("length")
            third = _f(d) if d is not None else _f(el.get("depth"))
            v = third * _f(el.get("width")) * _f(el.get("height"))
            _group_add(groups, "梁(混凝土)", layer, 1, 0.0, v)
            conc_vol += v
        elif et == "box":
            v = _f(el.get("width")) * _f(el.get("depth")) * _f(el.get("height"))
            label = "基础(混凝土)" if "FOUND" in str(layer).upper() else "箱体构件(混凝土)"
            _group_add(groups, label, layer, 1, 0.0, v)
            conc_vol += v
        elif et == "wall":
            gross = _f(el.get("length")) * _f(el.get("height"))
            net = gross
            for o in el.get("openings") or []:
                oa = _f(o.get("width")) * _f(o.get("height"))
                net -= oa
                ot = o.get("type", "opening")
                rec = openings.setdefault(ot, [0, 0.0])
                rec[0] += 1
                rec[1] += oa
            net = max(net, 0.0)  # 洞口超限保护
            v = net * _f(el.get("thickness"))
            _group_add(groups, "墙体(净面积)", layer, 1, net, v, "面积已扣洞口")
        elif et in ("floor", "slab"):
            a, _p = polygon_area_perimeter(el.get("outline", []))
            _group_add(groups, "楼板(面积)", layer, 1, a, a * _f(el.get("thickness")))
        elif et == "roof":
            a, _p = polygon_area_perimeter(el.get("outline", []))
            _group_add(groups, "屋面(面积)", layer, 1, a, a * _f(el.get("thickness")),
                       "按投影轮廓计，坡屋面实际面积更大")
        elif et in ("door", "window"):  # 独立洞口元素（未挂墙）也计数
            ot = "门" if et == "door" else "窗"
            rec = openings.setdefault(ot, [0, 0.0])
            rec[0] += 1
            rec[1] += _f(el.get("width")) * _f(el.get("height"))
        else:
            skipped += 1  # polyline2d/text2d 等图元不计量

    rows = [QTY_HEADER]
    order = ["柱(混凝土)", "梁(混凝土)", "基础(混凝土)", "箱体构件(混凝土)",
             "墙体(净面积)", "楼板(面积)", "屋面(面积)"]
    for key in order:
        for (k, layer), g in sorted(groups.items()):
            if k != key:
                continue
            rows.append([k, layer, str(g["cnt"]), _num(g["area"], 2),
                         _num(g["vol"], 3), "; ".join(g["notes"])])
    # 洞口（墙上的门/窗）
    for ot in ("door", "window"):
        if ot in openings:
            cnt, area = openings[ot]
            cn = {"door": "门", "window": "窗"}.get(ot, ot)
            layer = "A-DOOR" if ot == "door" else "A-WINDOW"
            rows.append(["洞口(%s)" % cn, layer,
                         str(cnt), _num(area, 2), "", "樘数；面积=宽x高"])
    for ot in sorted(openings):  # 其它非常规洞口类型
        if ot not in ("door", "window"):
            cnt, area = openings[ot]
            rows.append(["洞口(%s)" % ot, "-", str(cnt), _num(area, 2), "", "樘数"])
    rows.append(["混凝土小计", "柱+梁+基础", "", "", _num(conc_vol, 3),
                 "不含楼板/屋面/墙"])
    if skipped:
        rows.append(["说明", "-", str(skipped), "", "", "非计量图元(text2d/polyline2d等)已跳过"])
    return _csv_text(rows)


# ---------- CLI ----------

def load_request(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    kind = "rooms"
    if "--kind" in argv:
        i = argv.index("--kind")
        if i + 1 >= len(argv):
            print("错误: --kind 后需跟 rooms|quant|both", file=sys.stderr)
            return 2
        kind, argv = argv[i + 1], argv[:i] + argv[i + 2:]
    for a in argv[:]:
        if a.startswith("--kind="):
            kind = a.split("=", 1)[1]
            argv.remove(a)
    if kind not in ("rooms", "quant", "both"):
        print("错误: --kind 只能是 rooms|quant|both，收到 %r" % kind, file=sys.stderr)
        return 2
    positional = [a for a in argv if not a.startswith("-")]
    if len(positional) < 2:
        print(__doc__.strip().splitlines()[-2])
        print("用法: python schedules.py request.json out.csv --kind rooms|quant")
        return 2
    req_path, out_path = positional[0], positional[1]

    request = load_request(req_path)
    parts = []
    if kind in ("rooms", "both"):
        parts.append(build_room_schedule(request))
    if kind in ("quant", "both"):
        if kind == "both":
            parts.append(NL)
        parts.append(build_quantity_takeoff(request))
    csv_text = "".join(parts)

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with open(out_path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(csv_text)

    lines = csv_text.split(NL)
    print("[schedules] kind=%s -> %s (%d 行, UTF-8-BOM/CRLF)" % (kind, out_path, len(lines)))
    for ln in lines[:8]:
        if ln:
            print("  " + ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
