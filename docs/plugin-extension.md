---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_f240b7e3d04f11f19fe7525400de85a5
    ReservedCode1: zR2VFztogAAtiuM7dL1fu50oZYxfnvDJgmareZFWde6nDT4oqgK03ut8RMOG43QsywrJHfSTStbHgudaN4co2B7b4EaUfBZQV/gwf3lkCxdmPMxsdILUJIA6clnzukcgW1UqTo7tg0GOAnKD8fon9LTwFbAQP1XuE+sleMIxqI9OY2flfcsEKVPQr8E=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_f240b7e3d04f11f19fe7525400de85a5
    ReservedCode2: zR2VFztogAAtiuM7dL1fu50oZYxfnvDJgmareZFWde6nDT4oqgK03ut8RMOG43QsywrJHfSTStbHgudaN4co2B7b4EaUfBZQV/gwf3lkCxdmPMxsdILUJIA6clnzukcgW1UqTo7tg0GOAnKD8fon9LTwFbAQP1XuE+sleMIxqI9OY2flfcsEKVPQr8E=
---

﻿# CadJsonPlugin 扩展方案（arch-bim-skill 目标能力）

> 本文档给出使现有插件完整支持 arch-bim-skill 所需的源码改动方案：**具体改动位置 + 新增逻辑 + 完整代码片段**。本次仅提供方案与代码，不要求编译。
>
> 现有能力判定（基于 CadJsonPlugin.cs 逐行审读）：
>
> | 目标能力 | 现状 | 结论 |
> |---|---|---|
> | 3D Solid3d 建模 | wall/column/beam/floor 均生成 Solid3d（CreateBox / Region.Extrude） | ✅ 已支持 |
> | 墙上门窗洞口布尔减法 | 无 openings 参数，无 BooleanSubtract 调用 | ❌ 需扩展 |
> | 屋顶 roof | 无 roof 类型分支 | ❌ 需扩展 |
> | 独立门/窗 door/window | 无类型分支（仅 instance 可放图块） | ❌ 需扩展 |
> | 2D 平面线框/标注 | 无 polyline/text 图元分支；SetEntityData 固定 Layer="0" | ❌ 需扩展 |
> | 图层分层 | 所有实体 Layer="0" | ⚠️ 需扩展 layer 参数 |

---

## 1. 改动总览

| # | 文件 | 位置 | 改动 |
|---|---|---|---|
| 1 | CadJsonPlugin.cs | `CreateElement` switch | 新增 `roof` / `door` / `window` / `polyline2d` / `text2d` 分支；`wall` 分支支持 `openings` 与 `elevation` |
| 2 | CadJsonPlugin.cs | 新增方法区（`CreateFloor` 之后） | `CreateWallWithOpenings`、`CreateRoof`、`CreateDoorWindow`、`CreatePolyline2d`、`CreateText2d`、`EnsureLayer` |
| 3 | CadJsonPlugin.cs | `SetEntityData` | 增加 `layer` 参数（缺省 "0"），并先 `EnsureLayer` |
| 4 | CadJsonPlugin.cs | `GetDouble` 附近 | 新增 `GetBool` 辅助方法（可选） |

---

## 2. 改动 1：CreateElement 类型映射扩展

**位置**：`CreateElement(Document doc, string type, JObject el, string name)` 的 switch。

**新增逻辑**：

