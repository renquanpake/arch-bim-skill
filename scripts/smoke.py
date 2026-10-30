#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke test for arch-bim-skill/brief_to_cad.py. Standard library only."""

import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
CONVERTER = ROOT / "brief_to_cad.py"
BRIEF = ROOT / "examples" / "brief.sample.json"
INTENT = ROOT / "examples" / "intent.sample.json"
TAIL_LINES = 20


class CaseFailure(Exception):
    def __init__(self, reason, proc=None):
        Exception.__init__(self, reason)
        self.reason = reason
        self.proc = proc


def run_converter(args, cwd):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(CONVERTER)] + [str(a) for a in args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd),
    )


def require_exit_zero(proc, label="converter"):
    if proc.returncode != 0:
        raise CaseFailure("%s exited %d" % (label, proc.returncode), proc)


def load_json_file(path, label):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except ValueError as exc:
        raise CaseFailure("%s is not valid JSON: %s" % (label, exc))
    except OSError as exc:
        raise CaseFailure("%s unreadable: %s" % (label, exc))


def same_path(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def case_brief_to_request(tmp):
    out = tmp / "brief_case"
    out.mkdir()
    request_path = out / "request.json"
    dwg_path = out / "model.dwg"
    proc = run_converter(
        ["--brief", BRIEF, "--request", request_path, "--dwg", dwg_path], cwd=out)
    require_exit_zero(proc, "--brief run")

    data = load_json_file(request_path, "request.json")
    elements = data.get("elements")
    if not isinstance(elements, list) or not elements:
        raise CaseFailure("elements missing or empty", proc)
    for idx, el in enumerate(elements):
        if not isinstance(el, dict) or not el.get("type"):
            raise CaseFailure("element[%d] has no type" % idx, proc)

    save_path = data.get("savePath")
    if not save_path:
        raise CaseFailure("savePath missing", proc)
    if not Path(save_path).exists() and not Path(save_path).parent.exists():
        raise CaseFailure("savePath points nowhere: %s" % save_path, proc)
    if not same_path(save_path, dwg_path):
        raise CaseFailure("savePath %s != --dwg %s" % (save_path, dwg_path), proc)

    walls = [el for el in elements if el.get("type") == "wall"]
    if not walls:
        raise CaseFailure("no wall element produced", proc)
    for wall in walls:
        height = wall.get("height")
        if not isinstance(height, (int, float)) or abs(float(height) - 3.0) > 0.01:
            raise CaseFailure(
                "wall %s height %r != 3.0m (mm->m conversion)" % (wall.get("name"), height),
                proc)


def case_plan(tmp):
    out = tmp / "plan_case"
    out.mkdir()
    request_path = out / "request.json"
    proc = run_converter(
        ["--brief", BRIEF, "--request", request_path, "--dwg", out / "model.dwg", "--plan"],
        cwd=out)
    require_exit_zero(proc, "--plan run")

    data = load_json_file(request_path, "request.json")
    types = set(el.get("type") for el in data.get("elements", []) if isinstance(el, dict))
    for wanted in ("polyline2d", "text2d"):
        if wanted not in types:
            raise CaseFailure(
                "--plan output lacks %s (types: %s)" % (wanted, sorted(t for t in types if t)),
                proc)


def case_check(tmp):
    out = tmp / "check_case"
    out.mkdir()
    request_path = out / "request.json"
    proc = run_converter(
        ["--brief", BRIEF, "--request", request_path, "--dwg", out / "model.dwg", "--check"],
        cwd=out)
    require_exit_zero(proc, "--check run")

    report_path = out / "violations.json"
    if not report_path.exists():
        raise CaseFailure("violations.json not created next to request", proc)
    report = load_json_file(report_path, "violations.json")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise CaseFailure("violations.json has no summary object", proc)
    for key in ("total", "errors", "warnings", "passed"):
        if key not in summary:
            raise CaseFailure("summary lacks %r key" % key, proc)


def case_intent(tmp):
    out = tmp / "intent_case"
    out.mkdir()
    request_path = out / "request.json"
    proc = run_converter(
        ["--intent", INTENT, "--request", request_path, "--dwg", out / "model.dwg"], cwd=out)
    require_exit_zero(proc, "--intent run")

    data = load_json_file(request_path, "request.json")
    elements = data.get("elements")
    if not isinstance(elements, list) or not elements:
        raise CaseFailure("--intent produced no elements", proc)


def require_clean_rejection(proc, label):
    if proc.returncode == 0:
        raise CaseFailure("%s should fail but exited 0" % label, proc)
    combined = (proc.stdout or "") + (proc.stderr or "")
    if not combined.strip():
        raise CaseFailure("%s failed without any message" % label, proc)
    for marker in ("Traceback", "SyntaxError"):
        if marker in combined:
            raise CaseFailure("%s crashed instead of validating args (%s)" % (label, marker), proc)


def case_arg_validation(tmp):
    out = tmp / "args_case"
    out.mkdir()
    both = run_converter(
        ["--brief", BRIEF, "--intent", INTENT, "--request", out / "both.json"], cwd=out)
    require_clean_rejection(both, "--brief + --intent")
    neither = run_converter(["--request", out / "neither.json"], cwd=out)
    require_clean_rejection(neither, "no --brief/--intent")
    for name in ("both.json", "neither.json"):
        if (out / name).exists():
            raise CaseFailure("invalid invocation still wrote %s" % name, both)


def case_input_error(tmp):
    out = tmp / "input_error"
    out.mkdir()
    bad = {"project": {"name": "bad", "unit": "mm"},
           "rooms": [{"name": "x", "outline": [[0, 0], [4000, 0]]}]}
    path = out / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    proc = run_converter(["--brief", path, "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    if proc.returncode != 2:
        raise CaseFailure("invalid outline should exit 2, got %d" % proc.returncode, proc)
    combined = (proc.stdout or "") + (proc.stderr or "")
    if "Traceback" in combined:
        raise CaseFailure("invalid outline printed a traceback", proc)
    if (out / "req.json").exists():
        raise CaseFailure("invalid brief still wrote request.json", proc)


def case_bom_input(tmp):
    out = tmp / "bom_case"
    out.mkdir()
    raw = json.loads(BRIEF.read_text(encoding="utf-8-sig"))
    path = out / "bom.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(raw).encode("utf-8"))
    proc = run_converter(["--brief", path, "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    require_exit_zero(proc, "BOM brief")
    data = load_json_file(out / "req.json", "request.json")
    if not data.get("elements"):
        raise CaseFailure("BOM brief produced no elements", proc)


def case_door_window_kind(tmp):
    out = tmp / "dw_kind"
    out.mkdir()
    item = {"width": 900, "height": 2100, "basePoint": [0, 0]}
    brief = {"project": {"name": "dw", "unit": "mm"},
             "rooms": [{"name": "r", "outline": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
             "doors": [dict(item)], "windows": [dict(item)]}
    path = out / "dw.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    proc = run_converter(["--brief", path, "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    require_exit_zero(proc, "door/window brief")
    data = load_json_file(out / "req.json", "request.json")
    kinds = sorted(el["type"] for el in data.get("elements", []) if el.get("type") in ("door", "window"))
    if kinds != ["door", "window"]:
        raise CaseFailure("identical door/window dicts misclassified: %s" % kinds, proc)


def case_intent_rejection(tmp):
    out = tmp / "intent_bad"
    out.mkdir()
    intent = {"project": {"name": "bad", "unit": "m", "floorHeight": 3.0, "floors": 1},
              "footprint": {"width": -12.0, "depth": 10.0},
              "entranceRoom": "hall",
              "rooms": [{"id": "hall", "name": "hall", "type": "living", "floor": 0, "targetArea": 20.0}]}
    path = out / "intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    proc = run_converter(["--intent", path, "--request", out / "req.json"], cwd=out)
    if proc.returncode != 1:
        raise CaseFailure("invalid intent should exit 1, got %d" % proc.returncode, proc)
    if (out / "req.json").exists():
        raise CaseFailure("invalid intent still wrote request.json", proc)


def case_preflight_drops_degenerate(tmp):
    out = tmp / "pf_drop"
    out.mkdir()
    brief = {"project": {"name": "pf", "unit": "mm"},
             "rooms": [{"name": "r", "outline": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
             "walls": [
                 {"id": "ZERO", "start": [100, 100], "end": [100, 100], "thickness": 240, "height": 3000},
                 {"id": "OK", "start": [0, 0], "end": [4000, 0], "thickness": 240, "height": 3000},
             ]}
    path = out / "pf.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    proc = run_converter(["--brief", path, "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    require_exit_zero(proc, "preflight drop case")
    data = load_json_file(out / "req.json", "request.json")
    walls = [el for el in data.get("elements", []) if el.get("type") == "wall"]
    if any(abs(el.get("length", 0)) < 1e-6 for el in walls):
        raise CaseFailure("zero-length wall survived preflight: %s" % walls, proc)
    if not (out / "preflight.json").exists():
        raise CaseFailure("preflight.json not written", proc)
    rep = json.loads((out / "preflight.json").read_text(encoding="utf-8-sig"))
    if rep["summary"]["fixed"] < 1:
        raise CaseFailure("preflight did not record the dropped wall", proc)


def case_preflight_blocks_invalid(tmp):
    out = tmp / "pf_block"
    out.mkdir()
    brief = {"project": {"name": "bad", "unit": "mm"},
             "rooms": [{"name": "flat", "outline": [[0, 0], [0, 0], [0, 0]]}]}
    path = out / "bad.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    proc = run_converter(["--brief", path, "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    if proc.returncode != 1:
        raise CaseFailure("degenerate slab should be blocked (exit 1), got %d" % proc.returncode, proc)
    if (out / "req.json").exists():
        raise CaseFailure("blocked run still wrote request.json", proc)


def case_stairs_2d_units(tmp):
    out = tmp / "stairs_units"
    out.mkdir()
    brief = {"project": {"name": "st", "unit": "mm", "floorHeight": 3000},
             "rooms": [{"name": "r", "outline": [[0, 0], [6000, 0], [6000, 6000], [0, 6000]]}],
             "stairs": [{"name": "S0", "fromFloor": 0, "risers": 16, "run": 250,
                         "rise": 188, "width": 1200, "x": 1000, "y": 3000}]}
    path = out / "st.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    proc = run_converter(["--brief", path, "--plan", "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    require_exit_zero(proc, "stairs unit case")
    data = load_json_file(out / "req.json", "request.json")
    lines = [el for el in data.get("elements", []) if el.get("name") == "S0-T0"]
    if not lines:
        raise CaseFailure("stair tread line S0-T0 missing", proc)
    pts = lines[0]["points"]
    length = abs(pts[1][0] - pts[0][0])
    if not (1.0 < length < 1.4):
        raise CaseFailure("stair tread line length %.4fm, expected ~1.2m (unit bug)" % length, proc)


def case_multifloor_walls(tmp):
    out = tmp / "multifloor"
    out.mkdir()
    rooms = []
    for f in range(2):
        for rid, area in (("living", 30.0), ("dining", 16.0), ("bed", 14.0), ("bed2", 14.0),
                          ("study", 10.0), ("bath", 6.0), ("wc", 4.0)):
            typ = {"bed": "bedroom", "bed2": "bedroom", "bath": "bathroom"}.get(rid, rid)
            rooms.append({"id": "%s%d" % (rid, f), "name": rid, "type": typ,
                          "floor": f, "targetArea": area})
    intent = {"project": {"name": "mf", "unit": "m", "floorHeight": 3.0, "floors": 2},
              "footprint": {"width": 12.0, "depth": 10.0},
              "entranceRoom": "living0",
              "rooms": rooms,
              "stairs": [{"fromFloor": 0, "toFloor": 1}]}
    path = out / "intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    proc = run_converter(["--intent", path, "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    require_exit_zero(proc, "multi-floor intent")
    data = load_json_file(out / "req.json", "request.json")
    els = data.get("elements", [])
    walls = [e for e in els if e.get("type") == "wall"]
    if not walls:
        raise CaseFailure("no walls generated", proc)
    zs = sorted({round(w["basePoint"][2], 3) for w in walls})
    if len(zs) < 2:
        raise CaseFailure("walls not split per floor (elevations=%s)" % zs, proc)
    interior = [w for w in walls if str(w.get("name", "")).startswith("IW-")]
    if len(interior) < 4:
        raise CaseFailure("interior partitions missing (found %d)" % len(interior), proc)
    columns = [e for e in els if e.get("type") == "column"]
    beams = [e for e in els if e.get("type") == "beam"]
    if not columns or not beams:
        raise CaseFailure("structure missing (columns=%d beams=%d)" % (len(columns), len(beams)), proc)
    if not any(e.get("layer") == "A-FOUND" for e in els):
        raise CaseFailure("foundation (A-FOUND) missing", proc)
    floors = [e for e in els if e.get("type") == "floor"]
    rooms_in = len(rooms)
    if len(floors) <= rooms_in:
        raise CaseFailure("stairwell slab hole not applied (floors=%d rooms=%d)" % (len(floors), rooms_in), proc)


def case_ifc_export(tmp):
    try:
        import ifcopenshell  # noqa: F401
    except ImportError:
        return  # 未安装 ifcopenshell 时跳过
    out = tmp / "ifc_case"
    out.mkdir()
    brief = {"project": {"name": "ifc", "unit": "mm", "floorHeight": 3000},
             "rooms": [{"name": "r", "outline": [[0, 0], [6000, 0], [6000, 6000], [0, 6000]]}],
             "walls": [{"id": "W1", "start": [0, 0], "end": [6000, 0],
                        "thickness": 240, "height": 3000}]}
    path = out / "b.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    proc = run_converter(["--brief", path, "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    require_exit_zero(proc, "ifc brief")
    ifc = out / "m.ifc"
    proc2 = subprocess.run([sys.executable, str(ROOT / "ifc_export.py"), str(out / "req.json"), str(ifc)],
                           cwd=str(out), capture_output=True, encoding="utf-8", errors="replace")
    if proc2.returncode != 0:
        raise CaseFailure("ifc_export failed: %s" % (proc2.stderr or "")[-300:], proc2)
    if not ifc.exists() or ifc.stat().st_size < 1000:
        raise CaseFailure("ifc file missing/too small", proc2)
    text = ifc.read_text(encoding="utf-8", errors="ignore")
    if "IFC4" not in text or "IFCWALL" not in text.upper():
        raise CaseFailure("ifc content unexpected", proc2)


def case_plan_sheet(tmp):
    out = tmp / "plan_sheet"
    out.mkdir()
    rooms = []
    for f in range(3):
        for rid, area in (("living", 30.0), ("dining", 16.0), ("bed", 14.0), ("bed2", 14.0),
                          ("study", 10.0), ("bath", 6.0)):
            typ = {"bed": "bedroom", "bed2": "bedroom", "bath": "bathroom"}.get(rid, rid)
            rooms.append({"id": "%s%d" % (rid, f), "name": rid, "type": typ,
                          "floor": f, "targetArea": area})
    intent = {"project": {"name": "sheet", "unit": "m", "floorHeight": 3.0, "floors": 3},
              "footprint": {"width": 12.0, "depth": 10.0},
              "entranceRoom": "living0", "rooms": rooms,
              "stairs": [{"fromFloor": 0, "toFloor": 1}, {"fromFloor": 1, "toFloor": 2}]}
    path = out / "intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    proc = run_converter(["--intent", path, "--plan-sheet", "--plan-only",
                          "--request", out / "req.json", "--dwg", out / "m.dwg"], cwd=out)
    require_exit_zero(proc, "plan sheet")
    data = load_json_file(out / "req.json", "request.json")
    els = data.get("elements", [])
    if any(el.get("type") not in ("polyline2d", "text2d") for el in els):
        raise CaseFailure("--plan-only still contains 3D elements", proc)
    titles = [el.get("text", "") for el in els if el.get("name", "").startswith("SHEET-")]
    if len(titles) != 3:
        raise CaseFailure("expected 3 floor titles, got %s" % titles, proc)


def case_sheets(tmp):
    out = tmp / "sheets"
    out.mkdir()
    rooms = []
    for f in range(2):
        for rid, typ, area in (("living","living",30.0),("dining","dining",16.0),("bed","bedroom",14.0),
                               ("bed2","bedroom",14.0),("study","study",10.0),("bath","bathroom",8.0)):
            rooms.append({"id": "%s%d" % (rid, f), "name": rid, "type": typ, "floor": f, "targetArea": area})
    intent = {"project": {"name": "sh", "unit": "m", "floorHeight": 3.0, "floors": 2},
              "footprint": {"width": 12.0, "depth": 10.0}, "entranceRoom": "living0",
              "rooms": rooms, "stairs": [{"fromFloor": 0, "toFloor": 1}]}
    path = out / "intent.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    proc = run_converter(["--intent", path, "--sheets", "--request", out / "req.json",
                          "--dwg", out / "s.dwg"], cwd=out)
    require_exit_zero(proc, "sheets")
    data = load_json_file(out / "req.json", "request.json")
    els = data.get("elements", [])
    if any(el.get("type") not in ("polyline2d", "text2d") for el in els):
        raise CaseFailure("--sheets must emit only 2D elements", proc)
    frames = [el for el in els if str(el.get("name", "")).endswith("FRAME") and str(el.get("name","")).startswith("SHEET")]
    if not frames:
        raise CaseFailure("no sheet frame generated", proc)


def case_golden(tmp):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "golden.py")],
                          cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise CaseFailure("golden drift: %s" % (proc.stdout or proc.stderr or "")[-300:], proc)


def case_props(tmp):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "props.py"), "--n", "12"],
                          cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise CaseFailure("invariant violation: %s" % (proc.stdout or "")[-300:], proc)


CASES = [
    ("brief_to_request", case_brief_to_request),
    ("plan_elements", case_plan),
    ("check_violations_report", case_check),
    ("intent_to_request", case_intent),
    ("arg_validation", case_arg_validation),
    ("input_error_friendly", case_input_error),
    ("bom_input_tolerated", case_bom_input),
    ("door_window_kind", case_door_window_kind),
    ("intent_rejection_exit", case_intent_rejection),
    ("preflight_drops_degenerate", case_preflight_drops_degenerate),
    ("preflight_blocks_invalid", case_preflight_blocks_invalid),
    ("stairs_2d_units", case_stairs_2d_units),
    ("multifloor_walls", case_multifloor_walls),
    ("ifc_export", case_ifc_export),
    ("plan_sheet", case_plan_sheet),
    ("sheets", case_sheets),
    ("golden_hash", case_golden),
    ("prop_invariants", case_props),
]


def render_tail(proc):
    if proc is None:
        return []
    lines = []
    stdout = (proc.stdout or "").splitlines()
    stderr = (proc.stderr or "").splitlines()
    if stdout:
        lines.append("  stdout (last %d):" % TAIL_LINES)
        lines.extend("  | " + ln for ln in stdout[-TAIL_LINES:])
    if stderr:
        lines.append("  stderr (last %d):" % TAIL_LINES)
        lines.extend("  | " + ln for ln in stderr[-TAIL_LINES:])
    return lines


def main():
    if not CONVERTER.exists():
        print("FAIL setup: %s not found" % CONVERTER)
        print("0 passed, 1 failed")
        return 1

    passed = 0
    failed = 0
    with tempfile.TemporaryDirectory(prefix="archbim-smoke-") as tmpdir:
        tmp = Path(tmpdir)
        for name, func in CASES:
            try:
                func(tmp)
            except CaseFailure as exc:
                failed += 1
                print("FAIL %s: %s" % (name, exc.reason))
                for line in render_tail(exc.proc):
                    print(line)
            except Exception as exc:
                failed += 1
                print("FAIL %s: unexpected %s: %s" % (name, type(exc).__name__, exc))
                for line in traceback.format_exc().rstrip().splitlines()[-TAIL_LINES:]:
                    print("  | " + line)
            else:
                passed += 1
                print("PASS %s" % name)

    print("%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
