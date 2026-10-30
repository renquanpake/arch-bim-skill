# arch-bim-skill 外部参考精华摘录

> 来源：5 个外部仓库（已 clone 至 `D:\cad\arch-bim-reference\` 目录）
> 整理时间：2026-10-25
> 目的：把值得抄的东西提炼出来，供 arch-bim-skill 二期扩展参考

## 仓库清单

| 仓库 | 目录 | 一句话定位 |
|------|------|-----------|
| Agentic BIM Team | D:\cad\arch-bim-reference\agentic-bim-team | 多 Agent 审查闭环（设计→四专家审查→BCF反馈→修复循环） |
| AI Architect Agent | D:\cad\arch-bim-reference\ai-architect-agent | 确定性平面图规划器（LLM意图→确定性布局→合规校验） |
| Sara Bensalem Skills | D:\cad\arch-bim-reference\sara-bensalem-skills | 建筑规范知识包（PMR/ADA 无障碍、1:20 墙身构造、疏散） |
| Claude CAD Skill | D:\cad\arch-bim-reference\claude-cad-skill | AutoCAD 无头施工图编辑（keynote/schedule/云线/PDF出图） |
| SkyCiv AI Skills | D:\cad\arch-bim-reference\skyciv-ai-skills | 结构计算 API 封装（荷载规范、快速计算器） |
| revit-model-mcp | D:\cad\arch-bim-reference\revit-model-mcp | Revit 插件+Python 桥的 60+ 工具 MCP 服务器（2022-2027） |

---

## 一、Agentic BIM Team —— 多 Agent 审查闭环（最值得抄）

### 核心模式
```
设计(architect) → 预分析(visual-qa) → 四专家并行审查(structural/mep/facade/fire)
→ BCF 问题报告 → architect 按 BCF 修复 → 收敛循环(最多3轮)
```

### 可抄点 1：BCF 审查协议
- 每个专家 Agent 只读模型、写 BCF 问题报告，绝不直接改设计（职责隔离）
- BCF topic 必须带 `globalIds`（问题定位到具体构件，可高亮跳转）
- 每条问题必须带修复建议："X is wrong, fix by Y"，空泛批评不可接受
- 无问题时也要写一条 INFO topic（"审查通过"），让编排器知道它跑过

### 可抄点 2：预分析文件约定
- 设计完成后先把模型拆成 `walls.json / slabs.json / columns.json / spaces.json / stairs.json` 等预分析数据
- 专家 Agent 直接读 JSON，禁止自己跑查询命令（省 token、防幻觉）

### 可抄点 3：规范绑定方式
- 每个专家 Agent 的 system prompt 绑定具体规范条款：
  - 结构：SIA 260-265
  - MEP：SIA 382.1（每层必须有通风井、层高≥2.8m）
  - 立面能耗：SIA 380/1、Minergie（外墙 U≤0.20、窗 U≤1.0）
  - 消防：VKF（隔墙 EI60、楼梯宽≥1.2m）
- 关键构件属性在 architect 阶段就写全（LoadBearing、FireRating、ThermalTransmittance），避免被专家拒

### 可抄点 4：收敛编排
- MAX_CYCLES=3，设计-审查-修复循环直到无 ERROR
- 构件属性用 PropertySet 标准化（Pset_WallCommon / Pset_SlabCommon）
- 所有坐标相对楼层标高，禁止双重偏移

### 对我们项目的启示
arch-bim-skill 目前是单程生成（口述→brief→DWG），可加一层**规范审查循环**：
生成 DWG → 提取构件清单（墙/门/窗/楼梯）→ 规范检查 Agent 对照 GB 规范出问题清单 → 改 brief 重新生成。

---

## 二、AI Architect Agent —— 确定性平面规划器（可直接移植思路）

### 核心模式
```
DesignIntent(LLM解析) → _Planner(确定性布局) → BuildingSpecification → Blender编译
```

### 可抄点 1：Spine-Column 布局模型
```
rear ┌────────┬──────┬────────┐
     │        │ slot │        │    spine = 走廊/楼梯间，从前墙延伸到后墙
     │ left   ├──────┤ right  │    左右两列房间贴 spine，房间同时触达外墙(采光)
     │ column │spine │ column │    slot = 小房间(WC/浴室/储物)放在 spine 后
