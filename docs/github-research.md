# GitHub 调研：Revit / BIM 自动化生态（2026-10-28）

## 结论
本地 Revit 自动化没有"拿来即用的完整建筑生成器"。成熟可借力的两条主线：
1. **pyRevit**（github.com/pyrevitlabs/pyRevit，1982★，GPL-3.0）
   - Revit 的 Python/RAD 宿主，自带命令面板；可写 pyRevit 脚本直接调 Revit API 出图/出表/建族。
   - 周边：EF-Tools(216★)、pyRevitMEP(78★)、revitron(80★，Python 封装)。
   - 价值：省去自研无头宿主与签名/安全框折腾；但仍是 GUI 进程，不能脱离 Revit。
2. **IfcOpenShell / BlenderBIM(Bonsai)**
   - IfcOpenShell：Python 直接读写 IFC4 全语义（墙层、洞口、空间、MEP、明细表），我们已集成 `ifc_export.py`。
   - BlenderBIM/Bonsai：开源 BIM 建模器，纯 headless 可脚本化，绕开 Revit 许可与 GUI。
   - 价值：真正"无头 + 标准语义"的正道；出图用 Bonsai 或 IFC→ 视图。

## 其它扫到的（参考价值低）
- RevitBatchProcessor(427★)、RevitHeadless(WCF+Idling，最小化常驻)、2D23D、massing(132★)、axis-model-generator。
- 都印证同一结论：本地 Revit "无头"= 最小化/私有桌面 GUI 进程 + API 驱动。

## 对我们管线（arch-bim-skill）的决策
| 能力 | 自研(现状) | 建议 |
|---|---|---|
| CAD 3D+2D | ✅ accoreconsole 真无头 | 保持，最强 |
| Revit 3D+平面DWG | ✅ 自研 addin + 私有桌面 | 保持；出图/建族可迁 pyRevit |
| IFC | ✅ ifc_export.py | 加深：墙层/洞口/明细表走 IfcOpenShell |
| 机电/结构计算 | ❌ | 别硬写；接 Bonsai 或专业求解 |

一句话：**CAD 自研继续，Revit 侧考虑挂 pyRevit，语义统一走 IFC(IfcOpenShell)。**
