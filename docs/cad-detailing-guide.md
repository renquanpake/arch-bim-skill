---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_c959dd4ed06c11f19fe7525400de85a5
    ReservedCode1: K/D5sluPpIDuH+3aEHqu5dgRkHwYDrCIz/Xsdx7DgrMk75zJfnyM8phwqx3E9wpL8lY0XGVEtuYTdVVw7pm91c63BYFwGP81QdquP40YqhtaNTNyobXiZgwR3urSOZUGrC3sZX3uydbnf+7icQ/OoMCgVkCN5kHjk3Li/avIh7zUmMVCj7F7iGcRmaw=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_c959dd4ed06c11f19fe7525400de85a5
    ReservedCode2: K/D5sluPpIDuH+3aEHqu5dgRkHwYDrCIz/Xsdx7DgrMk75zJfnyM8phwqx3E9wpL8lY0XGVEtuYTdVVw7pm91c63BYFwGP81QdquP40YqhtaNTNyobXiZgwR3urSOZUGrC3sZX3uydbnf+7icQ/OoMCgVkCN5kHjk3Li/avIh7zUmMVCj7F7iGcRmaw=
---

# 施工图深化参考文档（CAD Detailing Guide）

> arch-bim-skill 的 P2 沉淀：面向「从 3D 建模结果到可交付施工图」的深化参考。
> 参考来源：`D:\cad\arch-bim-reference\claude-cad-skill`（LISP 工具集）、`D:\cad\arch-bim-reference\claude-cad-skill\skills\cad-edit\reference\graphic-standard.md`（出图规范）、
> `D:\cad\arch-bim-reference\agentic-bim-team`、`D:\cad\arch-bim-reference\sara-bensalem-skills`（DWG 标注习惯）。
> 本文档仅沉淀思路与清单，不要求完整实现；落地时可分批扩展插件 2D 能力。

---

## 1. 出图阶段划分

| 阶段 | 输入 | 产出 | 要点 |
|---|---|---|---|
| 建模 | brief/request → DWG 3D | 建筑实体（wall/floor/column/roof…） | 现有 brief_to_cad.py 闭环 |
| 深化 | 3D 模型 + 本参考 | 平面/立面/大样 | 标注、表格、云线、线型 |
| 出图 | 深化成果 | 按图号组织 DGN/PDF | 图框、图签、打印样式 |

当前 arch-bim-skill 聚焦「建模 → 深化」衔接：`brief_to_cad.py --plan` 已产出基础 2D
俯视线框与尺寸标注；本参考给出进一步深化（云线、表格、符号、图纸组织）的可用思路。

---

## 2. 标注规范（graphic-standard.md 摘录）

| 项目 | 规范建议 |
|---|---|
| 文字 | 中文：仿宋 / 长仿宋；数字：Arial / 等线。图中最小字高 ≥ 2.5mm（1:100 下对应 250mm 模型单位） |
| 尺寸标注 | 图层 `A-DIM`，线宽 0.18mm；尺寸界线出界 2~3mm；箭头斜线 45° 短斜线或圆点 |
| 标高 | 三角形标高符号（空心/实心），数字两位小数；结构标高与建筑标高分开 |
| 索引符号 | 圆 φ10mm 内写「图号-索引号」；被索引图在上、本图在下 |
| 轴线 | 细点划线 `A-AXIS`，轴号圆 φ8mm，编号：横向 1,2,3…、纵向 A,B,C… |
| 线宽体系 | 0.7mm 粗线（剖切/墙轮廓）、0.35mm 中粗线（门窗/可见边）、0.18mm 细线（填充/标注） |

## 3. LISP 工具集思路清单（claude-cad-skill）

| 参考文件 | 思路 | 本项目可落地位置 |
|---|---|---|
| `lib.lsp` | 通用辅助：坐标取点、对象过滤、图层切换封装 | `--plan` 的 2D 图元生成逻辑可复用图层/线型封装 |
| `census.lsp` | 图纸统计：遍历实体生成门窗表/房间表 | brief 房间/门窗清单天然结构化，直接出表（见 §4） |
| `revcloud.lsp` | 修订云线：沿多边形/矩形生成云线、标记修订号 | 规范审查 `violations.json` 可映射为图面云线圈注（§5） |
| `mlarrow.lsp` | 多段线箭头：标注引线、疏散方向箭头 | 平面疏散路径箭头：`polyline2d` + 箭头符号 |
| 其他 | 标高标注、轴号递增、填充 HATCH 处理 | 可扩展为插件 2D 元素类型（text2d/polyline2d 已有） |

## 4. 深化表格（结构化 JSON 即表格源）

brief 与布局结果中的结构化数据可直接生成图纸表格：

| 表 | 数据源 | 列建议 |
|---|---|---|
| 房间表 | `brief.rooms` | 编号 / 名称 / 面积 / 层高 / 楼板标高 |
| 门窗表 | `walls[].openings` + `doors` | 编号 / 类型 / 宽×高 / 樘数 / 备注 |
| 楼梯表 | `stairs` | 编号 / 层段 / 踏步高 / 踏面宽 / 踏步数 / 净宽 |
| 防火分区表 | 布局 `fireCompartments` | 分区号 / 面积 / 耐火等级 / 分隔措施 |

## 5. 规范审查 → 云线圈注闭环

```
brief --check → violations.json
     └→ 按 category 归类 → 图面云线（revcloud 思路）
         每处违规生成 revcloud 多边形 + 修订引注（ruleId + fixHint）
```

云线参数建议：弧长 300~500mm（模型单位）、图层 `A-CLOUD`、线宽 0.25mm；
修订号递增（R1, R2…），与审查报告 violation 序号对应，实现「图面问题定位 ↔ 清单逐条对照」。

## 6. 出图组织建议

- 图号体系：`A-101（一层平面）/ A-201（南立面）/ A-301（剖面）/ S-501（楼梯大样）`
- 每图：图框（外框 A1/A2 + 图签栏：工程名/图名/图号/比例/日期/设计阶段）
- 比例：平面 1:100（小户型 1:50）、大样 1:20/1:50、总图 1:200/1:500
- 打印样式：CTB 按图层映射线宽，A-DIM/A-TEXT 细线，A-WALL 粗线

## 7. 落地路线（可选扩展）

1. `brief_to_cad.py --check` 已产出 violations.json（P0 闭环）；后续加 `--detail` 模式
   在 request.json 追加 2D 深化元素（云线/表格/标高符号/轴号），仍走「写入即兼容」策略；
2. 表格先以 text2d/polyline2d 组合生成图纸表格，复杂表格可在 DWG 外附 HTML/CSV 清单；
3. LISP 思路不要求在本项目实现；若用户有 AutoCAD 环境，可把 lib.lsp/revcloud.lsp 直接
   拷入插件侧作为 2D 深化辅助脚本。

---
*本文件为参考沉淀，非可执行规范。*
*（内容由AI生成，仅供参考）*
