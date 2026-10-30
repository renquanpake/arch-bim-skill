---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_690dc75dd08111f19fe7525400de85a5
    ReservedCode1: joq656D0Gml+56YTylBGHDRlm8tTbIw3lYcQbw5UpYw0IH3AjRvDxjJ7ftsr+05wEKPdCVq2jNw5QYY7rfFD+QT+Q8VncweNg+2BSsTHJLsBFhnTGXeuyIvfZ0YydvXU/shipyXNs+Go8n5j+fDDuhZv2qR3RZ21dvK4wjhG9B+QsAfX8OV04q9hEkw=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_690dc75dd08111f19fe7525400de85a5
    ReservedCode2: joq656D0Gml+56YTylBGHDRlm8tTbIw3lYcQbw5UpYw0IH3AjRvDxjJ7ftsr+05wEKPdCVq2jNw5QYY7rfFD+QT+Q8VncweNg+2BSsTHJLsBFhnTGXeuyIvfZ0YydvXU/shipyXNs+Go8n5j+fDDuhZv2qR3RZ21dvK4wjhG9B+QsAfX8OV04q9hEkw=
---

# 双无头模式（Dual Headless）总文档

> 将 **AutoCAD 真无头**（accoreconsole + CADJSON_RUN）与 **Revit 无头**（Revit.exe + DocumentOpened/Idling 事件）整合为可持续使用的无人值守建模工作流。
> 适用：批处理建模、CI 集成、跨软件双端自动化。与 acad.exe 半无头轮询互为备选，协议三通道共用。

---

## 1. 总体架构

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                    headless/ 工作区                       │
                        │  request.json（elements + savePath）                      │
                        │  或请求目录批量（*.json，排除 *.result.json / *.done）       │
                        └──────────────────────────┬───────────────────────────────┘
                                                   │
                                                   ▼
                        ┌──────────────────────────────────────────────────────────┐
                        │  run-headless-all.ps1（统一驱动入口）                     │
                        │  - 日志：run-headless-all.log + logs/cad-*.out/.err        │
                        │  - 失败续跑：-Resume（result 已 ok 自动跳过）              │
                        └──────────────┬──────────────────────────┬────────────────┘
                                       │                          │
                 CAD 通道（循环）       ▼                          ▼     Revit 通道（逐 RVT）
        ┌────────────────────────────────┐          ┌─────────────────────────────────┐
        │ accoreconsole.exe              │          │ D:\Revit 2022\Revit.exe <rvt>    │
        │   /i <seed.dwg> /s run.scr     │          │   DocumentOpened 检测请求         │
        │   NETLOAD CadJsonPlugin.dll    │          │   第 2 次 Idling 建模             │
        │   CADJSON_RUN（同步执行）       │          │   保存 → 写 result → 归档 *.done  │
        │   环境变量 CADJSON_* 传参       │          │   SC_CLOSE 自动退出               │
        │                                │          │   环境变量 REVITJSON_* 传参       │
        └──────────────┬─────────────────┘          └──────────────┬──────────────────┘
                       │                                          │
                       ▼                                          ▼
        ┌─────────────────────────────┐           ┌──────────────────────────────────┐
        │ result.json                 │           │ result.json                       │
        │ {status, created, errors,   │           │ {status, created, errors,         │
        │  savedTo, entityCount, ...}  │           │  entityCount, volumeM3, savedTo}  │
        │ DWG 落盘（savePath）         │           │ RVT 落盘（savePath / REVITJSON_SAVE）│
        └─────────────────────────────┘           └──────────────────────────────────┘
