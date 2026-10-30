#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""props.py — 属性不变量测试（随机 brief → 生成+校验后必须满足的硬约束）

跑 N 个随机/恶意 brief，断言：
  P1 不崩溃（转换器 + preflight 全流程无未捕获异常）
  P2 无 NaN/Inf，坐标不失控（|v| < 1e6）
  P3 所有构件尺寸 > 0（退化件已被丢弃或修好）
  P4 每个洞口都在墙内：0 <= startX，startX+width <= 墙长，sill+height <= 墙高
  P5 输出构件数 <= 输入构件数（修复只会删/改，不会凭空造）

用法：python scripts/props.py [--n 50] [--seed 7]
"""

import argparse
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "planner"))

import brief_to_cad as b2c      # noqa: E402
import preflight                # noqa: E402

TOL = 1e-6


def _finite(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def _elements(req):
    return [e for e in req.get("elements", []) if isinstance(e, dict)]


def check_invariants(req, n_in):
    errs = []
    els = _elements(req)
    if len(els) > n_in:
        errs.append("P5 构件数增加: %d > %d" % (len(els), n_in))
    for e in els:
        t = (e.get("type") or "").lower()
        # P2 数值有限
        for k, v in e.items():
            if isinstance(v, float) and not math.isfinite(v):
                errs.append("P2 %s.%s 非有限" % (e.get("name"), k))
            if isinstance(v, (list, tuple)):
                for x in v:
                    if isinstance(x, (int, float)) and (not math.isfinite(x) or abs(x) > 1e6):
                        errs.append("P2 %s.%s 坐标失控" % (e.get("name"), k))
        # P3 尺寸
        if t in ("box", "column"):
            if min(float(e.get("width", 0)), float(e.get("depth", 0)), float(e.get("height", 0))) <= TOL:
                errs.append("P3 %s 尺寸<=0" % e.get("name"))
        elif t == "beam":
            if min(float(e.get("length", 0)), float(e.get("width", 0)), float(e.get("height", 0))) <= TOL:
                errs.append("P3 %s 尺寸<=0" % e.get("name"))
        elif t == "wall":
            L = float(e.get("length", 0))
            H = float(e.get("height", 0))
            if L <= TOL or H <= TOL:
                errs.append("P3 %s 墙含糊" % e.get("name"))
            for i, o in enumerate(e.get("openings") or []):
                sx, w = float(o.get("startX", 0)), float(o.get("width", 0))
                sill, h = float(o.get("sill", 0)), float(o.get("height", 0))
                if w <= TOL:
                    continue
                if sx < -TOL or sx + w > L + 1e-3:
                    errs.append("P4 %s#%d 洞口越界 %.3f+%.3f>%.3f" % (e.get("name"), i, sx, w, L))
                if sill < -TOL or sill + h > H + 1e-3:
                    errs.append("P4 %s#%d 洞口超高 %.3f+%.3f>%.3f" % (e.get("name"), i, sill, h, H))
    return errs


def random_brief(rng):
    """随机 + 恶意 edge case：零长墙、极薄板、越界洞、重叠洞、负标高。"""
    w = rng.choice([3, 5, 6, 8, 12])
    d = rng.choice([3, 4, 6, 10])
    walls = []
    for i in range(rng.randint(0, 4)):
        sx, sy = rng.uniform(-2, w), rng.uniform(-2, d)
        L = rng.choice([0.0, 0.001, 0.05, 1.0, 3.0, 6.0])
        ang = rng.choice([0, 90, 30])
        ex = sx + L * math.cos(math.radians(ang))
        ey = sy + L * math.sin(math.radians(ang))
        th = rng.choice([0.001, 0.12, 0.24, 0.4])
        hh = rng.choice([0.0, 0.01, 2.8, 3.0])
        ops = []
        for _ in range(rng.randint(0, 3)):
            ops.append({"type": rng.choice(["door", "window"]),
                        "startX": rng.choice([-1.0, 0.0, L * 0.5, L * 1.5]),
                        "width": rng.choice([0.0, 0.001, 0.9, 3.0, 50.0]),
                        "height": rng.choice([0.0, 0.5, 2.1, 9.0]),
                        "sill": rng.choice([-1.0, 0.0, 0.9, 9.0])})
        walls.append({"id": "W%d" % i, "start": [sx, sy], "end": [ex, ey],
                      "thickness": th, "height": hh, "openings": ops})
    rooms = [{"name": "R%d" % i,
              "outline": [[0, 0], [w, 0], [w, d], [0, d]],
              "elevation": rng.choice([0, 0, 3000])} for i in range(rng.randint(0, 2))]
    cols = [{"id": "C%d" % i, "basePoint": [rng.uniform(0, w), rng.uniform(0, d)],
             "width": rng.choice([0.0, 0.001, 0.4]), "depth": rng.choice([0.001, 0.4]),
             "height": rng.choice([0.0, 3.0])} for i in range(rng.randint(0, 2))]
    return {"project": {"name": "props", "unit": "mm", "floorHeight": 3000},
            "rooms": rooms, "walls": walls, "columns": cols}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    failures = 0
    for i in range(args.n):
        brief = random_brief(rng)
        n_out = 0
        try:
            req, unit = b2c.convert(brief, "x.dwg")
            n_in = len(req["elements"])
            fixed, rep = preflight.check_request(req, target="both", fix=True)
            n_out = len(fixed["elements"])
            errs = check_invariants(fixed, n_in)
        except Exception as exc:                     # P1
            errs = ["P1 崩溃 %s: %s" % (type(exc).__name__, exc)]
        if errs:
            failures += 1
            print("FAIL case %d: %s" % (i, "; ".join(errs[:3])))
    print("%d cases, %d failed" % (args.n, failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
