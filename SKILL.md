---
name: arch-bim-skill
description: >
  建筑协同建模（CAD/BIM）：口述建筑概念 → brief/intent JSON → CAD JSON 指令 → AutoCAD(CadJsonPlugin) / Revit 无头建模 → 2D 平面图 + 3D 模型 DWG。
  覆盖墙体/门窗/楼板/屋顶/柱梁、门窗洞布尔减法、图层与出图标注、确定性 Spine-Column 布局、规范合规审查（无障碍/疏散/楼梯/防火分区）、半无头与双无头批处理。
  当任务涉及 AutoCAD/Revit 建模、DWG 输出、建筑施工图、BIM 布局规划、CAD 几何与图元生成时使用。
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_6a03122fd08111f1814c525400cd780f
    ReservedCode1: kPrzBL5f821KynUGqEb56tQ4tVjpiZEayHYM+/l64V5fvKMUYJ+HEF7J/C6WiYXonWi+AmiKhRHx6WhfqhYu/0b0Zm+etYPFaRo5WoKkbwAMbkTnxlcfyW2NaHoJRDZ7mYWyDg2qy0HPcV82WMfJW64/CJbnMFDgSVH5H9xN2CMvTnbSxBOOGkT6XK0=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_6a03122fd08111f1814c525400cd780f
    ReservedCode2: kPrzBL5f821KynUGqEb56tQ4tVjpiZEayHYM+/l64V5fvKMUYJ+HEF7J/C6WiYXonWi+AmiKhRHx6WhfqhYu/0b0Zm+etYPFaRo5WoKkbwAMbkTnxlcfyW2NaHoJRDZ7mYWyDg2qy0HPcV82WMfJW64/CJbnMFDgSVH5H9xN2CMvTnbSxBOOGkT6XK0=
---

# arch-bim-skill — 建筑协同建模

