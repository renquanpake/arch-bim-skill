# 发单前几何校验（preflight）

> 目的：把"进到目标软件才报错、还留下半成品"的问题前移到 request.json 阶段。
> 纯标准库、无 GUI、无外部依赖（毫秒级，常驻内存 < 20MB）。

## 1. 两种容差

| 目标 | 容差 | 依据 |
|---|---|---|
| cad | 1e-6 m | AutoCAD 对极短线段基本无感 |
| revit | 0.0016 m | `Application.ShortCurveTolerance`(≈0.8mm) × 2 安全边界 |
| both | 取较大值 | 一份 request 双端消费时的最严格值 |

## 2. 规则

| 规则 | 级别 | 处理 |
|---|---|---|
| `wall_degenerate` 墙长/墙厚/墙高 ≤ 容差 | error | fix 时丢弃该墙 |
| `opening_out_of_wall` `startX < 0` 或 `startX+width > 墙长` | error | 需修（转换器已在 clamp_openings 夹紧） |
| `opening_out_of_height` `sill+height > 墙高` | error | 需修 |
| `opening_degenerate` 洞口宽 ≤ 容差 | error | 需修 |
| `slab_degenerate` outline 顶点 < 3 或面积 ≈ 0 | error | 需修 |
| `box_degenerate` width/depth/height ≤ 容差 | error | fix 时丢弃 |
| `negative_elevation` 底标高 < 0 | error | 需修 |
| `polyline_short_segment` 线段低于容差 | warning | fix 时剔除短段，点不足 2 个则丢弃元素 |
| `polyline_degenerate` 有效点 < 2 | warning | fix 时丢弃 |
| `duplicate_renamed` 结构构件重名 | warning | fix 时改名 `name~2`（保证结果可对账） |
| `unknown_type` 协议外类型 | error | 需修（否则插件必报 unsupported type） |
| `text_empty` / `text_degenerate` | error | 需修 |

## 3. 用法

```powershell
# 独立校验（只报告）
python preflight.py request.json --target revit

# 自动修复并输出报告
python preflight.py request.json --fix --out preflight.json
```

转换器默认已内置（`--validate fix`）：

```powershell
python brief_to_cad.py --brief brief.json --dwg out.dwg
#   发单校验 : 通过 target=both 容差1.60mm，构件 44 -> 44，修复 0，error 0，warning 0

python brief_to_cad.py --brief brief.json --validate report   # 只报告不修
python brief_to_cad.py --brief brief.json --validate off      # 跳过
python brief_to_cad.py --brief brief.json --allow-invalid     # 校验失败仍写出
```

未通过时：打印 error 明细 + 写 `preflight.json`，**不写出 request**（退出码 1）。

## 4. 实战抓到的 bug（2026-10-27）

preflight 上线当天就抓到两处同类"二次单位换算"：

1. 尺寸标注线：bbox 已是米，又除 1000 → 6.0m 变 0.006m（文字仍写 6000）
2. 楼梯 2D 踏步线：坐标已是米，又除 1000 → 1.2m 踏步线变 1.2mm

第 2 条正是 Revit 日志里 32 条 `Curve length is too small` + 64 条 `SKIP` 的根因。
修好后同一 intent 复生成：206 构件、0 error、0 修复。