```csharp
private void CreateElement(Document doc, string type, Newtonsoft.Json.Linq.JObject el, string name)
{
    switch (type)
    {
        case "wall":
        {
            double elevation = GetDouble(el, "elevation", 0.0);
            double[] basePt = GetPoint(el, "basePoint", new double[] { 0, 0, 0 });
            basePt[2] += elevation;   // elevation 快捷参数 → 合并到 Z 起点
            var openings = el["openings"] as Newtonsoft.Json.Linq.JArray;
            if (openings != null && openings.Count > 0)
                CreateWallWithOpenings(doc, name,
                    GetDouble(el, "length", 4.0), GetDouble(el, "thickness", 0.2),
                    GetDouble(el, "height", 3.0), basePt, GetDouble(el, "angle", 0),
                    openings, (string)el["layer"] ?? "0");
            else
                CreateBox(doc, name,
                    GetDouble(el, "length", 4.0), GetDouble(el, "thickness", 0.2),
                    GetDouble(el, "height", 3.0), basePt, GetDouble(el, "angle", 0),
                    (string)el["layer"] ?? "0");
            break;
        }
        case "column":
            CreateBox(doc, name,
                GetDouble(el, "width", 0.4), GetDouble(el, "depth", 0.4), GetDouble(el, "height", 3.0),
                GetPoint(el, "basePoint", new double[] { 0, 0, 0 }), GetDouble(el, "angle", 0),
                (string)el["layer"] ?? "0");
            break;
        case "beam":
            CreateBox(doc, name,
                GetDouble(el, "length", 6.0), GetDouble(el, "width", 0.3), GetDouble(el, "height", 0.5),
                GetPoint(el, "basePoint", new double[] { 0, 0, 0 }), GetDouble(el, "angle", 0),
                (string)el["layer"] ?? "0");
            break;
        case "floor":
            CreateFloor(doc, name, el);
            break;
        case "roof":
            CreateRoof(doc, name, el);
            break;
        case "door":
        case "window":
            CreateDoorWindow(doc, name, type, el);
            break;
        case "polyline2d":
            CreatePolyline2d(doc, name, el);
            break;
        case "text2d":
            CreateText2d(doc, name, el);
            break;
        case "instance":
            CreateInstance(doc, name, el);
            break;
        default:
            throw new SysException("未知类型 " + type);
    }
}
```

> 说明：`CreateBox` 签名增加 `string layer = "0"` 参数（见改动 3），所有调用点传入 `el["layer"]`；`CreateFloor` / `CreateInstance` 同样读取 `layer` 并在 `SetEntityData` 中使用。

---

## 3. 改动 2：新增建模方法

### 3.1 CreateWallWithOpenings — 墙体 Solid3d 拉伸 + 门窗洞布尔减法

**位置**：`CreateFloor` 之后。

**核心思路**：墙与洞口均在**局部坐标系**（原点 = 墙起点，X = 墙长方向，Y = 墙厚方向，Z = 向上）创建并完成减法，再整体旋转变换到世界坐标。**减法必须在未加入数据库的临时 Solid3d 上进行**，最后才 AppendEntity。

```csharp
// 带门窗洞墙体：布尔减法开洞
private void CreateWallWithOpenings(Document doc, string name,
    double lenX, double lenY, double lenZ, double[] basePt, double angle,
    Newtonsoft.Json.Linq.JArray openings, string layer)
{
    using (DocumentLock docLock = doc.LockDocument())
    using (Transaction tr = doc.Database.TransactionManager.StartTransaction())
    {
        BlockTable bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForWrite);
        BlockTableRecord btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

        // 1) 墙体实体（局部坐标：起点在原点，X 沿墙长）
        Solid3d wall = new Solid3d();
        wall.CreateBox(lenX, lenY, lenZ);

        // 2) 每个洞口 box 布尔减除（局部坐标）
        foreach (Newtonsoft.Json.Linq.JObject o in openings)
        {
            double sx   = (double)o["startX"];                       // 沿墙起点偏移（米）
            double w    = (double)o["width"];                        // 洞口宽（米）
            double h    = (double)o["height"];                       // 洞口高（米）
            double sill = o["sill"] != null ? (double)o["sill"] : 0.0; // 窗台高/门底高（米）

            Solid3d hole = new Solid3d();
            // 洞口必须穿透墙厚（两端各超出 0.01），避免布尔残留碎面
            hole.CreateBox(w, lenY + 0.02, h);
            hole.TransformBy(Matrix3d.Displacement(
                new Vector3d(sx + w / 2.0, 0, sill + h / 2.0)));
            wall.BooleanSubtract(hole);
            hole.Dispose();
        }

        // 3) 整体旋转变换 + 平移到 basePoint（减法完成后）
        if (Math.Abs(angle) > 1e-9)
            wall.TransformBy(Matrix3d.Rotation(angle, Vector3d.ZAxis, Point3d.Origin));
        wall.TransformBy(Matrix3d.Displacement(new Vector3d(basePt[0], basePt[1], basePt[2])));

        // 4) 入库
        SetEntityData(wall, name, layer);
        btr.AppendEntity(wall);
        tr.AddNewlyCreatedDBObject(wall, true);
        tr.Commit();
    }
}
```