管线：口述 → `brief.json` → `brief_to_cad.py` → `request.json` → `CadJsonPlugin`（AutoCAD）/ `RevitAddin`；产物 3D 模型 DWG/RVT **默认落桌面**（`paths.modelOutputDir`）、2D 平面 DWG、回执（默认轮询 `cad-result.json`，headless 驱动 `<请求基名>.result.json`）。技能只管转换/布局/合规/无头，插件在 `D:\cad\cad-ai-plugin\`。

## 0. 环境自检

1. 读 `config.json`：`paths.acadExe`/`paths.accoreconsoleExe`/`paths.cadJsonDll`/`paths.revitExe`/`paths.requestDir`/`paths.modelOutputDir`/`paths.dwgOutputDir`，缺失或不存在先问用户，不盲跑。
2. `python brief_to_cad.py --env-check` 校验路径与插件 DLL。
3. `python scripts\smoke.py` 全绿再建模。

## 1. 执行流程（5 步）

1. 收集概念：房间、墙段、门窗、层高、屋顶类型（平顶/单坡/双坡）。
2. 写 `brief.json`（§3），默认 `mm`，转换器换算米；或写 `intent.json` 交 `planner/spine_column.py` 出兼容 brief。
3. 跑转换器（默认内置发单前校验，`--validate fix`；不通过则拦截不写 request）：
   - `python brief_to_cad.py --brief brief.json --rvt` → 存桌面 `<brief基名>.rvt`（Revit）
   - `python brief_to_cad.py --brief brief.json --plan` → 存桌面 `<brief基名>.dwg`（AutoCAD）
   - 指定路径才写死：`--rvt "D:\models\demo.rvt"` / `--dwg "D:\models\demo.dwg"`
   - 目录可用 `paths.modelOutputDir` 或 `ARCHBIM_MODEL_OUTPUT_DIR` 覆盖（默认 `%USERPROFILE%\Desktop`）
4. 合规审查：加 `--check`，产出 `violations.json`（与 request 同目录）：`summary` + `violations[]`（含 fixHint）；按 `fixHint` 回改重跑至 `passed == true` 或确认豁免。
5. 触发建模并验证：acad.exe 已运行或 `Start-Process 'D:\cad\AutoCAD 2021\acad.exe' -ArgumentList '/nologo'`；读回执四判据全过：
   - 回执路径：默认轮询 `<paths.requestDir>\cad-result.json`（默认 `%USERPROFILE%\Documents\Marvis\ai-modeling\cad-result.json`）；`run-headless-all.ps1 -CadRequest` 驱动为 `<请求基名>.result.json`（`-CadResultDir` 定目录，缺省与请求同目录）；设了 `CADJSON_RESULT` 则写该路径；
   - `status == "ok"`；
   - `errors == []`（否则重试）；
   - `entityCount > 0`；
   - `savedTo` 非空（可能加 `_1`）。

## 1A. 图纸数字化硬规则（从 PDF/扫描件建模，防认错六条）
> 全链路实操（PDF 矢量 → DXF → 链接 Revit → 精度验证，含 A02 实测基准 0.34mm/42.5mm 选型）见 `docs/pdf-cad-revit-pipeline.md`。

适用：从施工图（矢量 PDF）提取几何建模型。教训来源：线宽/厚度配对猜语义，把踏步梳、坡道线、标注刻度当墙，山墙原点错 1.5m，自评"视觉相似 76 分"而实际 42 分。

1. **真值先行**：第一步强制产出 `truth.json`（尺寸链数字序列、总尺寸、轴号→位置、门窗表 编号|宽|高|窗台、设计墙厚集）。尺寸链/门窗表用 OCR+视觉读数字，读不清的标 `unclear` 不许猜。几何提取未拿到 truth.json 禁止开始。
2. **标定禁循环**：比例 K 与原点必须由"气泡测得轴距"与"读到的尺寸链数字"逐项比对（容差 ±50mm），任一项超差即标定失败报错。原点轴号由轴号文字确认，不许用"总长假设+数字整齐"自证。
3. **墙线准入 = 轴网对齐审查**：每条候选墙报告到最近轴线的偏差（应 ≈0 或 ±半厚）；偏差 >100mm 或厚度 ∉ truth 墙厚集 → 降级"嫌疑"，不进模型，进报告。
4. **非墙黑名单（几何签名）**：踏步梳（≥6 条等距平行短线，间距 250-300）、标注刻度（垂直尺寸线的 45° 短线）、引线（斜线端触文字）、雨棚/坡道（轮廓外单线无配对）、填充线（45° 等距阵列）。命中即剔除，判例见 `docs/extraction-rules.md`。
5. **洞口对账表**：每个洞口输出 实测宽|吸附编号|标称宽|Δ；Δ>±100mm 不进模型（宁缺勿滥）。门/窗定性优先用门弧半径+编号首字母（C/M/FM/YTM/DYM/BY），不许只看断口。
6. **QA = 数字比对，禁视觉自评**：交付前跑 `python scripts\verify_geometry.py --truth truth.json --request request.json`，输出对账+diff；"渲染叠加图让模型打分"不算验证。对账表不干净不得声称完成，交付物必须含该表。

## 1B. 落盘默认桌面 + Revit 致命错误

- 新建模默认落盘 `paths.modelOutputDir`（默认 `%USERPROFILE%\Desktop`）：`--rvt` → `<基名>.rvt`，`--dwg`/不加 → `<基名>.dwg`。`headless/run-headless-all.ps1` 的 `-CadSaveDir`/`-RevitSaveDir` 未指定时同样取该目录。
- `request.savePath` 优先级最高（`CADJSON_SAVE`/`REVITJSON_SAVE` 仅在 savePath 为空时兜底）。要让文件落桌面，必须让 savePath 指向桌面——只改启动器兜底无效。
- **"不能生成墙（cannot generate wall）"是致命错误**：插件事务中断 → 弹模态错误框 → 私有桌面的按钮点击（1003/6/1）关不掉 → 10 分钟超时。判别：result `errors` 有 4 条即失败，0 条即成功（墙重叠警告是噪音，可忽略）。建模前必须清洗这 5 类几何：
  1. 洞口宽度 ≥ 墙长（2099 墙配 2100 窗）→ 收窄至墙长-120 并居中；
  2. 洞口距墙端 < 40mm → 夹住偏移；
  3. 薄短墙整段嵌在厚墙带内（100 垛落在 200 墙里）→ 删；
  4. 短墙垂直穿越长墙（十字 join）→ 删；
  5. 平行微缝墙（间隙 < (t1+t2)/2+60）→ 合成一条并转移洞口。
- **北向扩展区不许拍脑袋裁**：提取墙带的 y 超过轴网最大值 ≠ 噪声。先回查 PDF 原始厚线（`w>=0.6`）：厚线存在即为真实体量（本项目北侧外廊延伸到轴网外 ~6.7m，真实北界 y≈20017 而非 13300）。误裁会把楼板从 21291mm 缩到 15374mm，且"不能生成墙"风险同步上升。
- 验证方式：模型墙端点与 PDF 线段做最近距离比对，>200mm 的端点逐条解释清楚，数量应在个位数（细线 `w=0.5~0.7` 的 ±200~300mm 属正常）。

## 1C. Revit 精准建模硬规则（A02 实战沉淀，违反必错）

适用：Revit 侧多层建模与修补。详细操作与代码见 `docs/revit-precision-playbook.md`。

1. **改插件必重签**：任何一次重编 RevitAddin.dll 都会换签名，必须重签（证书 `CN=Marvis BIM`）并重新信任（机器级 `TrustedPublisher` + `Root`），`Get-AuthenticodeSignature` 状态非 `Valid` 禁止启动 Revit。TaskDialog 自动确认只是兜底，不能替代签名。
2. **Level 前置 + 偏移公式**：`CreateLevels` 必须排在所有构件之前；墙底偏移 = `baseZ − Level.Elevation`（不是 baseZ）；`LevelFor(baseZ)` = **不高于 baseZ 的最高标高**，不许取"最接近"或"第一个"。
3. **门窗读 elevation**：门窗标高 = `opening.elevation + opening.sill`，`elevation` 缺省才回落 Level 标高。否则 -1F 门窗整体错层。
4. **楼板支持 holes**：楼梯洞口/竖井用内环 `CurveLoop` 与外环同一次 `Floor.Create`，不用布尔减法；`elevation` 语义统一为**板底**。
5. **禁 rebuild 修补**：修补只走 `patch`（按 ID/名称改属性或几何）或 `deleteNames`（先 dry-run 出待删清单与数量，核对后执行）。v2 因 rebuild 清场漏删，墙 250→500 段整模报废。每次修补前后各出一份分层统计，差值必须等于预期变更量，不符立刻停。
6. **构件名可寻址**：带楼层前缀（`-1F_W_A_250`/`1F_D_1021`），否则 deleteNames 无从下手。
7. **纯矢量 PDF 无文字层**：走线段几何提取（`page.get_drawings()` 按线宽分层），换算 `1 pt = 24.948 mm`，先减原点再乘系数；拿不到的尺寸用线段间距/轴网气泡反推并在 truth.json 标 `derived:true`。无 truth.json 禁止开工。
8. **验收三件套缺一不算交付**：分层 report（每层 墙/门/窗/板/楼梯级数）+ 轴测图 + 剖面图，且必须过 `verify_geometry.py` 数字对账；禁"视觉相似度打分"结案。
9. **交付版本干净**：版本后缀只准 `_v1/_v2/_v3`，禁止把 Revit 自动备份 `.0001/.0004` 当交付物；过程版本归档到 `02_过程版本`，桌面不留中间版本。

## 1D. Revit 无头启动铁律（违反即返工，详见 `docs/revit-headless-playbook.md`）

**任务需要 Revit，就必然走 `headless\launch-revit-hidden.ps1`（私有桌面 `MarvisHidden`）；裸 `Start-Process Revit.exe` 一律禁止。**

| # | 规则 | 判据 |
|---|---|---|
| R1 | 禁止裸启动 Revit | 出现 `Start-Process "D:\Revit 2022\Revit.exe"` 即违规；`-WindowStyle Hidden` 也无效（splash 照弹） |
| R2 | dll 必须签名 `Valid` | `Get-AuthenticodeSignature` 非 `Valid` 禁止启动；重编译=换签名=弹窗，必重签并机器级信任（`CN=Marvis BIM`） |
| R3 | 一个插件承载所有动作 | 禁止每个任务新建 `Addins\2022\RevitXXX\`；动作走 request 的 `action` 分支（现有 12 个目录即反面教材） |
| R4 | 必须自动退出 | 写完 out/result → `SC_CLOSE`；残留进程启动器兜底 kill |
| R5 | 改 json 即生效，不重启 Revit | `Idling` 比对 `LastWriteTime` 换动作重跑；"要重启 Revit 才生效"属设计缺陷 |

- **RevitGrid 协议**：`MARVIS_GRID_REQ` / `MARVIS_GRID_OUT` 环境变量 + 首次 `Idling` 执行，out 末行 `DONE` 为完成信号；action 分支 `build/clean/diag2/floor/iso/views/dump/grid/restoregrid/gridhead/gridbubble`。
- **启动器调用**（`-Env` 注入环境变量、`-ResultSignal` 等 DONE 信号，均已支持）：
  ```powershell
  .\headless\launch-revit-hidden.ps1 -Document $rvt -Request $req -Result $resJson `
      -Env @{ MARVIS_GRID_REQ=$req; MARVIS_GRID_OUT=$out } -ResultSignal $out -TimeoutSec 600
  ```
