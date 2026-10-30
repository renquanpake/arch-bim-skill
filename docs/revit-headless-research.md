---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_0ef27eefd06f11f1814c525400cd780f
    ReservedCode1: MVakNqgYEYudAk83vQKTWoBvaPw26H5aENdkz1ANJXOYrLOwrx1oPKtNNym/bp01xzay5WQqZliAmWtBFTXxa42BNFM6HQ2cMLxXU7DWx15p4kiiuEJJ1n2FPR29BRfZq3znKT7RmNbPNKDmR1MXqO5fUr/DK+1Vva0WPe4+94WGU2ur5S4/bw1xDQk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_0ef27eefd06f11f1814c525400cd780f
    ReservedCode2: MVakNqgYEYudAk83vQKTWoBvaPw26H5aENdkz1ANJXOYrLOwrx1oPKtNNym/bp01xzay5WQqZliAmWtBFTXxa42BNFM6HQ2cMLxXU7DWx15p4kiiuEJJ1n2FPR29BRfZq3znKT7RmNbPNKDmR1MXqO5fUr/DK+1Vva0WPe4+94WGU2ur5S4/bw1xDQk=
---

# Revit 无头（Headless）自动化研究

> 研究日期：2026-10-25
> 背景：CAD 侧已跑通"启动 acad.exe 进程 + 插件轮询消费 request.json → 写 result.json → 退出"的半无头闭环。本文件研究 Revit 2022 侧能否复刻同构闭环，实现不开 UI 交互的批量/无人值守建模。
> 结论先行：**可以复刻同构闭环，推荐"启动 Revit.exe 打开 RVT + DocumentOpened 事件自动触发插件命令"路线**（官方支持的 API 路线，不用 journal 破解）。

---

## 一、方案对比

| 方案 | 机制 | 官方支持 | 稳定性 | 适合场景 | 本地可行性 |
|---|---|---|---|---|---|
| A. DocumentOpened 事件 + 外部命令 | 插件订阅 DocumentOpened/Idling 事件，Revit 打开文件即自动执行建模命令 | 是（纯 API） | 高 | 批量无人值守、与 CAD 侧轮询同构 | ★★★★★ 推荐 |
| B. Journal 文件回放 | 预置 journal 文件触发 add-in 命令 | 否（Autodesk 明说不支持） | 跨版本不稳定 | 官方不推荐，仅了解 | ★★ 备选/兜底 |
| C. PostCommand | 外部命令用 PostCommand 启动内置/插件命令 | 是 | 高 | 进程内串联多个命令 | ★★★ 辅助（配合 A） |
| D. APS Design Automation（云端） | 云上无头 Revit，完整 Revit DB API | 是 | 高 | 云端服务、无需本地装 Revit | ★ 不推荐（需订阅付费，且破解环境不适配云端合规流程） |
| E. Dynamo CLI（DynamoWPFCLI.exe -o） | 命令行无头运行 Dynamo 图 | 是（Dynamo 功能） | 中 | 纯几何/参数化图，无 Revit 宿主时可跑 | ★★ 辅助（依赖装 Dynamo，Revit 关联图仍需宿主） |

---

## 二、推荐方案 A 架构（与 CAD 闭环同构）

```
brief/request.json（建模指令）
        │  （外部）轮询消费者
        ▼
启动 Revit.exe "<目标>.rvt"（带插件）
        │  DocumentOpened 事件触发
        ▼
RevitAddin（IExternalCommand / DocumentOpened handler）
        │  读 request.json → 用 Revit DB API 建模（墙/楼板/门窗/屋顶/楼梯）
        │  写 result.json（构件清单/体积/违规检查入口）
        ▼
退出 Revit.exe → 下一个 RVT
```

关键设计点：
1. **入口**：Revit 启动时即加载 add-in（.addin 文件注册 Application 级事件），DocumentOpened 触发时检测工作目录下 request.json 是否存在，存在才执行，否则直接退出——避免干扰正常手工使用。
2. **执行**：建模逻辑写 IExternalCommand（可被事件内 Invoke 复用），与 CAD 侧 CadJsonPlugin 同一套 JSON 协议（元素类型映射到 Revit API：Wall/Floor/Column/Roof/Door/Window/Stairs）。
3. **出口**：执行完写 result.json + Transaction.Commit 后，用 `Document.Close(false)` + `UIApplication.Exit()` 或按需轮询下一请求；批处理脚本负责监控进程存活、内存、失败自动重启续跑（Revit 单会话不宜打开几十个文件，批量必须分段重启）。
4. **事务**：全部建模包在单个 Transaction 内，失败回滚，保证幂等。

