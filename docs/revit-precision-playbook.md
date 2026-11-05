---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_a2fbe7a7d8ed11f1a0ca525400de85a5
    ReservedCode1: UA/BHpyNRZpFL/17L0TqZ+sgCSCZ8J8tYfUAMhWoz5PIbbl1ldpgpAI8p0FUsv171G86YzW+3KP0bcbbhd49sFnWq6wywly9OKAosmF7Jkg5g883WbGwSv0bwmVmnSG6YUTTsQ3Wht6P5DjUSgIjBijx42Z+w0bV9yf0mViqtKVS7R7b7imc5LT7cmE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_a2fbe7a7d8ed11f1a0ca525400de85a5
    ReservedCode2: UA/BHpyNRZpFL/17L0TqZ+sgCSCZ8J8tYfUAMhWoz5PIbbl1ldpgpAI8p0FUsv171G86YzW+3KP0bcbbhd49sFnWq6wywly9OKAosmF7Jkg5g883WbGwSv0bwmVmnSG6YUTTsQ3Wht6P5DjUSgIjBijx42Z+w0bV9yf0mViqtKVS7R7b7imc5LT7cmE=
---

# Revit 精准建模实操手册（A02 实战沉淀）

来源：A02 住宅楼 B1+1F 两层建模（2026-11），从 PDF 施工图到 rvt 全流程，含 v1→v3 三轮修补。
定位：**SKILL.md §1C 的展开细节**。遇到"模型数量翻倍/门窗错层/标高错位/加签卡死"先查本文。

---

## 1. 插件签名：改 DLL 后的强制动作

**症状**：改完 RevitAddin.dll 直接跑无头，Revit 弹"未签名加载项"TaskDialog，私有桌面上按钮点不到，流程卡到 10 分钟超时。

**根因**：任何一次重新编译都换掉签名，之前的信任记录失效。

**硬规则**：改代码 → 重编 → **重签 → 重信任** → 才允许启动 Revit。

```powershell
# 1) 重签（证书 CN=Marvis BIM，已在本机证书库）
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -like '*CN=Marvis BIM*' }
Set-AuthenticodeSignature -FilePath "D:\cad\cad-ai-plugin\RevitAddin\bin\Release\RevitAddin.dll" -Certificate $cert

# 2) 导出并信任（机器级 TrustedPublisher + Root）
Export-Certificate -Cert $cert -FilePath "$env:TEMP\marvis-bim.cer"
Import-Certificate -FilePath "$env:TEMP\marvis-bim.cer" -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
Import-Certificate -FilePath "$env:TEMP\marvis-bim.cer" -CertStoreLocation Cert:\LocalMachine\Root

# 3) 校验
Get-AuthenticodeSignature "D:\cad\cad-ai-plugin\RevitAddin\bin\Release\RevitAddin.dll" | Select-Object Status
# 必须为 Valid，Invalid/NotSigned 一律不许启动
```

TaskDialog 自动确认只是兜底，**不能替代签名**。无头流程里出现任何对话框即视为已失败。

---

## 2. 标高：Level 必须前置，且三处对齐

**症状**：-1F 构件建到 ±0，或墙悬空、门窗跑到别的楼层。

**硬规则（三条，缺一必错）**：

1. **CreateLevels 必须排在所有构件之前**。Revit 里 Level 是墙/板/门窗的宿主，先建构件后补 Level 只能靠改 offset 硬扛，必错。
2. **墙底偏移 = `baseZ − Level.Elevation`**，不是 `baseZ`。
   - Revit 的 `Wall.Create(doc, curve, levelId, false)` 生成的墙底默认坐落在该 Level 标高上；`baseZ` 是绝对标高。
   - 正确写法：`offset = baseZ - level.Elevation`，写入 `WallBaseOffset`（或创建后 `get_Parameter(WALL_BASE_OFFSET).Set(offset)`）。
3. **`LevelFor(baseZ)` = 不高于 `baseZ` 的最高标高**。不许取"最接近"，不许取"第一个"。取错会让 -1F 构件挂到 1F 的 Level 上，偏移算出负数导致墙反向或丢失。

```csharp
// LevelFor 正确实现
Level LevelFor(double baseZ) => levels
    .Where(l => l.Elevation <= baseZ + 1e-6)
    .OrderByDescending(l => l.Elevation)
    .First();
```

**自检**：建完后按 Level 分组统计墙段数，应为 每层墙段数 × 层数；组内 `WallBaseOffset` 全为 0（因为已用 Level 抬升）或全为设计层高差，不允许出现小数。

---

## 3. 门窗：必须读 `elevation` 字段

**症状**：-1F 的门窗门窗跑到 1F，或两层门窗叠在同一高度。