- **验收四件**：out 含 `DONE` → 导出 PNG 非空 → 日志 `用户桌面可见窗口峰值=0` → 无 Revit 残留进程。缺一条不许说"完成"。

## 2. 构件映射表

| 构件 | type | 参数 |
|---|---|---|
| 墙段 | `wall` | `start`/`end` 或 `length`+`basePoint`+`angle`；`thickness`；`height`；opt `elevation`(0)/`openings`/`layer` |
| 楼板 | `floor` | `outline`(多边形，米)；opt `thickness`(0.15)/`elevation`(板底 0)/`layer` |
| 屋顶 | `roof` | `type`(gable/shed/flat)；`outline` 或 `start`/`end`+`width`；`thickness`；opt `height`(坡顶)/`elevation`/`overhang`/`layer` |
| 门 | `door` | 优先并入 `wall.openings[]`：`startX`/`width`/`height`；opt `sill`(0)/`layer` |
| 窗 | `window` | 同门并入 `wall.openings[]`：`startX`/`width`/`height`/`sill` |
| 柱 | `column` | `width`/`depth`/`height`/`basePoint`；opt `angle`(绕 Z 弧度)/`layer` |
| 梁 | `beam` | `length`/`width`/`height`/`basePoint`；opt `angle`/`layer` |
| 实体方块 | `box` | `width`/`depth`/`height`/`basePoint`（最小角点）；opt `angle`/`layer`；楼梯踏步即逐级 box |
| 2D 平面线 | `polyline2d` | `points`(俯视 z=0)；opt `closed`/`layer` |
| 2D 标注 | `text2d` | `text`/`insertPoint`；opt `height`(字高)/`rotation`/`layer` |
| 图块实例 | `instance` | `blockName`/`insertPoint`；opt `scale`/`rotation` |