```

- 上游 `brief_to_cad.py`（arch-bim-skill）产出的 request.json 无需改动即可双端消费；
- 双端执行结果统一收敛为 `<请求基名>.result.json`（`-CadResultDir`/`-RevitResultDir` 决定目录，缺省与请求同目录），驱动脚本据此判定成功（`status == "ok"`）并记录日志；
- Revit 侧请求文件执行后归档为 `*.done`（防重复触发/防重启循环），脚本按 `*.json` 收集时自动排除。

## 2. CAD 侧：真无头机制

### 2.1 命令入口 CADJSON_RUN

- CadJsonPlugin（D:\cad\cad-ai-plugin\CadJsonPlugin，net48，产物 `D:\cad\AutoCAD 2021\CadJsonPlugin.dll`）新增 `[CommandMethod("CADJSON_RUN")]`。
- 插件 `Initialize()` 探测进程名/启动命令行：**Core Console 下跳过** acad.exe 的 1s 轮询 Timer，改由命令驱动；建模逻辑不变（全程后台 Transaction，无 `GetString/GetPoint/对话框` 等 UI 阻塞）。
- `CADJSON_RUN` 为同步执行：读 `CADJSON_REQUEST` → 按元素协议建模 → 写 `CADJSON_RESULT` → 按请求 `savePath`（或 `CADJSON_SAVE` 兜底）保存 DWG → 控制台打印 `[CADJSON_RUN] start/done` → 进程退出（ExitCode 正常，不拉起 GUI）。

### 2.2 调用命令

script `headless/run.scr`：

```scr
NETLOAD
"D:\cad\AutoCAD 2021\CadJsonPlugin.dll"
CADJSON_RUN
```

调用：

```powershell
D:\cad\AutoCAD 2021\accoreconsole.exe /i <seed.dwg> /s D:\skills\arch-bim-skill\headless\run.scr
```

> 路径说明：外部路径统一由技能根 `config.json` 提供（`paths.acadExe` / `paths.accoreconsoleExe` / `paths.cadJsonDll` / `paths.revitExe` / `paths.requestDir` / `paths.dwgOutputDir`），示例中的绝对路径仅为默认值。

### 2.3 环境变量

| 变量 | 作用 | 优先级 |
|---|---|---|
| `CADJSON_REQUEST` | 请求 JSON 路径 | 高于默认轮询路径 |
| `CADJSON_RESULT` | 结果 JSON 路径 | 高于默认轮询路径 |
| `CADJSON_SAVE` | DWG 保存兜底路径 | 仅请求内 `savePath` 为空时生效 |

### 2.4 请求隔离

无头模式按 CADJSON XData 清场（Purge）：同一 seed.dwg 连续跑多请求互不污染（已验证：含 34 实体的 DWG 跑简化请求仅输出新请求实体）。

## 3. Revit 侧：无头机制

### 3.1 工程与部署

- 工程：`D:\cad\cad-ai-plugin\RevitAddin`（net48 / AnyCPU，零外部依赖，`dotnet build -c Release` 0 错误）。
- DLL：`C:\ProgramData\Autodesk\Revit\Addins\2022\RevitAddin\RevitAddin.dll`
- 清单：`C:\ProgramData\Autodesk\Revit\Addins\2022\RevitAddin.addin`，含两条注册：
  - `AddIn Type="Application"` → `RevitAddin.AutoRunner`（无头自动执行）
  - `AddIn Type="Command"` → `RevitAddin.RevitJsonRunCommand`（手动执行一次，菜单名"Marvis AI 建模 (Revit JSON)"）
- 旧轮询插件 `RevitAIModeling.addin` 已改名 `.disabled`，避免双消费者争抢请求文件。

### 3.2 事件触发流程

1. `OnStartup` 订阅 `ControlledApplication.DocumentOpened` 与 `Idling`；
2. `DocumentOpened` 只做检测：`REVITJSON_REQUEST` 存在才挂起文档；不存在立即返回，不干扰手工使用；
3. 建模在第 **2 次 Idling** 中执行（规避"事件内改模型"限制）；
4. 建模 → 保存（`request.savePath` 或 `REVITJSON_SAVE`）→ 写 result（`REVITJSON_RESULT`）→ 请求归档 `*.done`；
5. 退出：活动文档禁止 API 关闭，改发 `WM_SYSCOMMAND / SC_CLOSE` 非阻塞退出；20s 未退出由 Idling 兜底 Kill（此时文档已保存）。

### 3.2b 私有桌面启动器（用户桌面零窗口）

**问题**：Revit 没有真无头模式（无 `nosplash`/`headless` 开关），`Start-Process -WindowStyle Hidden` 实测无效（splash 照弹）。每次 `Revit.exe <rvt>` 都会把窗口顶到用户桌面。

**解法**：`headless/launch-revit-hidden.ps1` 用 `CreateDesktop` + `CreateProcess(lpDesktop=...)` 把 Revit 放到独立桌面 `MarvisHidden`，窗口全部落在该桌面，用户看不到也抢不到焦点；建模、保存、`SC_CLOSE` 退出流程不变。

```powershell
.\launch-revit-hidden.ps1 -Document D:\models\seed.rvt `
    -Request .\revit\req.json -Result .\revit\res.json -Save D:\models\out.rvt
```