**根因**：CreatePanel 只用了 `sill`（窗台高），忽略了洞口自带的 `elevation`（所在楼层的绝对标高）。

**硬规则**：
- 门窗标高 = `opening.elevation + opening.sill`；`elevation` 缺省时才回落 `LevelFor().Elevation`。
- 门窗宿主墙必须与洞口 elevation 一致，否则 Revit 会抛"洞口超出宿主"。
- 建完按层做门窗对账：`层号 | 门数 | 窗数`，与图纸门窗表逐层比对，**不许只对总数**（A02 就出现过总数对、分层错）。

---

## 4. 楼板：必须支持 `holes` 开洞

**症状**：楼梯洞口被楼板封死，或改用"只铺局部楼板"导致板拼缝、面积统计失真。

**硬规则**：
- `hole` 用内环（`CurveLoop`）表达，与外环同一次 `Floor.Create`；不许用布尔减法做楼板洞。
- 洞口顶点数与楼梯段一一对应，楼梯 18 级连续段就应有 1 个连续洞口。
- 板顶 = `elevation + thickness`；`elevation` 语义是**板底**，与 CAD 侧一致，两边不许各解释一套。

---

## 5. 增量修补 > 重建（最贵的一课）

**事故**：v2 用 rebuild 清场（删旧构件再全量重建），清场漏删中间状态的结果，墙从 250 段翻到 500 段，模型报废重来。

**硬规则**：

1. **禁止使用 rebuild 模式做修补**。修补只走两条路：
   - `patch`：按构件 ID/名称定位，只改属性或几何；
   - `deleteNames`：按名称**精确**删除指定构件（先 dry-run 输出待删清单与数量，人工/程序核对后再执行）。
2. 每次修补前先落一份"修补前统计"（墙/门/窗/板/楼梯 分层计数），修补后立即重出统计，**两次差值必须等于预期变更量**。差值不符即停止后续操作。
3. 命名要可寻址：构件名带楼层前缀（`-1F_W_A_250`、`1F_D_1021`），否则 deleteNames 无从下手。
4. 全量重建只允许在"新建模型"场景使用，且目标文件必须为空或独立新文件。

---

## 6. 纯矢量 PDF（无文字层）提取

**症状**：`pdfplumber`/`PyMuPDF` 提不到任何文字，`truth.json` 的尺寸链全空，很容易一急就开始"看图猜"。

**根因**：施工图是 CAD 直接导出的矢量 PDF，文字是曲线路径，没有 text layer。

**硬规则**：

1. **走线段几何提取**，不是 OCR。读 `page.get_drawings()`（PyMuPDF）拿 `lines/rects/curves`，按线宽分层：
   - `w >= 0.6` → 粗线，实体轮廓（墙、板边）；
   - `w 0.5~0.7` → 可能存在 ±200~300mm 端点误差，验收时容忍；
   - `w < 0.3` → 细线，标注、填充、轴线。
2. **pt → mm 换算**：`1 pt = 24.948 mm`（按图幅实测标定，非同图幅必须重标）。
3. **原点偏移**：本图 `origin = (590.58 pt, 885.92 pt)`，对应图纸原点。所有坐标先减 origin 再乘系数，顺序颠倒会整体偏移。
4. 尺寸数字确实拿不到时，用"线段间距聚类 + 轴网气泡间距"反推，并在 `truth.json` 标 `derived:true`，交付报告里明示。
5. 无 `truth.json`（含 derived 标记）**禁止开始建模**。

---

## 7. 验收三件套（缺一件不算交付）

| 件 | 内容 | 通过标准 |
|---|---|---|
| 分层 report | 每层 墙段数 / 门 / 窗 / 板 / 楼梯级数，逐层列出 | 与图纸门窗表、平面图逐层一致 |
| 轴测图 | 全楼轴测截图 | 无穿模、无悬空墙、楼梯连续 |
| 剖面图 | 每层至少 1 条剖面 | 标高链闭合（-2900 → 0 → 层高），梯段连续 |

**数字对账优于看图**：`verify_geometry.py` 输出 Δ 表，Δ 超阈值（门窗 Δ>±100mm、墙厚 ∉ 设计墙厚集、端点距 PDF 线段 >200mm 且数量超个位数）一律回改，不许用"看着挺像"结案。

---

## 8. 版本与交付物命名

- 版本后缀只准 `_v1/_v2/_v3`，**禁止交付带 `.0001/.0004` 的 Revit 自动备份版本**——那是软件自动存档，混入交付即视为交付错误。
- 最终交付只留一份 `<项目>_<阶段>.rvt`，过程版本统一进归档目录 `02_过程版本`（按时间编号），桌面不留中间版本。
- 报告与图纸统一 `03_图纸与报告`，与 rvt 同项目名对齐，便于对应。
*（内容由AI生成，仅供参考）*