**边界约束**：
- `startX + width` 不得超过墙长 `lenX`，否则布尔结果异常，应由上层（brief_to_cad.py）先做几何校验；
- 洞口高度 `sill + h` 不得超过墙高 `lenZ`；
- 建议调用 `wall.CheckInterference` 或至少记录日志便于排查。

### 3.2 CreateRoof — 屋顶（gable 双坡 / shed 单坡 / flat 平顶）

**位置**：`CreateWallWithOpenings` 之后。

**核心思路**：利用 `Solid3d.Extrude(Region, height)` 沿 Region 法线拉伸。将屋顶**截面**（等腰三角形 / 直角三角形 / 矩形）构造在 YZ 平面（x=0），法线沿 X 轴，Extrude 沿 X 即**沿屋顶长度方向**拉伸成三棱柱/薄板，天然表达坡面，无需放样 API。

```csharp
// 屋顶：双坡 gable / 单坡 shed / 平顶 flat
// 入参：outline（屋顶平面外包矩形轮廓，米）、thickness、height（坡顶高）、elevation、overhang
private void CreateRoof(Document doc, string name, Newtonsoft.Json.Linq.JObject el)
{
    string roofType = (string)el["roofType"] ?? "gable";
    var outline = el["outline"] as Newtonsoft.Json.Linq.JArray;
    if (outline == null || outline.Count < 3)
        throw new SysException("roof 缺少 outline 多边形");

    double thickness = GetDouble(el, "thickness", 0.12);
    double height    = GetDouble(el, "height", 1.5);
    double elevation = GetDouble(el, "elevation", 3.0);
    double overhang  = GetDouble(el, "overhang", 0.3);

    // 外包矩形（米）
    double minX = double.MaxValue, minY = double.MaxValue, maxX = double.MinValue, maxY = double.MinValue;
    foreach (Newtonsoft.Json.Linq.JArray p in outline)
    {
        minX = Math.Min(minX, (double)p[0]); minY = Math.Min(minY, (double)p[1]);
        maxX = Math.Max(maxX, (double)p[0]); maxY = Math.Max(maxY, (double)p[1]);
    }
    double length = (maxX - minX) + 2 * overhang;   // 沿 X（屋脊方向默认平行 X）
    double width  = (maxY - minY) + 2 * overhang;   // 坡向跨度

    using (DocumentLock docLock = doc.LockDocument())
    using (Transaction tr = doc.Database.TransactionManager.StartTransaction())
    {
        BlockTable bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForWrite);
        BlockTableRecord btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

        // 截面点列（YZ 平面，x=0；Z 从 0 到 height+thickness）
        List<Point3d> profile = new List<Point3d>();
        double baseZ = 0.0;
        if (roofType == "flat")
        {
            profile.Add(new Point3d(0, -width / 2.0, baseZ));
            profile.Add(new Point3d(0,  width / 2.0, baseZ));
            profile.Add(new Point3d(0,  width / 2.0, baseZ + thickness));
            profile.Add(new Point3d(0, -width / 2.0, baseZ + thickness));
        }
        else if (roofType == "shed")   // 单坡：直角三角形
        {
            profile.Add(new Point3d(0, -width / 2.0, baseZ));
            profile.Add(new Point3d(0,  width / 2.0, baseZ + height));
            profile.Add(new Point3d(0,  width / 2.0, baseZ + height + thickness));
            profile.Add(new Point3d(0, -width / 2.0, baseZ + thickness));
        }
        else                            // gable：双坡等腰三角形（顶上加板厚）
        {
            profile.Add(new Point3d(0, -width / 2.0, baseZ));
            profile.Add(new Point3d(0,  width / 2.0, baseZ));
            profile.Add(new Point3d(0,  width / 2.0, baseZ + thickness));
            profile.Add(new Point3d(0, 0, baseZ + height));
            profile.Add(new Point3d(0, -width / 2.0, baseZ + thickness));
        }

        // 构造截面曲线（闭合多段线）→ Region
        Polyline pl = new Polyline();
        for (int i = 0; i < profile.Count; i++)
            pl.AddVertexAt(i, new Point2d(profile[i].Y, profile[i].Z), 0, 0, 0);
        pl.Closed = true;

        using (DBObjectCollection curves = new DBObjectCollection())
        {
            curves.Add(pl);
            using (DBObjectCollection regions = Region.CreateFromCurves(curves))
            {
                if (regions.Count == 0)
                    throw new SysException("无法从屋顶截面创建区域");
                foreach (DBObject regObj in regions)
                {
                    if (regObj is Region)
                    {
                        Region region = (Region)regObj;
                        Solid3d solid = new Solid3d();
                        // 截面在 YZ 平面 → Region 法线沿 X → 沿屋顶长度拉伸
                        solid.Extrude(region, length, 0.0);
                        // 平移到外包矩形左下角，再抬升到 elevation
                        solid.TransformBy(Matrix3d.Displacement(
                            new Vector3d(minX - overhang, minY - overhang, elevation)));
                        SetEntityData(solid, name, (string)el["layer"] ?? "0");
                        btr.AppendEntity(solid);
                        tr.AddNewlyCreatedDBObject(solid, true);
                    }
                    regObj.Dispose();
                }
            }
        }
        tr.Commit();
    }
}
```