- `run-headless-all.ps1 -Mode revit` 已默认走该启动器（`Invoke-RevitTask`）。
- 自证日志：`[hidden] 用户桌面可见窗口峰值=0，结束时=0`。
- 两个必须注意的实现细节：
  1. `CreateProcess` 的 `lpCommandLine` 必须包含 exe 作为 argv[0]，否则 Revit 把文档路径当程序名忽略，停在“首页”不开文件（表现为永远等不到 result）。
  2. 结果出现后由启动器向私有桌面主窗发 `SC_CLOSE` 优雅退出，25 秒未退再 `taskkill /T /F`。
- 实测：184 构件全量模型端到端 27 秒，`status=ok`，`volumeM3=190.7046`，用户桌面可见窗口 0。

### 3.3 调用命令

```powershell
D:\Revit 2022\Revit.exe <sample.rvt>
```

> 路径说明：外部路径统一由技能根 `config.json` 提供（`paths.acadExe` / `paths.accoreconsoleExe` / `paths.cadJsonDll` / `paths.revitExe` / `paths.requestDir` / `paths.dwgOutputDir`），示例中的绝对路径仅为默认值。

### 3.4 元素协议（mm → 英尺换算）

| CAD type | Revit 实现 |
|---|---|
| wall | `Wall.Create` 分段（洞口间整高墙段 + 洞口上方过梁段），Comments 记 `name#i` |
| floor / roof / column / beam | 轮廓拉伸 → `DirectShape`（OST_Floors / OST_Roofs / OST_Columns / OST_StructuralFraming） |
| door / window | 面板拉伸；墙洞由 `wall.openings` 生成 |
| polyline2d | DirectShape 曲线族 |
| text2d | `TextNote.Create`（优先活动平面视图） |
| action=report | 统计已落盘构件（allElements / marvisWalls / marvisDirectShapes / textNotes） |

## 4. 请求 / 结果协议

- request.json（双端同构）：

```json
{
  "appendExisting": false,
  "elements": [
    {"name": "TW1", "type": "wall", "start": [0,0,0], "end": [6000,0,0], "thickness": 200, "height": 3000, "layer": "A-WALL"}
  ],
  "savePath": "D:\\models\\headless-min.dwg"
}
```

- result.json 关键字段（文件名：默认轮询 `<requestDir>\cad-result.json`；headless 驱动 `<请求基名>.result.json`；`CADJSON_RESULT`/`REVITJSON_RESULT` 覆盖）：`status`（ok/error）、`created[]`、`errors[]`、`savedTo`、`entityCount`；Revit 额外含 `volumeM3`、`count`、`docPath`，report 模式含 `report{allElements, marvisWalls[], marvisWallsCount, marvisDirectShapes[], textNotes[], textNotesCount}`。

## 5. 已验证用例汇总

### 5.1 CAD（accoreconsole + CADJSON_RUN）

| 用例 | 请求 | 输出 DWG | status | 实体数 | 统计 |
|---|---|---|---|---|---|
| 完整样例（32 构件） | headless/request.headless.json | D:\models\headless-demo.dwg | ok，errors=[] | 34 | Solid3d=11、Polyline=19、DBText=4、体积 86.8968 m³、图层 A-WALL/A-WINDOW/A-DOOR/A-ROOF/A-COLUMN/A-TEXT/A-DIM |
| 简化墙+楼板 | headless/request.min.json | D:\models\headless-min.dwg | ok，errors=[] | 3 | Solid3d=2（墙、楼板）、Polyline=1（楼板轮廓）、图层 A-WALL/A-FLOOR |
| 请求隔离 | 34 实体 DWG → 简化请求 | 同上 | ok | 3 | 仅新请求实体，XData 清场生效 |

### 5.2 Revit（Revit.exe + DocumentOpened/Idling）

| 用例 | 输入 | 结果 | 产物 | 退出方式 |
|---|---|---|---|---|
| 1 首次无头建模 | revit-seed.rvt | status=ok，created=[W1,F1,C1,PL1,TXT1]，entityCount=7，volume=6.528 m³ | revit-headless-out.rvt | 首版策略（Kill） |
| 2 增强场景 | 同上 | status=ok，created=[W1,W2,F1,C1,B1,PL1,TXT1]，entityCount=11，volume=11.64 m³ | revit-headless-out2.rvt（581632 B） | SC_CLOSE 生效，journal: finished recording |
| 3 落盘复检（report） | revit-headless-out2.rvt | allElements=1980；Marvis 墙 W1#0…W1#4（2 洞口+2 过梁）+ W2 整墙；DirectShape [B1,C1,F1,PL1]；TextNote "Marvis Headless Demo 2" | revit-report2-result.json | 自行退出 |