## 三、关键技术事实

- Journal 方案是**非官方、跨版本不稳定**的（The Building Coder 明确表态：`Customisation using the journal file is not officially supported`）。
- 纯 API 批量做法（Building Coder 推荐）：外部命令 + DocumentOpened 事件 + 必要时级联 Idling 事件；启动脚本逐文件启动 Revit 即可，完全不用 journal。
- PostCommand 是官方支持的编程启动命令方式，可替代 journal 触发场景。
- 大批量处理注意：Revit 按交互场景设计，单会话开太多文件会内存不足；严肃的批处理必须 日志+心跳监控+自动重启续跑。
- APS Design Automation：云端无头 Revit，全量 DB API，可处理 RVT/RTE/RFA，但需 Autodesk 账号订阅，且要上传模型到 Autodesk 云（破解环境不适用）。
- Dynamo CLI（DynamoWPFCLI.exe）：`-o` 参数以 headless 模式打开 Dynamo 跑图，适合无宿主场景；Revit 关联 Dynamo 图仍依赖 Revit 宿主，无法真正无头化。

## 四、落地步骤清单（建议）

1. [ ] 确认 Revit 2022 SDK 安装（RevitAPI.dll/RevitAPIUI.dll 路径，或从安装目录引用）
2. [ ] 新建 RevitAddin 项目（.NET Framework 4.8，net48 与 CAD 插件一致）
3. [ ] 实现 `IExternalCommand`：读 request.json → 映射 Revit API 建模（复用 CAD JSON 协议的元素字典）
4. [ ] 实现 Application 级 `DocumentOpened` 事件：检测 request.json → Invoke 命令 → 写 result.json → 退出
5. [ ] 部署 .addin 到 `C:\ProgramData\Autodesk\Revit\Addins\2022\`（Revit 2022 对应 Addins 目录）
6. [ ] 批处理脚本：逐 RVT 启动 Revit.exe → 轮询 result.json → 超时/崩溃自动重启续跑
7. [ ] 用样例 RVT + 样例 request 跑通闭环，验证构件数量与体积

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| Journal 方案被禁用/不稳定 | 一律不用 journal，走 DocumentOpened + IExternalCommand |
| 单会话批量内存泄漏 | 每文件一个 Revit 进程，跑完即退 |
| 事件误触发干扰手工使用 | 以 request.json 存在作为执行门禁，无请求即退出 |
| 破解许可环境下 API 行为差异 | 先在本机 2022 上验证最小建模命令（墙+楼板）再扩展 |
| 建模失败写坏 RVT | 先处理副本 RVT，成功后替换；事务回滚 |
*（内容由AI生成，仅供参考）*


---

# 补研（2026-10-26）：为什么"无头"仍然弹窗，以及"先画错再报错"

## 0. 结论先行

- **本地 Revit 没有真无头模式。** Revit.exe 不存在任何 headless/console 开关（二进制串扫描 `nosplash`/`headless`/`NoGui` 全部未命中）；官方唯一姿势是"GUI 进程 + API 驱动"。所谓"Revit 无头"本质是**自动化的 GUI**，窗口一定会创建。
- **窗口可以不进你的桌面。** 实测：`Start-Process -WindowStyle Hidden` 无效（splash 照弹）；**把 Revit 启动到私有桌面（CreateDesktop）后，用户桌面 30 秒内可见窗口数 = 0，进程正常运行**。
- **"先画错再报错"的根因是缺少前置校验**，不是插件不会建模。日志统计见 §3：Revit 侧 132 条失败/跳过里 96 条是低于 ShortCurveTolerance 的曲线、6 条零长度墙；CAD 侧 29 条里 20 条是 `eNotInDatabase`、2 条越界参数。全部可在发单前拦掉。

## 1. 弹窗根因（Phase 1 取证）

| 事实 | 证据 |
|---|---|
| Revit 无命令行无头开关 | `Revit.exe` 二进制扫描：`/nosplash`、`headless`、`NoGui`、`journal` 均未命中 |
| 现有链路依赖 GUI 事件 | `AutoRunner.OnStartup` 订阅 `DocumentOpened` + `Idling`；`RequestQuit` 用 `WM_SYSCOMMAND/SC_CLOSE` 关 MainWindowHandle —— 必须有窗口才能退 |
| 每次请求都新起进程 | `run-headless-all.ps1` / 手写调用均 `Start-Process Revit.exe <rvt>`，单次 25~60s 启动，必然顶到前台 |
| `-WindowStyle Hidden` 不生效 | 实测：t+6s 仍可见 `SECSplashWnd`（Revit 自建窗口，不吃 STARTUPINFO 的 SW_HIDE） |

## 2. 不弹窗的三种可行方案（按成本排序）

| 方案 | 做法 | 用户可见性 | 代价 | 风险 |
|---|---|---|---|---|
| **P1 私有桌面启动器** | `CreateDesktop("MarvisHidden")` + `CreateProcess(lpDesktop=...)` 启 Revit | **0 窗口**（已实测 30s） | 一个 ~80 行 C#/PowerShell 启动器，不改插件 | 需验证 GPU/DPI 差异下建模与出图；桌面不可交互 |
| **P2 常驻 Revit + 后台开文档** | 一次启动（隐藏/私有桌面），插件轮询请求目录，用 `Application.OpenDocumentFile(path, OpenOptions)` 在内存中开文档建模后关闭 | 0 窗口，且**只启动一次** | 插件改造（新增轮询 + 非 UI 开文档路径） | 单会话内存增长，需定时重启；RBP 同款做法 |
| **P3 云端 APS Design Automation** | 上传 appbundle + RVT 到 Autodesk 云跑 | 0 窗口（无本地进程） | 订阅/额度、上传下载、合规 | 与本地破解环境不匹配；离线不可用 |

> P2 的可行性有业界先例：RevitBatchProcessor 文档明确 —— Python 任务脚本下"only opens the document in memory"；RevitHeadless（MIT，2019-2022 实测）用 WCF + `Idling` 队列驱动**最小化**的 Revit。两者都证明"窗口可不干扰人，进程常驻复用"。

**建议顺序：P1 立竿见影（今天就能消掉弹窗）→ P2 解决"每次都启动"（吞吐 + 体感）→ P3 只在需要服务器化时考虑。**

## 3. "画错再报错"的失败面（日志统计）

Revit 侧 `revit-json-plugin.log`：132 条失败/跳过

| 次数 | 类别 | 性质 |
|---|---|---|
| 64 SKIP + 32 FAIL | 楼梯踏步线/洞口线 `Curve length is too small` | 生成端没按目标容差裁剪，32 条是**真报错** |
| 7+5+5+5 SKIP | 洞口 2D 线过短 | 同上，被跳过 |
| 3+3+1+1 FAIL | `SP-L/SP-R/EXT-E/EXT-W` 零长度墙 | planner 产出了退化墙段 |

CAD 侧 `cad-json-plugin.log`：29 条失败

| 次数 | 类别 | 性质 |
|---|---|---|
| 20 | `未在命名对象字典中找到数据库实体`（墙段/柱轮廓/屋顶/DIM-W） | 事务内建实体后归属/图层异常，成批出现 |
| 2 | `参数超出范围…小于集合大小`（text2d） | 取点/索引越界 |
| 1 | `floor 缺少 outline 多边形` | 输入侧未校验 |

共同点：**全部是在目标软件里"先建一半再抛错"**。CAD 侧 `CreateElement` 逐元素 try/catch，失败只记账不回滚，于是"画错的半成品"留在 DWG/RVT 里。

## 4. 修法（研究结论，不含实现）

1. **前置校验器（Python，发单前跑）**——平台无关规则 + 平台特性规则：
   - 几何退化：长度/面积/厚度 ≤ 容差（墙段、踏步线、洞口线）；零长度墙直接剔除并回写 planner。
   - 包容性：洞口 `startX+width ≤ wall.length`（今天已修 clamp）、`sill+height ≤ wall.height`、楼梯 footprint 不出楼层轮廓。
   - 单位/坐标：z 落在 `[0, floors*floorHeight]`，楼梯 `fromFloor` 连续。
   - 输出 `violations.json` 复用现有合规报告结构，`passed=false` 就不发单。
2. **容差单一真源**：Python 侧硬编码 0.6mm/1.6mm，Revit 侧用 `doc.Application.ShortCurveTolerance` —— 两边不一致正是 32 条 FAIL 的直接原因。改成"发单前按目标平台配置裁剪"。
3. **失败即回滚**：插件侧元素级失败不落库（Revit 已有 Transaction；CAD 侧每元素单独事务改为整单事务或失败即删）。
4. **结果断言**：用 `byType`/`entityCount`/`volumeM3` 与请求期望值对账，少一个构件就判失败，避免"看起来 ok 其实缺件"。