**说明**：
- 屋脊默认平行 X 轴（`ridgeStart/ridgeEnd` 可后续扩展为任意方向：按两点的 angle 整体旋转）；
- 双坡截面顶点 `(0,0,height)` 处板厚近似（工程简化），如需精确闭合坡板可把顶点下移 `thickness/2` 再补顶板；
- `Extrude(region, length)` 若法线为 -X 会反向，可在 Extrude 后统一 `TransformBy(Rotation(pi, ZAxis))` 校正，或按区域顶点顺序调整。

### 3.3 CreateDoorWindow — 独立门扇/窗扇薄板

**位置**：`CreateRoof` 之后。用于无墙可挂接时的退化表达（正常推荐走 wall.openings 布尔减法）。

```csharp
// 独立门/窗：薄板 Solid3d（门扇或窗扇），局部 X=宽、Y=厚、Z=高
private void CreateDoorWindow(Document doc, string name, string type, Newtonsoft.Json.Linq.JObject el)
{
    CreateBox(doc, name,
        GetDouble(el, "width", 0.9), GetDouble(el, "thickness", 0.05), GetDouble(el, "height", 2.1),
        GetPoint(el, "basePoint", new double[] { 0, 0, 0 }), GetDouble(el, "angle", 0),
        (string)el["layer"] ?? (type == "door" ? "A-DOOR" : "A-WINDOW"));
}
```

### 3.4 CreatePolyline2d / CreateText2d — 2D 平面图元

**位置**：`CreateDoorWindow` 之后。

```csharp
// 2D 平面线框（z=0 俯视）
private void CreatePolyline2d(Document doc, string name, Newtonsoft.Json.Linq.JObject el)
{
    var points = el["points"] as Newtonsoft.Json.Linq.JArray;
    if (points == null || points.Count < 2)
        throw new SysException("polyline2d 缺少 points");

    bool closed = el["closed"] != null && (bool)el["closed"];
    string layer = (string)el["layer"] ?? "0";

    using (DocumentLock docLock = doc.LockDocument())
    using (Transaction tr = doc.Database.TransactionManager.StartTransaction())
    {
        BlockTable bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForWrite);
        BlockTableRecord btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

        Polyline pl = new Polyline();
        for (int i = 0; i < points.Count; i++)
        {
            var p = (Newtonsoft.Json.Linq.JArray)points[i];
            pl.AddVertexAt(i, new Point2d((double)p[0], (double)p[1]), 0, 0, 0);
        }
        pl.Closed = closed;
        SetEntityData(pl, name, layer);
        btr.AppendEntity(pl);
        tr.AddNewlyCreatedDBObject(pl, true);
        tr.Commit();
    }
}

// 2D 标注文字（尺寸/房间名）
private void CreateText2d(Document doc, string name, Newtonsoft.Json.Linq.JObject el)
{
    string text = (string)el["text"];
    if (string.IsNullOrEmpty(text)) throw new SysException("text2d 缺少 text");
    double[] ins = GetPoint(el, "insertPoint", new double[] { 0, 0, 0 });
    double height = GetDouble(el, "height", 0.25);
    double rotation = GetDouble(el, "rotation", 0.0);
    string layer = (string)el["layer"] ?? "0";

    using (DocumentLock docLock = doc.LockDocument())
    using (Transaction tr = doc.Database.TransactionManager.StartTransaction())
    {
        BlockTable bt = (BlockTable)tr.GetObject(doc.Database.BlockTableId, OpenMode.ForWrite);
        BlockTableRecord btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

        DBText txt = new DBText();
        txt.TextString = text;
        txt.Position = new Point3d(ins[0], ins[1], ins[2]);
        txt.Height = height;
        txt.Rotation = rotation;
        SetEntityData(txt, name, layer);
        btr.AppendEntity(txt);
        tr.AddNewlyCreatedDBObject(txt, true);
        tr.Commit();
    }
}
```

