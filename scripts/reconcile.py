#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reconcile.py — 跨端体积对账：解析值 vs AutoCAD 实测 vs Revit 实测

同一 request 喂给 CAD 与 Revit，三方的体积必须互相收敛，否则说明某一端几何/单位出错。

用法：
  python scripts/reconcile.py --request req.json \
      [--cad-result cad-result.json] [--revit-result revit-result.json] [--tol 0.02]
退出码：任一端与解析值偏差超过 --tol → 1
"""

import argparse
import json
import math
import os
import re
import sys


def poly_area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def analytic_volume(req):
    """按 request 元素解析计算体积（米）。"""
    total = 0.0
    detail = {}
    for e in req.get("elements", []):
        t = (e.get("type") or "").lower()
        v = 0.0
        if t == "wall":
            L = float(e.get("length", 0)); H = float(e.get("height", 0))
            T = float(e.get("thickness", 0))
            v = L * H * T
            for o in e.get("openings") or []:
                w = float(o.get("width", 0)); h = float(o.get("height", 0))
                if w > 0 and h > 0 and h <= H + 1e-6:
                    v -= min(w, L) * min(h, H) * T
        elif t == "beam":
            v = float(e.get("length", 0)) * float(e.get("width", 0)) * float(e.get("height", 0))
        elif t in ("column", "box"):
            v = float(e.get("width", 0)) * float(e.get("depth", 0)) * float(e.get("height", 0))
        elif t in ("floor", "roof"):
            v = poly_area([(float(p[0]), float(p[1])) for p in e.get("outline", [])]) * float(e.get("thickness", 0))
            # gable/shed 屋面：Revit/CAD 是截面棱柱，解析值按棱柱补正
            if t == "roof" and (e.get("roofType") in ("gable", "shed")):
                pts = [(float(p[0]), float(p[1])) for p in e.get("outline", [])]
                if len(pts) >= 3:
                    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                    W, L = max(ys) - min(ys), max(xs) - min(xs)
                    th = float(e.get("thickness", 0.12)); hh = float(e.get("height", 0))
                    if e.get("roofType") == "gable":
                        prof = [(0, 0), (W, 0), (W, th), (W / 2, hh), (0, th)]
                    else:
                        prof = [(0, 0), (W, 0), (W, hh + th), (0, th)]
                    v = poly_area(prof) * L
        total += max(v, 0.0)
        detail[t] = detail.get(t, 0.0) + max(v, 0.0)
    return total, detail


def cad_volume(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    try:
        j = json.loads(raw)
    except Exception:
        return None
    msg = j.get("message", "") or ""
    m = re.search(r"体积合计\s*=\s*([0-9.]+)\s*m3", msg)
    if not m:
        m = re.search(r"体积合计=([0-9.]+)", msg)
    return float(m.group(1)) if m else None


def revit_volume(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        j = json.load(f)
    v = j.get("volumeM3")
    return float(v) if v is not None else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--cad-result", default="")
    ap.add_argument("--revit-result", default="")
    ap.add_argument("--tol", type=float, default=None, help="统一阈值（覆盖分端阈值）")
    ap.add_argument("--tol-cad", type=float, default=0.01,
                    help="CAD 阈值：Solid3d 真实 mass properties，应≈0")
    ap.add_argument("--tol-revit", type=float, default=0.05,
                    help="Revit 阈值：体积是账面累加(非 mass properties)，放宽")
    args = ap.parse_args(argv)

    with open(args.request, "r", encoding="utf-8-sig") as f:
        req = json.load(f)
    base, detail = analytic_volume(req)
    print("解析体积 : %.3f m3" % base)
    for k, v in sorted(detail.items(), key=lambda x: -x[1]):
        print("           %-10s %.3f m3 (%.1f%%)" % (k, v, 100 * v / base if base else 0))

    bad = []
    rows = []
    if args.cad_result:
        cv = cad_volume(args.cad_result)
        rows.append(("AutoCAD", cv))
    if args.revit_result:
        rv = revit_volume(args.revit_result)
        rows.append(("Revit", rv))

    for name, v in rows:
        if v is None:
            print("%-8s : 无数据（result 未含体积）" % name)
            continue
        tol = args.tol if args.tol is not None else (
            args.tol_cad if name == "AutoCAD" else args.tol_revit)
        d = (v - base) / base if base else 0.0
        flag = "OK" if abs(d) <= tol else "MISMATCH"
        print("%-8s : %.3f m3  Δ=%+.2f%%  (阈值 %.0f%%)  %s" % (name, v, 100 * d, tol * 100, flag))
        if abs(d) > tol:
            bad.append(name)

    if bad:
        print("[reconcile] 体积不收敛: %s" % ", ".join(bad))
        return 1
    print("[reconcile] 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