原生 wall/column/beam/floor/instance，余为已编译扩展。

## 3. brief / intent Schema

**brief**（`doors`/`windows`/`columns`/`beams`/`roof` 可省略）：

```json
{
  "project": {"name":"demo-house","unit":"mm","floorHeight":3000,"roofElevation":3000},
  "rooms": [{"name":"living","outline":[[0,0],[6000,0],[6000,4000],[0,4000]],"floorThickness":150}],
  "walls": [{"id":"W1","start":[0,0],"end":[6000,0],"thickness":240,"height":3000,
    "openings": [{"type":"window","startX":1200,"width":1200,"height":1500,"sill":900},
                 {"type":"door","startX":4200,"width":900,"height":2100,"sill":0}]}]
}
```

- 坐标 2D `[x, y]`，Z 由 `elevation`/`height`/`floorHeight` 推导；单位 `project.unit`（`mm`/`m`）；`startX` 沿 `start`→`end` 偏移。`roof`：`type`（`gable` 双坡/`shed` 单坡/`flat` 平顶板）、`thickness`/`height`/`overhang`。

**intent**（确定性布局，LLM 只出意图）：

```json
{
  "project": {"name":"auto-layout-sample","unit":"m","floorHeight":3.0,"floors":1,"buildingType":"residential"},
  "footprint": {"width":12.0,"depth":10.0},
  "entranceRoom": "hall",
  "rooms": [{"id":"living","name":"客厅","type":"living","floor":0,"targetArea":30.0,"preferredSide":"left"}],
  "stairs": []
}
```

- `footprint` 宽/深（米）；`targetArea` 面积（m²，必填）；`floor` 从 0 起；`preferredSide`：left/right/front/rear/any；`connections[]`：`{"roomA","roomB"}`。
- `stairs[]`：`{"fromFloor":0,"toFloor":1}`；踏步由 `calculate_stair(层高)` 算（rise ∈ [150,220]mm，目标 190mm）。
- 预校验：填充率每层 70%~110%、宽深为正、楼层合法、入口存在、楼梯连续；失败报错不生成 request。

## 4. 合规规则库

9 类规则在 `compliance/rules_accessible.py`；输入由 `context_from_*` 构建。