### 3.5 EnsureLayer — 图层创建（幂等）

**位置**：`SetEntityData` 之前。

```csharp
// 确保图层存在（不存在则创建，颜色按名称后缀：WALL=7 白 / DOOR=1 红 / WINDOW=3 绿 / DIM=2 黄）
private void EnsureLayer(Transaction tr, Database db, string layerName)
{
    LayerTable lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
    if (lt.Has(layerName)) return;
    lt.UpgradeOpen();
    LayerTableRecord ltr = new LayerTableRecord();
    ltr.Name = layerName;
    if (layerName.StartsWith("A-DIM")) ltr.Color = Autodesk.AutoCAD.Colors.Color.FromColorIndex(ColorMethod.ByAci, 2);
    else if (layerName.StartsWith("A-DOOR")) ltr.Color = Autodesk.AutoCAD.Colors.Color.FromColorIndex(ColorMethod.ByAci, 1);
    else if (layerName.StartsWith("A-WINDOW")) ltr.Color = Autodesk.AutoCAD.Colors.Color.FromColorIndex(ColorMethod.ByAci, 3);
    else ltr.Color = Autodesk.AutoCAD.Colors.Color.FromColorIndex(ColorMethod.ByAci, 7);
    lt.Add(ltr);
    tr.AddNewlyCreatedDBObject(ltr, true);
}
```

---

## 4. 改动 3：SetEntityData 支持图层

**位置**：现有 `SetEntityData(Entity ent, string name)`。

```csharp
private void SetEntityData(Entity ent, string name, string layer = "0")
{
    if (!string.IsNullOrEmpty(layer) && layer != "0")
    {
        using (Transaction tr = ent.Database.TransactionManager.StartTransaction())
        {
            EnsureLayer(tr, ent.Database, layer);
            tr.Commit();
        }
    }
    ent.Layer = string.IsNullOrEmpty(layer) ? "0" : layer;
    if (!string.IsNullOrEmpty(name))
    {
        try { ent.SetXDataForEntity(name); }
        catch { }
    }
}
```

> 注意：`SetEntityData` 在事务内被调用（各 Create 方法已有事务），`EnsureLayer` 需用同一事务或独立短事务。推荐把 `EnsureLayer` 提到各 Create 方法主事务开头调用一次，`SetEntityData` 只负责 `ent.Layer = layer`，避免嵌套事务。实现时按主事务统一管理。

---

## 5. 编译与部署

```powershell
cd D:\cad\cad-ai-plugin\CadJsonPlugin
dotnet build -c Release
# 产物 bin\Release\CadJsonPlugin.dll → 复制到 D:\cad\AutoCAD 2021\（受信任路径）
```

部署后重启 acad.exe 使新 DLL 生效；插件仍注册表加载（HKLM\SOFTWARE\Autodesk\AutoCAD\R24.0\ACAD-4101:804\Applications\CadJsonPlugin）。

## 6. 扩展后验证用例

