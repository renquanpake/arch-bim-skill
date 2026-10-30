# 协同出图：一份 brief → 3D + 每层 2D + IFC

## 1. 一张图说清

```
                intent.json / brief.json
                          │
                brief_to_cad.py（唯一数据源）
                          │  request.json（构件 + 语义 + 出图配置）
        ┌─────────────────┼──────────────────────┬───────────────────┐
        ▼                 ▼                      ▼                   ▼
  AutoCAD(accoreconsole) Revit(私有桌面)      ifc_export.py      2D 平面
  3D DWG + 2D 平面图      3D RVT + 每层平面 DWG   标准 IFC4          (同 request)
```

- **同一份 request** 被 CAD / Revit / IFC 三端消费，不存在"两边对不上"。
- 2D 平面有两种产出：
  1. **CAD 批量平面图**：`--plan-sheet`，每层一张，网格排版进同一个 DWG；
  2. **Revit 每层视图**：`--revit-drawings <dir>`，建 `PLAN-L0/L1/...` 视图并各自导出 DWG。

## 2. 命令

```powershell
# 3D + 每层平面排版（CAD 一张大图）
python brief_to_cad.py --intent intent.json --plan-sheet --dwg D:\models\plans.dwg

# 只要 2D（不写 3D 构件）
python brief_to_cad.py --intent intent.json --plan-sheet --plan-only --dwg D:\models\plans.dwg

# 3D RVT + Revit 每层平面 DWG
python brief_to_cad.py --intent intent.json --revit-drawings D:\models\drawings --dwg D:\models\b.rvt

# 3D DWG（CAD）
python brief_to_cad.py --intent intent.json --dwg D:\models\b.dwg

# IFC
python ifc_export.py request.json D:\models\b.ifc
```

## 2.5 立面 / 剖面 / 门窗表（新增）

```powershell
# CAD：4 立面 + 1-1 剖面（示意投影，与 3D 同一 request）
python brief_to_cad.py --intent intent.json --elevations --dwg elevations.dwg
# 门窗统计表 CSV（UTF-8-BOM，Excel 直开）
python brief_to_cad.py --intent intent.json --schedule schedule.csv --dwg m.dwg
# Revit：每层平面 DWG + 1-1 剖面 DWG
python brief_to_cad.py --intent intent.json --revit-drawings D:\models\drawings --dwg b.rvt
```

## 2.6 施工图套图（图框 + 标题栏）

```powershell
python brief_to_cad.py --intent intent.json --sheets --sheet-size A1 --sheet-scale 100 --dwg sheets.dwg
```
`sheets.py`：A1/A2/A3 图框、真 1/scale 缩放排版、平面→A-101 / 立面→A-201 / 剖面→A-301 图号、
图签栏（工程名/图名/图号/比例/日期/阶段）。套图走 report 校验（不删 1:100 缩放后的细线）。

## 2.7 标注包 v1（模型空间 + 纸面）

- 模型空间（`annotations.py`→`brief_to_cad`）：标高符号(等腰直角三角+横线+三位小数)、轴号圆(φ8 16 边形+跳 I/O/Z)、房间三行标签(名称/面积/标高)、门窗编号(M0921/C1515)、剖切符号。
- 纸面（`sheets.py`）：指北针(φ24 圆+针+北，读 project.northAngle)、图名粗下划线+比例、左下角内嵌**面积表/工程量表**(table+csv_to_table，数据来自 schedules.py)。
- 全 2D(polyline2d/text2d)，CAD/Revit 插件通吃；套图走 report 校验不删细线。

## 2.8 标注包 v2（门/窗/索引/会签）

- 模型空间（`annotations.py`）：`door_swing` 门扇开启线+1/4 弧、`window_lines` 窗三线+洞口 jamb、`index_symbol` 索引圆 φ10（详图引用，备用）。
- 纸面（`sheets.py`）：标题栏左侧**会签栏**（设计/制图/校对/审核 4 行 + 签名空列）。
- 3 层套图 DWG 含：图框+标题栏+会签栏+指北针+内嵌面积/工程量表+标高/轴号圆/房间三行标签/门窗编号/门弧/窗三线/剖切符号。

## 2.9 Revit 原生图纸（ViewSheet）+ 真消隐立面

```powershell
# Revit 原生图纸：ViewSheet + Viewport + 手绘图签（无标题栏族也能出）
python brief_to_cad.py --intent intent.json --revit-drawings D:\models\revit-sheets --revit-sheets --dwg b.rvt

# 真消隐立面（背面剔除 + 共享边去重，替代 AABB 示意）
python brief_to_cad.py --intent intent.json --elev-true --dwg b.dwg
```
- Revit 侧：每层一个 `ViewSheet`（A-101/A-102/A-103），`Viewport` 摆平面视图 + 剖面，右下角用详图线+文字**手绘图签**（工程/图名/图号/1:100/日期），逐图导出 DWG。
- 立面：`elevations2.py` 做背面剔除 + 相邻可见面共享边去重，输出"可见面线框"级消隐（非线段级遮挡裁剪）。

## 3. 实测（3 层样板）

| 产物 | 结果 |
|---|---|
| `complete-3d.rvt` | 426 构件、0 error、455.5 m³、Level 3 / Room 22 / Grid 7 |
| `complete-3d.dwg` | 456 个 Solid3d、0 error |
| `complete-3d.ifc` | IFC4、432 产品（含 22 个 IfcSpace）、0.3 MB |
| `complete-3d-plans.dwg` | 168 实体（122 线 + 42 文字）、3 张平面排版、0 Solid3d |
| `complete-3d-elevations.dwg` | **4 立面 + 1-1 剖面**：2800 实体、0 error |
| `complete-3d-schedule.csv` | 门窗表：1 门 + 5 窗型号、数量、洞口面积 |
| `drawings\PLAN-L1/L2/Level 1.dwg` | Revit 每层平面 DWG（3 张） |
| `drawings\SECT-1-1.dwg` | Revit 1-1 剖面 DWG |

30 层：RVT 3825 构件、0 error、3304.7 m³、30 Level / 210 Room、65 秒、峰值 4.0 GB。

## 4. 一致性的来源

- 几何：三端都从 `request.elements` 取数，单位统一为米（CAD 1 单位=1m，Revit 内部乘 mm→ft）。
- 楼层/房间/轴网：`request.levels` / `rooms` / `grids`，Revit 建真实对象，CAD 用于 2D 标注。
- 出图：Revit 用 `drawings.dir` 建视图导出；CAD 用 `--plan-sheet` 排版，二者互不依赖。

## 5. 已知差异

- CAD 平面是"线框+文字标注"，不是关联尺寸（真 DIMENSION 需另跑 LISP 步骤）。
- Revit 视图名跟随 Level 名（首个 Level 在样板里叫 `Level 1`）。
- 2D 只出平面；立面/剖面尚未生成。