front └────────┴──────┴────────┘
```
- 每列房间都贴 spine（可达）且贴外墙（有窗）——这是规则，不是巧合
- 房间深度按目标面积比例分配（Claude 给面积，代码分深度）

### 可抄点 2：锚定修改（anchor）
- 用户口语改图（"客厅加宽1米"）时，保留原有布局中未改动部分（anchor 机制）
- 修改走 changeset/diff/apply，不重建整个模型
- 搜索 spine 位置每 10cm 一次，带惩罚系数避免"孤立房间"

### 可抄点 3：意图预校验
- 布局前先跑 validate_intent（面积、长宽、楼层数合法性），错误直接拒绝，不硬画

### 可抄点 4：楼梯计算
- calculate_stair(floor_height, run)：riser/tread 比例确定性计算
- 商业建筑 stair_width=1.1m，住宅 0.9m；run 商业 0.28 / 住宅 0.25

### 对我们项目的启示
可以把这套布局逻辑移植到 brief_to_cad.py：LLM 只出 intent（总面积、房间清单、类型），
布局由确定性代码生成（spine+columns+slot），避免 LLM 直接给坐标导致墙不闭合。

---

## 三、Sara Bensalem Skills —— 规范知识包（可直接抄校验规则）

### 可抄点 1：PMR/ADA 无障碍校验阈值（spatial-anatomy）
| 项 | 最小值 | 规范来源 |
|----|--------|---------|
| 门净通行宽 | ≥830mm | 法国 PMR 2015-12-24 / ADA 2010 |
| 门扇标称宽 | ≥900mm | 同上 |
| 前室/无障碍厕所回转圆 | Ø≥1500mm | PMR/ADA |
| 单向轮椅走廊 | ≥1200mm | IBC |
| 双向通行走廊 | ≥1400mm（推荐1800） | IBC |
| 尽端走廊最大长度 | ≤12m（法国ERP）/≤20ft(IBC) | IBC 1017 |
| 疏散行走距离 | ≤30m | IBC |

### 可抄点 2：1:20 墙身构造（constructive-detail）
- 连续热桥阻断（Schöck Isokorb）、EPDM 防水、挂板次框架
- 精确 mm 标注 + 厂家规格（Knauf、EPDM），露点/U 值验算
- 教训："3D 渲染陷阱"——渲染图好看≠可施工，构造细节要可验证

### 对我们项目的启示
把无障碍/疏散阈值做进规范检查 Agent 的规则库，生成平面图后自动校验：
门宽、走廊宽、回转圆、疏散距离，输出违规清单。

---

## 四、Claude CAD Skill —— AutoCAD 无头施工图编辑（插件深化）

### 可抄点 1：LISP 工具集
- `lib.lsp`（公共库）、`census.lsp`（图面普查）、`revcloud.lsp`（云线）、`mlarrow.lsp`（多引线箭头）
- 可直接参考脚本组织方式，用 LISP 扩展本地 CadJsonPlugin 覆盖不到的编辑场景

### 可抄点 2：施工图深化能力
- keynote 重编号（气泡+图例+图例行三处同步）
- schedule 增删行/改单元格（外框随行拉伸、下方内容避让检查）
- revision cloud + delta 标注（不碰文字）
- 每次编辑后重新出图自检连锁问题；每次保存先备份

### 可抄点 3：图纸规范沉淀
- `D:\cad\arch-bim-reference\claude-cad-skill\skills\cad-edit\reference\graphic-standard.md`：线宽、遮挡关系、datum、VIF 尺寸、无障碍安装高度
- 图纸规范独立成文档，供 Agent 生成施工图时遵守

### 对我们项目的启示
本地插件出的是模型 DWG，施工图深化（标注/表格/云线/出图）可参考此仓库的
LISP 思路和 graphic-standard 规范，二期做施工图阶段直接借用。

---

## 五、SkyCiv AI Skills —— 结构计算（暂缓，仅记录）

- 偏 API 封装（结构分析 S3D、荷载查表、150+ 快速计算器），依赖 SkyCiv 云端服务
- 对本地 CAD 工作流价值低，除非要做结构验算，否则不整合

---

## 整合优先级建议

| 优先级 | 抄什么 | 落地方式 |
|--------|--------|---------|
| P0 | 规范审查闭环（agentic-bim-team 模式） | brief_to_cad.py 后加合规检查阶段，出问题清单回改 brief |
| P0 | 无障碍/疏散校验阈值（sara） | 新建规范规则库，审查 Agent 读取 |
| P1 | Spine-Column 确定性布局（ai-architect） | 移植到 brief_to_cad.py，LLM 出 intent、代码出布局 |
| P1 | 意图预校验（ai-architect） | brief 校验：面积/长宽/楼层合法性 |
| P0 | 安全模型（revit-model-mcp） | 默认只读+confirmation token+dry_run+单 undo 入口，无头方案同步接入 |
| P0 | CAD→墙配对（revit-model-mcp） | brief_to_cad 出的 DWG 墙线→配对生成 Revit 墙，替代逐墙创建 |
| P1 | 本地化名称解析（revit-model-mcp） | 查询层：类别/参数名从模型取，四种形态解析，不猜名 |
| P1 | 批量处理流水线（revit-model-mcp） | 多模型 steps+code+exports，长任务 job 轮询/取消 |
| P2 | 施工图深化 LISP 集（claude-cad） | 二期扩展插件 |
| P3 | 结构计算（skyciv） | 暂缓 |

## 待办（来自本次抄作业）
- [ ] 把 P0 规范审查闭环写进 SKILL.md 工作流
- [ ] 建规范规则库（无障碍/疏散/楼梯/防火分区）供审查 Agent 读取
- [ ] Spine-Column 布局移植到 brief_to_cad.py
- [ ] brief 预校验（面积/长宽/楼层合法性）
- [ ] 无头方案接入默认只读+confirm token+单 undo 入口
- [ ] DWG 墙线配对算法移植到建模插件
- [ ] 查询层本地化名称解析（四种形态+不猜名）


---

## 六、revit-model-mcp —— Revit 插件+Python 桥的 60+ 工具 MCP 服务器（最值得抄）

> 仓库：`D:\cad\arch-bim-reference\revit-model-mcp`（sharafutdinovdi/revit-model-mcp，支持 Revit 2022-2027）

### 架构模式
```
Python MCP server (stdio) ──named pipe / 文件通道(job_*.json+heartbeat) / HTTP──▶ Revit 内 C# Addin
                                                                                    └─ ExternalEvent → API 线程执行 handler → 返回 JSON