```json
{
  "elements": [
    {"type": "wall", "name": "W1", "length": 6.0, "thickness": 0.24, "height": 3.0,
     "basePoint": [0,0,0], "angle": 0,
     "openings": [
       {"type": "window", "startX": 1.2, "width": 1.2, "height": 1.5, "sill": 0.9},
       {"type": "door",   "startX": 4.2, "width": 0.9, "height": 2.1, "sill": 0.0}
     ], "layer": "A-WALL"},
    {"type": "roof", "name": "ROOF", "roofType": "gable", "thickness": 0.12, "height": 1.5,
     "elevation": 3.0, "overhang": 0.3,
     "outline": [[0,0],[6,0],[6,4],[0,4]], "layer": "A-ROOF"},
    {"type": "polyline2d", "name": "PLAN-W1", "points": [[0,0],[6,0]], "layer": "A-WALL"},
    {"type": "text2d", "name": "DIM1", "text": "6000", "insertPoint": [3,-0.9], "height": 0.25, "layer": "A-DIM"}
  ],
  "savePath": "D:\\models\\ext-demo.dwg"
}
```

预期：回执（默认轮询 `cad-result.json`；headless 驱动 `<请求基名>.result.json`）`status=ok`、`created` 含 4 项、`entityCount >= 4`；DWG 中 W1 为带窗洞+门洞的墙体 Solid3d，ROOF 为双坡三棱柱实体。

## 7. 边界与风险提示

1. **BooleanSubtract 稳定性**：洞口 box 必须穿透墙厚，且 `startX+width <= lenX`、`sill+height <= lenZ`；上层 brief_to_cad.py 已做换算，仍需在 CreateWallWithOpenings 内做防御校验（非法直接抛异常进 errors）。
2. **Extrude 反向**：YZ 平面 Region 法线可能为 ±X，拉伸后如方向相反需旋转校正（见 §3.2 注释）。
3. **事务嵌套**：`SetEntityData` 中避免开启新事务与主事务嵌套，图层创建统一在主事务内完成。
4. **旧版兼容**：未扩展插件遇到 `openings/roof/...` 未知键会忽略（不报错），请求仍可创建基础构件；扩展后能力完整。

## 8. 落地实现偏差说明（2026-10-25 编译部署实测）

本文档第 1-7 节为设计方案，实际实现有 4 处调整，均已编译进 `D:\cad\AutoCAD 2021\CadJsonPlugin.dll`：

| 项 | 方案写法 | 实际实现 | 原因 |
|---|---|---|---|
| 布尔减 | `solid.BooleanSubtract(hole)` | `solid.BooleanOperation(BooleanOperationType.BoolSubtract, hole)` | AutoCAD 2021 acdbmgd 的 Solid3d 无 BooleanSubtract 方法，枚举值为 BoolUnite/BoolIntersect/BoolSubtract |
| 屋顶挑檐 | 插件按 overhang 二次扩边 | 插件直接取 outline 包围盒，不再扩边 | brief_to_cad.py 已把挑檐烘进 outline，二次扩展会重复放大 |
| XData 构件标记 | 直接 `ent.XData = ...` | 先 `EnsureRegApp` 注册 CADJSON 再赋值 | 未注册的应用名写入 XData 会静默失败，导致清场失效 |
| 请求隔离 | 未定义 | 默认每次请求先 PurgePluginEntities（按 CADJSON XData 识别），`appendExisting:true` 可关闭 | 否则连续请求会累加实体，entityCount/体积不可比对 |

实现位置：`CadJsonPlugin\CadJsonExtensions.cs`（partial class，新增 CreateWallWithOpenings/CreateRoof/CreateDoorWindow/CreatePolyline2d/CreateText2d/EnsureLayer/EnsureRegApp/PurgePluginEntities/ComputeStats），`CadJsonPlugin\CadJsonPlugin.cs` 仅做 CreateElement 分发扩展与 floor/instance 的图层+XData 接入。

实测结论（examples/request.sample.json，32 构件）：`status=ok`、`errors=[]`、`entityCount=34`、`Solid3d=11 / Polyline=19 / DBText=4`、体积合计 `86.8968 m³`（与按 L·t·H−Σw·h·t 手算值完全一致，证明开洞与屋顶截面拉伸正确）、图层 `A-WALL/A-WINDOW/A-DOOR/A-ROOF/A-COLUMN/A-TEXT/A-DIM` 全部生成，DWG 落盘 `D:\models\demo-house.dwg`。连续两次相同请求 entityCount 稳定为 34，验证清场幂等。
*（内容由AI生成，仅供参考）*
