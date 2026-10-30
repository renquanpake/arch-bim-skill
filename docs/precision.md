# 精度保障（Precision & Verification）

> 目标：不是"我觉得对"，而是每一步都有可复核的数字与阈值。

## 1. 三道闸门

| 闸门 | 落点 | 机制 | 阈值 |
|---|---|---|---|
| ① 单位归一 | `brief_to_cad.py` + `RevitAddin/Units.cs` | 全部转米；Revit 侧 `DetectScale`（max≤100 当米×1000），长度键含 center/x/y | 坐标舍入 1e-6 m |
| ② 发单前校验 | `preflight.py` | 退化几何/洞口越界/洞口互叠/共线墙重叠/z 越界/薄实体/未知类型/短线剔除；有 error 拦单 | cad 1e-6 m，revit 1.6 mm |
| ③ 发单后自证 | 插件 result.json | 四判据 `status/errors/entityCount/savedTo` + 体积 + byType + 包围盒 Z | 任一不符判失败 |

## 2. 三个回归工具（新）

### 2.1 `scripts/golden.py` — 几何黄金哈希
固定 3 个 fixture（intent/brief/elev-true）→ 只对 `elements` 做 SHA256。
任何几何漂移立刻红：`python scripts/golden.py`；有意修改后 `--update`。

### 2.2 `scripts/props.py` — 属性不变量（随机恶意输入）
N 个随机 brief（零长墙、极薄板、越界/重叠洞、负标高…），断言：
- P1 不崩溃
- P2 无 NaN/Inf，坐标 |v| < 1e6
- P3 所有尺寸 > 0（退化件被丢弃/修复）
- P4 洞口恒在墙内
- P5 修复后构件数不增加

当前：`--n 50` → 0 failed。

### 2.3 `scripts/reconcile.py` — 跨端体积对账
解析体积（Python）vs AutoCAD 实测（`体积合计`）vs Revit 实测（`volumeM3`）。

```powershell
python scripts/reconcile.py --request req.json --cad-result cad.json --revit-result rev.json
```

分端阈值：**CAD 1%**（Solid3d 真 mass properties，实测 Δ=0.00%）/ **Revit 5%**（体积是账面累加，非 mass properties）。

## 3. 实测基线（3 层样板，426 构件）

| 指标 | 值 |
|---|---|
| 解析体积 | 568.638 m³ |
| AutoCAD | 568.638 m³（Δ **+0.00%**） |
| Revit | 547.643 m³（Δ **-3.69%**） |
| 楼梯对账 | 16 个 box 解析 5.7375 m³ = 插件实测 5.7375 m³（差 0） |
| 确定性 | 同输入同路径两次 SHA256 完全相同 |
| 全局 AABB | X[-6,18] Y[-12,22] Z[-0.6,11.4]，0 负尺寸 |

### 对账立功记录
首轮对账发现 **Revit 体积 -19.9%**：Revit 把坡屋顶建成了平板（面积×厚度），CAD 是坡面棱柱。
修复：`ModelBuilder.CreateRoof` 改为 YZ 截面沿 X 拉伸，体积按截面 shoelace×长度计入。
修复后 -3.69%，且 Revit 3D 里屋顶形状与 CAD 一致。

## 4. 已知精度边界（不掩盖）

1. 立面 = 可见面线框，**无线段级消隐裁剪**
2. 楼梯 = 台阶 box 堆叠，非参数化楼梯对象
3. 门窗 = 洞口 + 薄板，非族实例
4. IFC 洞口有真布尔，但**无材质分层/厚度** → 只能估量
5. 机电 = 立管 + 直干管，无坡度/避让 → 长度准、拓扑不真
6. 房间面积为矩形 shoelace，非 Revit Room 边界
7. Revit 体积为账面累加（分端阈值 5% 即为此设）