| 规则（类别） | 阈值 | 来源 |
|---|---|---|
| 门净通行宽（无障碍） | ≥ 830mm | PMR / ADA §604 / GB 50763 |
| 门扇标称宽（无障碍） | ≥ 900mm | 同上 |
| 无障碍回转圆（无障碍） | Ø ≥ 1500mm | PMR / ADA §603 |
| 单向走廊净宽（无障碍） | ≥ 1200mm | IBC §1020 / GB 50352 |
| 双向走廊净宽（无障碍） | ≥ 1400mm | IBC §1020 / GB 50352 |
| 尽端走廊长度（疏散） | ≤ 12m | IBC §1018.4 / GB 50016 |
| 疏散距离（疏散） | ≤ 30m | IBC §1017 / GB 50016 |
| 踏步高/踏面宽/净宽（楼梯） | 150~220mm / ≥220mm / 按类型 | GB 50352 / GB 50016 |
| 防火分区面积（防火） | 地上≤2500 / 高层≤1500 / 地下≤500 m² | GB 50016 表3.3.1 |

## 5. 建模策略与硬约束

- 3D：墙/柱/梁 `Solid3d.CreateBox`（墙含洞口 `BooleanSubtract`）；楼板 `Region.CreateFromCurves` → `Extrude(thickness)`，底面在 elevation；屋顶 YZ 截面 Region 沿长度 `Extrude(length)`；门窗扇为洞口内薄板；`instance` → `BlockReference`。
- 逐层墙体：planner 每层生成 4 段外墙（elevation=层号×层高）+ 全房间隔墙（共享边去重），不再是通高单墙；spine 楼板按梯段拆块形成楼梯洞口；Revit 侧墙用 `basePoint[2]` 抬标高，窗洞补窗下墙。
- 2D：俯视 z=0 线框，图层 `A-*`（`A-WALL`/`A-DOOR`/`A-WINDOW`/`A-COLUMN`/`A-DIM`/`A-TEXT`），`text2d` 出尺寸与房间名。

布尔减法 3 条硬约束：

1. 洞口 box 必须完全穿透墙厚（沿墙厚两端各超出 ≥ 0.01m），避免布尔残留碎面。
2. 减法必须在**未加入数据库**的临时 Solid3d 上进行，结果实体再 AppendEntity。
3. 墙有 `angle` 旋转时，先在墙局部坐标系完成洞口减法，再整体旋转变换。

## 6. 无头三通道

| 通道 | 启动方式 | 适用场景 | 依赖 |
|---|---|---|---|
| 半无头 | `acad.exe`（插件 1s 轮询请求文件） | 交互式会话、调试 | 完整 GUI + 轮询 Timer |
| CAD 真无头 | `accoreconsole.exe /i <seed.dwg> /s run.scr` | 批处理、无人值守、CI | Core Console + `CADJSON_RUN` |
| Revit 半无头（GUI 进程 + 事件驱动，**非真无头**） | `Revit.exe <sample.rvt>`（`DocumentOpened`+`Idling`）；窗口会创建，需私有桌面/隐藏才能不干扰用户 | 批处理、无人值守 | Revit 2022 + RevitAddin |

- 插件 DLL 必须**签名并信任**（机器级 `TrustedPublisher` + `Root`），否则 Revit 会弹“未签名加载项”TaskDialog 卡住无头流程；`launch-revit-hidden.ps1` 同时带 TaskDialog 自动确认兜底。
- 语义层：request 可带 `levels` / `rooms` / `grids`，Revit 侧对应建 `Level` / `Room` / `Grid`；CAD 侧忽略未知键。
- Revit 窗口不干扰用户：`headless/launch-revit-hidden.ps1` 用私有桌面（CreateDesktop）启动，实测用户桌面可见窗口 0；`run-headless-all.ps1 -Mode revit` 已默认接入。
- `CADJSON_RUN`：探测到 Core Console 即跳过 1s 轮询 Timer，命令驱动（建模逻辑不变，后台 Transaction）。
- `CADJSON_REQUEST`/`CADJSON_RESULT`/`CADJSON_SAVE` 优先于默认轮询路径，`CADJSON_SAVE` 仅在 `savePath` 为空时兜底；Revit 侧 `REVITJSON_*`。
- XData 清场：按 CADJSON XData Purge 隔离请求。
- `DocumentOpened` 仅在 `REVITJSON_REQUEST` 存在时挂起文档，否则立即返回；建模在第 2 次 `Idling` 执行（规避事件内改模型）。
- 完成后保存 → 写 result → 归档 `*.done` 防重启循环 → 自动退出（`WM_SYSCOMMAND / SC_CLOSE`；20s 未退出由 Idling Kill）。

