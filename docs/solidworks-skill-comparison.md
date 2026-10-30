# SolidWorks 自动化 Skill vs 我们的 arch-bim-skill（对比 2026-10-29）

> 对比对象：
> - **SW-A** `wzyn20051216/solidworks-automation-skill`（1.6k★，MIT，2026-10-29 仍在更新，322 commits）
> - **SW-B** `andrewbartels1/SolidworksMCP-python`（86★，MIT，399 commits，自述"under construction"）
> - **我们** `D:\skills\arch-bim-skill`（自研，Building/BIM 域）

---

## 1. 一句话定位

| | 定位 | 领域 |
|---|---|---|
| SW-A | AI Skill + MCP 工具包，驱动**真实桌面 CAD**（SolidWorks 2024/2026，AutoCAD 可选，OCCT 开放格式兜底） | 机械零件/装配/GB-T 工程图 |
| SW-B | 纯 MCP server（151 tools），COM/VBA 路由 + mock 模式 | 机械零件 |
| 我们 | Skill + JSON 协议 + 原生插件（AutoCAD/Revit），**建筑/BIM** | 建筑：墙板柱梁梯、机电、平立剖、IFC |

---

## 2. 架构对比

### SW-A
```
User/Agent → Skill / MCP / CAD Studio(三入口平级)
           → Execution Core（Capability · Verification · Recovery Decision · Trace）
           → Backend Router
           → Python COM / C# / OCCT
           → CAD → Artifacts + Evidence
```
- Root Skill = 通用能力；`subskills/` = 专项工作流（工程图、螺纹孔、CNC 圆角、齿轮、AutoCAD DWG/DXF）
- 能力清单 `capabilities.yaml`，状态分级 `verified / stable / pilot / blocked`
- MCP：完整模式 66 个命名工具；默认只暴露 3 个检索入口（`sw_search_tools` / `sw_describe_tool` / `sw_call_tool`）

### 我们
```
intent.json → brief_to_cad.py → request.json（唯一数据源）
  ├→ AutoCAD accoreconsole（真无头）→ 3D DWG + 平面排版 + 立面/剖面 + 套图
  ├→ Revit（签名 DLL + 私有桌面伪无头）→ RVT + 每层平面/剖面 DWG + Level/Room/Grid
  ├→ ifc_export.py（IfcOpenShell）→ IFC4（真洞口 IfcRelVoidsElement + 布尔）
  └→ schedules.py / sheets.py / annotations.py → 面积表/工程量/门窗表/图框标注
```
- **文件协议驱动**（request/result JSON），不依赖 MCP tool calling
- 三段验证：`result.json` 四判据（status/errors/entityCount/savedTo）+ 体积 + `smoke.py 16/16`
- 发单前 `preflight.py` 防呆（退化几何/洞口越界/共线墙重叠/z 越界…）

---

## 3. 关键能力对照

| 维度 | SW-A | SW-B | 我们 |
|---|---|---|---|
| Agent 接入方式 | **Skill + MCP（3 检索工具）** | MCP（151 tools） | 无 MCP；靠 SKILL.md + CLI |
| 无头能力 | COM 需 GUI 进程（最小化/后台） | 同 | **AutoCAD 真无头**（accoreconsole）；Revit 私有桌面 |
| 不装 CAD 也能出活 | OCCT 写 STEP/BREP/STL/OBJ/GLB | mock（模拟，不可信） | **IFC4**（ifcopenshell） |
| 语义深度 | 特征级（草图/拉伸/螺纹/圆角） | 同 | 建筑构件 + IfcSpace + Pset + 真洞口布尔 |
| 2D 出图 | GB/T 工程图、尺寸链、孔表、BOM | 工程图工具 | 每层平面/4 立面/剖面 + A1 图框 + 标题栏/会签栏 + 门窗表/面积表 |
| 验证体系 | **43 deterministic scenarios + 12 golden workflows + 1045 pytest + 证据系统** | 部分测试 + mock 警告 | smoke 16/16 + result 四判据 + 30 层压测 |
| 能力矩阵 | `capabilities.yaml` 带 verified/stable/pilot/blocked | 无 | 仅文档 `docs/complete-building.md`（人读，无分级） |
| 失败恢复 | **Backend Router + Recovery Decision** | 有限 | 无（人工选 CAD/Revit） |
| 可追踪/审计 | **结构化 Trace + Evidence** | SOC 日志→可回放 Python | 日志（插件 log）+ result.json |
| 安装/注册 | `npx` 安装器、拒绝覆盖、`.bak` 备份、默认不装依赖 | venv + 脚本 | 手工复制脚本 + UAC 部署 DLL |
| 跨平台 | 非 Windows 仅开放格式 | 同 | 强依赖本机 AutoCAD/Revit 路径 |

