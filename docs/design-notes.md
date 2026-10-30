# 历史设计笔记（revit-model-mcp 借鉴整合，P0/P1）

> 本文承接自 SKILL.md 的历史设计排期记录，非执行指令。执行手册见 `SKILL.md`，外部参考摘录见 `docs/reference-digest.md` 第六章。

## 9.5 revit-model-mcp 借鉴整合（P0/P1，详见 docs/reference-digest.md 第六章）

> 来源：`D:\cad\arch-bim-reference\revit-model-mcp`（60+ 工具 MCP 服务器，Revit 2022-2027）。以下仅登记可落地的整合设计，代码实现按优先级排期。

### P0-1 无头安全模型（对接 SKILL.md §6 Revit 无头）

| 机制 | 落地方式（RevitAddin + 驱动层） |
|---|---|
| 默认只读 | 请求目录放置 `read-only` gate 文件时，插件拒绝所有建模动作，返回 `errorCode:"read_only"`；`REVITJSON_READ_ONLY=1` 环境变量为第二开关 |
| confirm token 两步流 | 高危请求（覆盖保存 / 删除 >500 / 执行脚本）先返回 `needsConfirmation+confirmationText+confirmToken`，零改动；带 token 重试才执行，token 绑定请求哈希+文档状态，5 分钟过期 |
| dry_run 预演 | 建模前先 `dry_run`：事务内执行→读预演结果→回滚，返回 `verification.before/after` 前后对照，不落库 |
| 单 undo 入口 | 整个建模动作合并一个 `TransactionGroup`，assimilate 成单条 undo 条目 `MCP (<client>): <summary>`（≤60 字符）；另加 `revit_undo_last` 走 Revit 自带 Undo |
| 空提交回滚 | 真实执行后无任何元素变更则回滚，返回 `committed:false, rolledBack:true`，不产生 undo 条目 |

### P0-2 CAD→墙配对（承接管线 DWG 输出）

- 从 DWG 墙线（A-WALL 图层）直接配对生成 Revit 墙，替代逐墙创建：同层共线段 0.5°/5mm 容差合并、跨缺口搭桥、厚度区间配对、宽度就近选基本墙类型。
- `join=true` 时中心线延伸/裁剪到 L/T/X 交叉并启用 Revit 连接；返回 `walls/unpairedLines/mergedSegments/unjoinedEnds+reasons`，dry_run 同路径不落库。
- 未知图层名直接拒绝并列出可用图层（不猜名，与防幻觉原则一致）。

### P1 查询层本地化名称解析 + 批量流水线

- 类别/参数名四种形态解析：UI 名 / BuiltInCategory 枚举名 / 英文名 / 类别 ID，未知名拒绝并给近似候选——名称必须从模型取，禁止翻译/猜测。
- 批量：多模型 steps+code+exports 流水线，长任务 job 轮询（`status:"running"+jobId+progress`）与 `cancel_job` 取消；已完成模型不回滚。
