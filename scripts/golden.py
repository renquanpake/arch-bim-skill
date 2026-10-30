#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""golden.py — 几何黄金哈希回归（防任何无声的几何漂移）

用法：
  python scripts/golden.py            # 校验（不一致 → 退出码 1）
  python scripts/golden.py --update   # 更新基线（确认差异是预期的）
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "tests", "golden.json")

FIXTURES = [
    ("intent.sample", ["--intent", os.path.join(ROOT, "examples", "intent.sample.json"), "--plan"]),
    ("brief.sample", ["--brief", os.path.join(ROOT, "examples", "brief.sample.json"), "--plan"]),
    ("intent.sample_elev_true",
     ["--intent", os.path.join(ROOT, "examples", "intent.sample.json"), "--elev-true"]),
]


def digest(value):
    """只对 elements 做哈希：savePath 属显式输入，不算漂移。"""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build(tmp, name, args):
    req = os.path.join(tmp, name + ".json")
    dwg = os.path.join(tmp, name + ".dwg")   # 固定路径，保证确定性
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "brief_to_cad.py")]
                          + args + ["--request", req, "--dwg", dwg],
                          cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("[golden] %s 生成失败: %s" % (name, (proc.stderr or "").strip()[-300:]))
    with open(req, "r", encoding="utf-8-sig") as f:
        return digest(json.load(f).get("elements"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="golden-") as tmp:
        current = {name: build(tmp, name, fargs) for name, fargs in FIXTURES}

    baseline = {}
    if os.path.exists(GOLDEN):
        with open(GOLDEN, "r", encoding="utf-8") as f:
            baseline = json.load(f).get("fixtures", {})

    if args.update or not baseline:
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w", encoding="utf-8") as f:
            json.dump({"fixtures": current}, f, ensure_ascii=False, indent=2)
        print("[golden] 基线已写入 %s（%d 个）" % (GOLDEN, len(current)))
        return 0

    bad = []
    for name, h in current.items():
        old = baseline.get(name)
        status = "OK" if old == h else "DRIFT"
        if old != h:
            bad.append(name)
        print("  %-28s %s  now=%s base=%s" % (name, status, h[:12], (old or "-")[:12]))

    if bad:
        print("[golden] 几何漂移: %s" % ", ".join(bad))
        print("         确认是有意修改后执行: python scripts/golden.py --update")
        return 1
    print("[golden] 全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
