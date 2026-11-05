# -*- coding: utf-8 -*-
"""图纸数字化对账：truth.json(尺寸链/门窗表真值) vs request.json(待交付模型)。

用法:
  python scripts\\verify_geometry.py --truth truth.json --request request.json
闸门: 墙轴对齐偏差>100mm=嫌疑; 墙厚∉设计集=嫌疑; 洞口宽与最近编号标称差>100mm=剔除。
退出码: 0=全部干净可交付; 1=存在嫌疑/剔除项(不得声称完成)。
"""
import argparse, json, math, sys


def load(p):
    with open(p, encoding="utf-8-sig") as f:
        return json.load(f)


def dist_to_axis(v, axes, half_th):
    """坐标 v 到最近轴线的偏差，允许 0(轴上) 或 ±半厚(轴边对齐) 两种合法关系。"""
    best = None
    for a in axes:
        d = abs(v - a)
        for cand in (d, abs(d - half_th)):
            if best is None or cand < best:
                best = cand
    return best


def snap_code(w, table):
    if not table:
        return None, None
    m = min(table, key=lambda o: abs(o["width"] - w))
    return m.get("code", "?"), abs(m["width"] - w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", required=True)
    ap.add_argument("--request", required=True)
    ap.add_argument("--axis-tol", type=float, default=100.0)
    ap.add_argument("--open-tol", type=float, default=100.0)
    a = ap.parse_args()

    truth = load(a.truth)
    req = load(a.request)
    gx = [float(x) for x in truth.get("grid_x", [])]
    gy = [float(y) for y in truth.get("grid_y", [])]
    th_set = [float(t) for t in truth.get("wall_th", [])]
    otbl = truth.get("openings", [])

    suspects, rejects, ok_w, ok_o = [], [], 0, 0
    for el in req.get("elements", []):
        if el.get("type") != "wall":
            continue
        s, e = el.get("start"), el.get("end")
        if not s or not e:
            suspects.append((el.get("name"), "no start/end"))
            continue
        th = float(el.get("thickness", 200))
        if th_set and min(abs(th - t) for t in th_set) > 20:
            suspects.append((el.get("name"), "墙厚 %.0f ∉ 设计集 %s" % (th, th_set)))
            continue
        if abs(s[1] - e[1]) < 1 and gx:          # H 墙: y 恒定, 查纵轴
            dev = dist_to_axis(s[1], gy, th / 2)
        elif abs(s[0] - e[0]) < 1 and gy:        # V 墙: x 恒定, 查横轴
            dev = dist_to_axis(s[0], gx, th / 2)
        else:
            dev = 0.0                            # 斜墙暂不审查
        if dev > a.axis_tol:
            suspects.append((el.get("name"), "轴线偏差 %.0fmm" % dev))
            continue
        ok_w += 1
        for op in el.get("openings", []):
            w = float(op.get("width", 0))
            code, delta = snap_code(w, otbl)
            if code is None:
                ok_o += 1
                continue
            if delta > a.open_tol:
                rejects.append((el.get("name"), "洞口 %.0f vs %s 标称 差%.0f" % (w, code, delta)))
            else:
                ok_o += 1

    print("墙: 通过 %d | 嫌疑 %d ; 洞口: 通过 %d | 剔除 %d" % (ok_w, len(suspects), ok_o, len(rejects)))
    for n, why in suspects:
        print("  嫌疑墙 %s: %s" % (n, why))
    for n, why in rejects:
        print("  剔除洞口 %s: %s" % (n, why))
    verdict = "PASS 可交付" if not suspects and not rejects else "FAIL 对账不干净，不得声称完成"
    print("结论: " + verdict)
    sys.exit(0 if not suspects and not rejects else 1)


if __name__ == "__main__":
    main()