```
- 通道目录 `%LOCALAPPDATA%\RevitModelMcp`；server 发 job，Addin 用 ExternalEvent 在 API 线程执行命令
- 三个传输：PipeChannel（本地最快）、FileChannel（跨机/SSH）、HTTP（远端）；HTTP 面向单实例，文件通道自动发现工作站实例
- 与 arch-bim-skill 无头方案的关系：本仓库是"Revit 常驻+外部调度"，我们是"事件触发的一次性无头进程"；可复用其 job/heartbeat/结果归档设计

### 可抄点 1：安全模型（最完整的一套）
- **默认只读**：`REVIT_MCP_READ_ONLY=1` 或工作站 gate 文件 `%LOCALAPPDATA%\RevitModelMcp\read-only`，两类独立开关任一即全局只读，工具仍在列表但返回 `errorCode:"read_only"`；管理员可用机器策略强制，用户无法解除
- **confirmation token 两步流**：save/sync/close-with-loss/删>500/覆盖导出/移除链接/C# 执行，首次调用返回 `needsConfirmation + confirmationText + confirmToken` 且零改动，用户明确同意后带 token 重试；token 5 分钟过期、单次、绑定命令+文档+参数+文档状态，防注入到模型数据里的指令
- **dry_run 预演**：所有变更类工具支持，事务内执行→读预演结果→回滚；返回与真实执行完全相同的 `verification` 结构（`before/after/changed` 前后对照）
- **单 undo 入口**：每个 action 或 1-50 步 batch 合并进一个 `TransactionGroup`，assimilate 成一条 Revit undo 条目 `MCP (<clientName>): <short summary>`（≤60 字符）；`revit_undo_last` 走 Revit 自带 Undo
- **空提交自动回滚**：真实执行后未改变任何元素则回滚，返回 `committed:false, rolledBack:true`，不产生 undo 条目
- **结果保留 24h**：jobs/<jobId>.json 跨 server 重启仍可取；`REVIT_MCP_REDACT_PATHS=1` 路径脱敏

### 可抄点 2：CAD→墙 工具（revit_walls_from_cad / revit_link_cad）
- `revit_link_cad` 链接/导入 DWG 到平面视图，返回实例 ID、各图层线数、XY 范围(mm)
- `revit_walls_from_cad`：导入几何+选中 CAD 图层→自动配对生成墙
  - 同层共线段 0.5°/5mm 容差内合并，跨 `max_gap_mm` 缺口搭桥，短段参与合并后再过滤长度
  - 厚度范围内区间配对，宽度就近选基本墙类型；`join=true` 时中心线延伸/裁剪到 L/T/X 交叉，启用 Revit 连接
  - 返回 walls/unpairedLines/mergedSegments/width mismatches/unjoinedEnds+reasons；`dry_run` 同路径不落库
  - 未知图层名直接拒绝并列出可用图层
- 与"口述建筑→2D 平面 DWG"流水线直接互补：CAD 画墙线→revit 配对成墙，省去逐墙创建

### 可抄点 3：查询/参数工具集（本地化名称解析）
- `revit_list_catalog` 取本地化类别/参数名（当前模型语言），`revit_query_elements`/`revit_aggregate_elements` 按名过滤
- 类别名接受 UI 名 / BuiltInCategory 枚举名 / 英文名 / 类别 ID 四种形态，未知名拒绝并给近似候选——不猜名
- `revit_update_parameters`：过滤器批量更新实例参数，dry_run 预测 skip（inGroup/missing/readOnly/typeParameter）
- 与已有 digest 的防幻觉原则一致：名称必须从模型取，禁止翻译/猜测

### 可抄点 4：C# 动态脚本（revit_execute_code）
- 提交 `public static object Execute(ScriptContext ctx)` 主体或完整编译单元，≤200,000 字符，Revit API 线程编译运行
- `ScriptContext` 提供 UiApplication/Application/UiDocument/Document/Log/ToMm/FromMm
- `transaction="auto"` 默认：自动包事务+group，单 undo 条目；`dry_run` 强制 auto；成功但零改动自动回滚
- 每次调用（除 dry_run）需 confirm token，token 绑定代码 SHA-256；编译缓存 32 个脚本；返回 depth≤6、集合≤5000 项的 JSON；活动面板记 SHA-256 前缀+审计头
- 2022-2024 (.NET Framework) 脚本无法卸载，相同代码复用同一程序集；2025+ 调用后卸载

### 可抄点 5：批量处理（revit_process_models）
- 1-500 个 .rvt 路径或 folder+recursive+pattern，交互式会话后台打开逐个处理
- steps（同 revit_run_actions 1-50 步）+ code + exports 组合流水线；`save.mode` 支持 none/output_dir/in_place（in_place 需 token）
- 失败模型不阻断后续（除非 stop_on_error），逐模型报告 open settings/step summary/script log/export files/saved path/dialogsDismissed/error
- 长任务：server 等待预算（默认 40s）后返回 `status:"running"+jobId+progress+partial`，`revit_jobs(job_id, wait_seconds)` 轮询直到原始响应返回；绝不重提进行中的任务；`revit_cancel_job` 可取消（已完成的不回滚）

### 可抄点 6：两个配套 Skill
- **revit-model-coordinator**（只读审查 playbook）：document_info → model_health → list_warnings → links_status → shared_coordinates → parameter_fill_check → family_audit 固定顺序；证据纪律：非空 skipped/partial 即结论不完整、缺失≠0、无需求不编造 pass 规则；分级 critical/major/minor/info
- **iso19650-issue-register**（EIR/BEP→Excel 问题登记）：Step1 提取可检查需求（条款+原文+计划证据）→ Step2 查模型（工具+证据表）→ Step3 写 findings（引用条款+实测事实+建议+责任角色）→ Step4 生成 xlsx（含元素快照，≤25 张，按严重度）→ Step5 汇报路径+严重度表+Top5
- 两条都坚持"模型事实与项目规则假设分离"——审查只读，变更前先描述计划再用 dry_run 预演

### 对 arch-bim-skill 的启示
1. 当前"无头 Revit 一次性建模"安全薄弱：可把 confirmation token + 默认只读 + 单 undo 入口搬进无头方案（*.done 归档后仍可留 undo 名）
2. CAD→墙配对是"口述→2D 平面"的直接下游，二期可把 DWG 墙线解析做成同一套配对算法，一次建模全墙落地
3. 本地化名称解析 + 不猜名原则应进 brief_to_cad 的查询层
