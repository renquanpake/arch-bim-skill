---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_f19c0711d04f11f1814c525400cd780f
    ReservedCode1: bduzB8mtNXM/kL16CjblwIRqjPirzZVS8lfaAjqPBJQ5etp1MX8cFRbu6jX6ClnOi6XQeFLmkm0Q+LsdRoFyfBdRQi708/Zddq6OGqoACAXPuZBC04VgSUmv8PVDiGQmfh7FHxlw0fi0wvCD8rI6H4lZ5H4zilMqNJoGAeE6hSsPmgmb6Tkh0YhxDxM=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_f19c0711d04f11f1814c525400cd780f
    ReservedCode2: bduzB8mtNXM/kL16CjblwIRqjPirzZVS8lfaAjqPBJQ5etp1MX8cFRbu6jX6ClnOi6XQeFLmkm0Q+LsdRoFyfBdRQi708/Zddq6OGqoACAXPuZBC04VgSUmv8PVDiGQmfh7FHxlw0fi0wvCD8rI6H4lZ5H4zilMqNJoGAeE6hSsPmgmb6Tkh0YhxDxM=
---

﻿# CAD JSON 协议说明（CadJsonPlugin）

AutoCAD JSON 建模插件的请求/响应协议。JSON 指令驱动 AutoCAD 自动建模，实现「Agent 说一句帮我建模 → AutoCAD 自动出模型并保存 DWG」的闭环。

## 1. 部署路径

| 项 | 路径 |
|---|---|
| 插件源码工程 | D:\cad\cad-ai-plugin\CadJsonPlugin\ |
| 插件 DLL（受信任路径） | D:\cad\AutoCAD 2021\CadJsonPlugin.dll |
| 请求文件 | %USERPROFILE%\Documents\Marvis\ai-modeling\cad-request.json |
| 结果文件 | %USERPROFILE%\Documents\Marvis\ai-modeling\cad-result.json（默认轮询；headless 驱动为 `<请求基名>.result.json`） |
| 插件日志 | D:\cad\AutoCAD 2021\cad-json-plugin.log |

> 路径说明：外部路径统一由技能根 `config.json` 提供（`paths.acadExe` / `paths.accoreconsoleExe` / `paths.cadJsonDll` / `paths.revitExe` / `paths.requestDir` / `paths.dwgOutputDir`），示例中的绝对路径仅为默认值。

插件在 acad.exe 中以 1 秒定时器轮询请求文件；发现请求后原子消费（改名 `.processing` → 处理 → 清理），避免重复处理与半写文件。Core Console（accoreconsole）下跳过轮询，改由 `CADJSON_RUN` 命令驱动（见 docs/dual-headless.md）。

## 2. 单位约定

- **米**：AutoCAD 为纯几何空间，1 米 = 1 图形单位。所有长度、坐标参数以米为单位。
- 上层 brief（arch-bim-skill）默认使用毫米（建筑习惯），由 `brief_to_cad.py` 统一换算为米后再写入 request。

## 3. 请求协议（cad-request.json）

```json
{
  "elements": [
    { "type": "wall",   "name": "W1", "length": 6.0, "thickness": 0.2,  "height": 3.0, "basePoint": [0,0,0], "angle": 0 },
    { "type": "column", "name": "C1", "width": 0.4,  "depth": 0.4,      "height": 3.0, "basePoint": [0,0,0], "angle": 0 },
    { "type": "beam",   "name": "B1", "length": 4.0, "width": 0.3,      "height": 0.5, "basePoint": [6,0,3.0], "angle": 90 },
    { "type": "floor",  "name": "F1", "outline": [[0,0],[6,0],[6,4],[0,4]], "thickness": 0.15, "elevation": 3.0 },
    { "type": "instance","name": "FURN-1", "blockName": "FURN-SIMPLE", "insertPoint": [2,2,0], "scale": 1.0, "rotation": 0 }
  ],
  "savePath": "D:\\models\\out.dwg"
}
```

### 3.1 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elements` | array | 是 | 构件数组，每个元素为构件对象（见 §3.2） |
| `savePath` | string | 否 | DWG 保存路径；缺省不保存。目录不存在会自动创建；文件已存在自动追加 `_1/_2` 后缀，**不覆盖已有图纸** |
| `appendExisting` | bool | 否 | 默认 `false`：处理前先清除上一轮由本插件创建的实体（按 XData 应用名 `CADJSON` 识别），保证每次请求的 `entityCount`/体积只反映本次模型；置 `true` 则在当前图纸上追加建模。用户自有实体（无该 XData）永不被清除 |

### 3.2 构件对象通用字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string | 是 | 构件类型（见 §3.3 构件类型表） |
| `name` | string | 否 | 构件名，缺省同 type；通过 XData(CADJSON) 记录到实体 |

### 3.3 构件类型表（当前插件原生支持）

| type | 必需参数 | 可选参数 | 建模方式 |
|---|---|---|---|
| `wall` | `length`（沿 X，米）/ `thickness` / `height` / `basePoint`([x,y,z]) | `angle`（绕 Z 轴旋转，弧度） | 矩形截面 Solid3d 拉伸体 |
| `column` | `width` / `depth` / `height` / `basePoint` | `angle` | 矩形截面 Solid3d 拉伸体 |
| `beam` | `length` / `width` / `height` / `basePoint` | `angle` | 矩形截面 Solid3d 拉伸体 |
| `floor` | `outline`（闭合多边形点列 `[[x,y],...]`，≥3 点）/ `thickness` | `elevation`（板底标高，默认 0） | Polyline + Region → Solid3d 拉伸，底面位于 elevation |
| `instance` | `blockName` / `insertPoint` | `scale`（默认 1.0）/ `rotation`（弧度） | 1x1 正方形块定义 + BlockReference |