三次执行全程无人工点击、无对话框干预。样本文件：`headless/revit/`（revit-request.json、revit-result.json、revit-report2-result.json 等）。

## 6. 已知限制

- **Revit 冷启动慢**：首次约 3 分钟（许可初始化 `License initialization complete` 后才继续加载文档），脚本默认超时 600s，建议 `-TimeoutSec >= 300`；
- **墙体厚度**：取墙类型宽度，JSON 的 `thickness` 仅参与体积统计；要精确宽度需复制并修改 WallType；
- **report 墙统计**：按 Comments 前缀过滤，多墙命名需统一前缀才能全量统计；
- **Revit 请求防重触发**：请求文件会被归档 `*.done`，脚本目录收集已自动排除；重跑需另存新请求；
- **Revit 许可证/实例**：脚本按顺序逐 RVT 执行（不并行），避免多实例许可冲突；
- **CAD 扩展字段**：roof/door/window/开洞/2D 图元已编译进 `D:\cad\AutoCAD 2021\CadJsonPlugin.dll`（见 docs/plugin-extension.md），wall/column/beam/floor/instance 原生支持；
- **退出策略差异**：CAD accoreconsole 命令执行完即退出；Revit 依赖 SC_CLOSE 事件退出，极端情况下由 Idling Kill 兜底（已保存文档）。

## 7. 调用示例

```powershell
cd D:\skills\arch-bim-skill\headless

# 1) CAD 单请求（真无头）
.\run-headless-all.ps1 -Mode cad -CadRequest .\request.headless.json

# 2) CAD 目录批量 + 续跑（成功过的自动跳过）
.\run-headless-all.ps1 -Mode cad -CadRequestDir .\requests -CadResultDir .\results -Resume

# 3) Revit 单请求（复用 seed rvt）
.\run-headless-all.ps1 -Mode revit -RevitRequest .\revit\revit-request.json -RevitRvtFiles D:\models\seed.rvt

# 4) 双无头全量（CAD 目录 + Revit 目录，各自带请求，失败续跑）
.\run-headless-all.ps1 -Mode all -CadRequestDir .\cad-requests -RevitRequestDir .\revit\requests -RevitRvtFiles D:\models\seed.rvt -Resume

# 5) 指定超时与日志
.\run-headless-all.ps1 -Mode all -RevitRequest .\revit\revit-request.json -RevitRvtFiles D:\models\seed.rvt -TimeoutSec 900 -LogFile .\logs\my-run.log
```

### 参数速查

| 参数 | 说明 |
|---|---|
| `-Mode` | cad / revit / all（默认 all） |
| `-CadRequest` / `-CadRequestDir` | CAD 请求文件（可多次）/ 请求目录（自动收集 *.json） |
| `-CadInputDwg` | 种子 DWG，默认 headless\seed.dwg |
| `-CadResultDir` / `-CadSaveDir` | CAD 结果目录 / DWG 保存目录（默认与请求同目录） |
| `-RevitRequest` / `-RevitRequestDir` | Revit 请求文件 / 请求目录 |
| `-RevitRvtFiles` | RVT 文件，与请求一一对应；只给 1 个则所有请求复用 |
| `-RevitResultDir` / `-RevitSaveDir` | Revit 结果目录 / RVT 保存目录 |
| `-TimeoutSec` | 每任务超时秒数（默认 600） |
| `-LogFile` | 日志路径（默认 headless\run-headless-all.log） |
| `-Resume` | 跳过 result 已 ok 的任务（失败续跑） |

## 8. 产物与日志

- 统一驱动：`headless/run-headless-all.ps1`（CAD 单请求驱动 `headless/run-headless.ps1` 保留）；
- CAD 脚本：`headless/run.scr`；种子：`headless/seed.dwg`；
- CAD 结果样例：`headless/result.headless.json` / `headless/result.min.json`；
- Revit 样例：`headless/revit/`（revit-request.json / revit-result.json / revit-report2-result.json）；
- 模型输出：`D:\models\`（headless-demo.dwg / headless-min.dwg / revit-headless-out2.rvt 等）；
- 运行日志：`headless/run-headless-all.log`、`headless/logs/cad-<任务名>.out/.err`；
- 插件日志：`D:\cad\AutoCAD 2021\cad-json-plugin.log`、`D:\Revit 2022\revit-json-plugin.log`。
*（内容由AI生成，仅供参考）*