共用 request/result 协议，互为备选；acad.exe 轮询为 fallback。见 `docs/dual-headless.md`。

## 7. 目录结构与文档索引

```
D:\skills\arch-bim-skill\
├── SKILL.md
├── README.md
├── brief_to_cad.py
├── archbim_config.py
├── config.json
├── scripts\smoke.py
├── compliance\
├── planner\
├── docs\
├── headless\
└── examples\

D:\cad\arch-bim-reference\   # 外部参考克隆（不在 skill 库内）
D:\cad\cad-ai-plugin\        # 插件本体
```

| 何时读 | 文档 |
|---|---|
| 完整建筑构件清单（UniFormat 对照） | `docs/complete-building.md` |
| GitHub 生态调研（pyRevit/IFC） | `docs/github-research.md` |
| SolidWorks 自动化 skill 对比 | `docs/solidworks-skill-comparison.md` |
| 精度保障（golden/props/reconcile） | `docs/precision.md` |
| **Revit 精准建模实操（签名/Level/门窗/开洞/禁rebuild/矢量PDF/验收）** | `docs/revit-precision-playbook.md`（建模前必读） |
| **Revit 无头启动铁律与 A02 SOP（禁裸启动/私有桌面/签名/自动退出/免重启/action 分支）** | `docs/revit-headless-playbook.md`（**启动 Revit 前必读**） |
| **PDF矢量→CAD→链接Revit→精度验证 全链路（K=24.948/图层语义/双线配对/链接撑大包围盒/四条路线实测）** | `docs/pdf-cad-revit-pipeline.md`（拿到 PDF 图纸时必读） |
| 3D/2D/IFC 协同出图 | `docs/dual-output.md` |
| IFC 导出 | `python ifc_export.py request.json out.ifc`（含真洞口/门窗/Pset/面积） |
| 发单前校验规则 | `docs/preflight.md` |
| 请求/响应字段 | `docs/protocol.md` |
| 插件扩展 | `docs/plugin-extension.md` |
| 建模通法（先读） | `docs/modeling-common-methods.md` |
| 无头架构 | `docs/dual-headless.md` |
| 施工图 | `docs/cad-detailing-guide.md` |
| 套图/图框排版代码 | `sheets.py` |
| 真消隐立面 | `elevations2.py`（`--elev-true`） |
| Revit 原生图纸 | `--revit-drawings ... --revit-sheets` |
| 面积表/工程量统计 | `schedules.py`（`--area-schedule/--quantity`） |
| 制图标准/标注缺口 | `docs/drawing-standards.md` |
| 外部参考 | `docs/reference-digest.md` |
| 历史设计（非指令） | `docs/design-notes.md` |

## 8. 依赖与已知限制

- Python 3.8+。核心管线（转换/布局/合规/无头驱动）仅标准库；IFC 导出（`ifc_export.py`）额外依赖 `numpy` 与 `ifcopenshell`。
- AutoCAD 2021 + CadJsonPlugin（`D:\cad\AutoCAD 2021\CadJsonPlugin.dll`，部署见 `D:\cad\cad-ai-plugin\README.md`）。
- 默认路径 `%USERPROFILE%\Documents\Marvis\ai-modeling\cad-request.json`/`cad-result.json`（`paths.requestDir` 可覆盖；headless 驱动产物为 `<请求基名>.result.json`）；模型默认落 `%USERPROFILE%\Desktop`（`paths.modelOutputDir` 可覆盖）。
- Revit 2022（`D:\Revit 2022\`）+ RevitAddin（`D:\cad\cad-ai-plugin\RevitAddin` → `C:\ProgramData\Autodesk\Revit\Addins\2022\`）。
- 扩展字段"写入即兼容"（旧插件忽略未知键）；Revit request 单位 mm 内部换算英尺；半无头依赖 GUI+轮询 Timer，批量走真无头，勿并发写同一请求文件。
- 退出码：0 成功；1 意图/合规预校验不通过、环境路径缺失；2 输入解析错误或配置缺失（统一友好报错，不吐 traceback；输入 JSON 容忍 UTF-8 BOM）。