参数默认值（插件 GetDouble/GetPoint 兜底）：

| 参数 | 默认值 |
|---|---|
| wall.length / thickness / height | 4.0 / 0.2 / 3.0 |
| column.width / depth / height | 0.4 / 0.4 / 3.0 |
| beam.length / width / height | 6.0 / 0.3 / 0.5 |
| floor.thickness / elevation | 0.15 / 0.0 |
| instance.scale / rotation | 1.0 / 0.0 |
| 所有 basePoint / insertPoint | [0, 0, 0] |
| 所有 angle | 0 |

### 3.4 扩展字段（已编译进 CadJsonPlugin.dll：roof / door / window、布尔开洞、2D 图元、图层）

| type | 扩展字段 | 说明 |
|---|---|---|
| `wall` | `openings` | 门窗洞列表：`[{"type":"door"/"window","startX":米,"width":米,"height":米,"sill":米}, ...]`，沿墙局部 X 方向偏移；扩展插件执行布尔减法开洞 |
| `wall` | `elevation` | 墙底标高（米，默认 0）；等价于 basePoint[2] |
| `roof` | `roofType`（`gable`/`shed`/`flat`）/ `outline`（含挑檐的最终轮廓，插件直接按其包围盒建模、不再二次扩边）/ `thickness` / `height` / `elevation` / `layer` | 截面 Region 沿屋脊（X）方向拉伸：双坡五边形、单坡斜四边形、平顶矩形 |
| `door` / `window` | `width` / `height` / `thickness` / `basePoint` / `angle` | 独立门扇/窗扇薄板（无墙可挂时使用） |
| `polyline2d` | `points`（`[[x,y],...]`）/ `closed` / `layer` | 2D 平面线框（z=0） |
| `text2d` | `text` / `insertPoint` / `height` / `rotation` / `layer` | 2D 标注文字/尺寸 |
| 所有 | `layer` | 图层名，缺省 `0`；扩展插件按 layer 分图层 |

## 4. 响应协议（cad-result.json / `<请求基名>.result.json`）

```json
{
  "status": "ok",
  "message": "已创建 7 个构件；已保存 D:\\models\\demo-house.dwg；模型空间实体数=8",
  "time": "2026-10-25 10:30:00",
  "created": ["W1", "W2", "C1", "C2", "B1", "F1", "FURN-1"],
  "errors": [],
  "savedTo": "D:\\models\\demo-house.dwg",
  "entityCount": 8
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | `ok` 或 `error`（解析/执行失败） |
| `message` | string | 中文汇总：创建数、失败明细、保存路径、实体数 |
| `time` | string | 处理时间 `yyyy-MM-dd HH:mm:ss` |
| `created` | array | 成功创建的构件名列表 |
| `errors` | array | 失败构件明细 `名:原因` |
| `savedTo` | string | 实际保存的 DWG 路径（可能带 `_1` 后缀） |
| `entityCount` | int | 模型空间实体总数（验证建模结果的权威指标） |
| `message`（统计段） | string | `message` 末尾附带实体统计：`Solid3d/Polyline/DBText/BlockRef` 计数、Solid3d 体积合计（m³，用于自证门窗洞布尔减生效）、图纸内图层清单 |

## 5. 错误处理

- 单构件失败不中断整体：`errors` 记录失败项，其余构件正常创建并保存。
- 顶层解析失败（缺 `elements`、非法 JSON）：`status=error`，不保存 DWG。
- 轮询异常：写入插件日志，请求文件被清理；Agent 侧超时未收到 result 时应检查日志 `D:\cad\AutoCAD 2021\cad-json-plugin.log`。

## 6. 验证方法（端到端）

```powershell
# 1. 写入 request.json（含 elements + savePath）
# 2. 确保 AutoCAD 已就绪：半无头启动 acad.exe（插件 1s 轮询请求文件）；批处理/无人值守用真无头 accoreconsole + CADJSON_RUN（见 docs/dual-headless.md）
Start-Process 'D:\cad\AutoCAD 2021\acad.exe' -ArgumentList '/nologo'
# 3. 轮询读取 cad-result.json（headless 驱动读 `<请求基名>.result.json`）：status=ok、errors=[]、entityCount>0、savedTo 非空
```

> 路径说明：外部路径统一由技能根 `config.json` 提供（`paths.acadExe` / `paths.accoreconsoleExe` / `paths.cadJsonDll` / `paths.revitExe` / `paths.requestDir` / `paths.dwgOutputDir`），示例中的绝对路径仅为默认值。

## 7. 与 Revit 对齐说明

协议字段名（wall/column/beam/floor/instance、basePoint、savePath）与 Revit AutoRunner 对齐，便于同一套上层 Agent 逻辑迁移。AutoCAD 版本为纯几何建模，无 Revit 参数化族语义；构件参数仅控制几何尺寸与空间位置。
*（内容由AI生成，仅供参考）*
