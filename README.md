---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_f0f43299d04f11f19fe7525400de85a5
    ReservedCode1: mhwqA1s8MT2Y2zsN5peKnSNe+XxSKGwlVl7zm66yJaoyh0qTSxVFS3AfZo7mDQnQfzabDTIGIoiYIXsin0bknK0eZsceTDZ9MwBJC0fzwnWSsnXsKy9pmWqep4uCqW+oROPfnvr05eTjrXtoGa2i0V/3jq5HjXU1omnj5DtoGlH3xRxHupPpNq+Vb9I=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_f0f43299d04f11f19fe7525400de85a5
    ReservedCode2: mhwqA1s8MT2Y2zsN5peKnSNe+XxSKGwlVl7zm66yJaoyh0qTSxVFS3AfZo7mDQnQfzabDTIGIoiYIXsin0bknK0eZsceTDZ9MwBJC0fzwnWSsnXsKy9pmWqep4uCqW+oROPfnvr05eTjrXtoGa2i0V/3jq5HjXU1omnj5DtoGlH3xRxHupPpNq+Vb9I=
---

# arch-bim-skill — 建筑协同建模技能

口述建筑概念 → 2D 平面图 + 3D 模型 DWG，基于本地 AutoCAD JSON 建模插件链路。

## 快速上手

```powershell
cd D:\skills\arch-bim-skill

# 0. 环境自检（外部路径集中在 config.json，缺失先改配置）
python brief_to_cad.py --env-check
python scripts\smoke.py

# 1. 编写/修改结构化建筑描述（默认 mm）
#    examples\brief.sample.json 为示例

# 2. 转换为 CAD JSON 指令（含 3D + 2D 平面）
python brief_to_cad.py --brief examples\brief.sample.json `
                       --dwg D:\models\demo-house.dwg --plan
# 未传 --request 时写入 config.json 的 paths.requestDir

# 3. 启动 AutoCAD（插件 1s 轮询请求文件）
Start-Process 'D:\cad\AutoCAD 2021\acad.exe' -ArgumentList '/nologo'

# 3.5 导出 IFC（可选，标准语义）
python ifc_export.py <request.json> D:\models\building.ifc

# 4. 读取验证回执（默认轮询 <requestDir>\cad-result.json；headless 驱动为 <请求基名>.result.json）
#    status=ok、errors=[]、entityCount>0、savedTo 非空
```

## 文档导航

| 文档 | 内容 |
|---|---|
| SKILL.md | 技能总览：管线架构、构件映射表、3D/2D 建模策略、验证闭环 |
| config.json / archbim_config.py | 外部路径集中配置（acad/accoreconsole/cadJsonDll/revit/requestDir/dwgOutputDir），可用 ARCHBIM_* 环境变量覆盖 |
| docs/protocol.md | CAD JSON 协议：请求/响应字段、构件类型表、单位约定、示例 |
| docs/plugin-extension.md | 插件扩展方案：roof/door/window、门窗洞布尔减法、2D 图元、图层（代码改动位置 + 完整代码） |
| examples/brief.sample.json | 示例建筑描述 |
| examples/request.sample.json | 转换器输出的示例 request（运行 brief_to_cad.py 生成） |

## 当前能力矩阵

| 能力 | 转换器（brief_to_cad.py） | 插件（CadJsonPlugin） |
|---|---|---|
| wall / column / beam / floor | ✅ 生成 | ✅ 原生 Solid3d |
| 门窗洞布尔减法 | ✅ 写入 wall.openings | ✅ 已编译（docs/plugin-extension.md §3.1） |
| roof（gable/shed/flat） | ✅ 生成 roof 元素 | ✅ 已编译（§3.2） |
| door / window 独立薄板 | ✅ 生成 | ✅ 已编译（§3.3） |
| 2D 平面线框 + 标注（--plan） | ✅ 生成 polyline2d/text2d | ✅ 已编译（§3.4） |
| 图层分层 | ✅ 写入 layer | ✅ 已编译（§3.5/§4） |
| 逐层墙体（每层独立标高） | ✅ 每层 4 外墙 + 全房间隔墙 | ✅ 墙按 basePoint.z 抬标高 |
| 楼梯间楼板开洞 | ✅ 楼板按梯段拆块 | ✅ 实体无洞即开洞 |
| 窗下墙 | — | ✅ 洞口下方补墙，窗不再通到地面 |
| 结构（基础/柱/梁） | ✅ 逐层柱网 + 双向主梁 + 独基/地梁 | ✅ 原生柱/梁 + box 基础 |
| 外围护（女儿墙/雨篷/台阶/散水） | ✅ | ✅ box |
| 门窗扇 | ✅ 按洞口生成带角度薄板 | ✅ box 支持 angle |
| 电梯井/轿厢、机电立管、洁具、阳台 | ✅ 体量 | ✅ box |
| 完整清单与缺口 | 见 `docs/complete-building.md` | — |

> 扩展已编译进 `D:\cad\AutoCAD 2021\CadJsonPlugin.dll`（CadJsonExtensions.cs），扩展字段由插件直接消费。
*（内容由AI生成，仅供参考）*