---

## 4. 我们强在哪 / 弱在哪

**强**
1. **真无头**：accoreconsole 不开 GUI；Revit 用私有桌面 + 签名 DLL，用户桌面 0 窗口。SW 系全靠 COM，必须有个 GUI 进程。
2. **建筑语义**：IfcSpace、楼层、轴网、真门窗洞口布尔、面积/工程量表——SW 是机械零件，无建筑空间概念。
3. **规模化**：30 层 3825 构件 65 秒实测；SW 的验收是单件/装配。
4. **出图链路完整**：平立剖 + 套图 + 表格 + 标注（门弧/窗三线/会签栏）。

**弱（也是该抄的）**
1. **没有 MCP**：Agent 只能靠读 SKILL.md 去调 CLI；SW-A 的核心卖点就是 tool calling。
2. **没有能力矩阵**：SW-A 用 `capabilities.yaml` + 四级状态，明确"哪些是 verified、哪些是 pilot、哪些 blocked"。我们只有一份人读文档。
3. **没有 Trace/Evidence**：它们把每次执行落成可审计证据包；我们只有 result.json + 日志。
4. **没有 Recovery Router**：CAD 失败不会自动切 Revit/IFC；SW-A 有 Backend Fallback + Capability Gap 结构化返回。
5. **测试深度**：它们 43 个确定性场景（含故障注入、假完成检测）；我们 16 个 smoke，无故障注入。
6. **单文件巨石**：`brief_to_cad.py` 已 ~1300 行；SW-A 拆 root skill + subskills。

---

## 5. 该抄什么（按性价比排序）

| 优先级 | 动作 | 成本 | 收益 |
|---|---|---|---|
| P0 | **MCP 包装**：把 `build/export_dwg/export_ifc/plans/sheets/verify` 暴露成 6~8 个 MCP 工具 | 1~2 天 | Agent 直接 tool-calling，不用读长文档 |
| P0 | **capabilities.yaml**：给每个能力标 verified/stable/pilot/blocked + 版本/限制 | 半天 | 杜绝"没验证就敢说完成" |
| P1 | **Evidence 包**：每次运行产出 `evidence/{request,result,log,dwg 哈希,截图}` | 1 天 | 可审计、可复现 |
| P1 | **故障注入测试**：退化几何/空 request/权限失败/超时，断言"不假完成" | 1 天 | 对齐他们 43 场景思路 |
| P2 | **Backend Router**：CAD 主 → 失败切 Revit → 再切 IFC 导出 | 2 天 | 一条命令多后端兜底 |
| P2 | **拆 subskills**：平面/立面/套图/IFC 各自 SKILL.md | 1 天 | 按需加载，省 context |

---

## 6. 结论

- **SW-A 强在"工程化外壳"**：MCP、能力矩阵、证据、恢复、确定性测试。它的代码复杂度和可靠性工程投入远超我们，但领域是机械零件。
- **我们强在"领域深度 + 真无头 + 规模"**：建筑全专业、IFC 语义、30 层、零窗口打扰。
- 两者**不冲突**：SW-A 的 Execution Core 思路可以直接借来套我们的管线。最值得先做的是 **MCP 包装 + capabilities.yaml**，这两项加起来两天，就能把"Agent 能不能自己用起来"这个短板补掉。
