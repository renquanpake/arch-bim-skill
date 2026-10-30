---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_14de58eed11211f182ed525400cd780f
    ReservedCode1: WZvugygWbHg8dPERf7Nc+PTb+VDqbPwBYrekURyUOX+Xeg08ZU0OkXqls5+2eLhc2Ordo9l2V7SyEZbL/oB8GXss9G0687cILEy67SexI0arH0QbLvMewcp3hEpEg4r+7ctI7qXvcjgcqjG2ASBnHnfunxvIBAXdAuZ6rNSRbJ3yGMder/gHPaIpqkU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_14de58eed11211f182ed525400cd780f
    ReservedCode2: WZvugygWbHg8dPERf7Nc+PTb+VDqbPwBYrekURyUOX+Xeg08ZU0OkXqls5+2eLhc2Ordo9l2V7SyEZbL/oB8GXss9G0687cILEy67SexI0arH0QbLvMewcp3hEpEg4r+7ctI7qXvcjgcqjG2ASBnHnfunxvIBAXdAuZ6rNSRbJ3yGMder/gHPaIpqkU=
---

# CAD 与 Revit 建模通法（跨平台方法论·完整版）

> 目的：掌握 AutoCAD 与 Revit 两大平台**任何建模任务**的通用方法，形成"输入建筑语义 → 平台落地 → 自证成功"的稳定套路。本文是 arch-bim-skill 插件扩展与日常建模任务的底层参考。
> 适用范围：建模创建、属性挂接、几何变换、几何读取、参数体系、文件格式、出图标注、无头驱动。

---

## 〇、通法总纲

**任何建模任务都回答 6 个问题**：
1. 目标构件是什么（墙/板/柱/梁/门窗/楼梯/标注/…）
2. 平台的"地基"在哪（CAD=块表+模型空间；Revit=标高+族体系）
3. 怎么创建（CAD=事务 AppendEntity；Revit=Transaction+New*/Create）
4. 挂什么属性（CAD=图层+线型+颜色+XData；Revit=类型/实例参数）
5. 放哪里（CAD=三维坐标；Revit=Level+插入点/位置线）
6. 怎么自证成功（CAD=实体计数；Revit=类别计数/参数/体积校验）

**共通底层心智**：`容器 → 创建对象 → 登记 → 提交事务`。区别只在容器、对象、事务写法。

---

## 一、AutoCAD 通法（几何引擎，无语义）

### 1.1 数据组织（四级链，记死）

```
Database（数据库）
├─ BlockTable（块表：名字 → 块表记录）
│  └─ BlockTableRecord（记录 = 模型空间/图纸空间/块定义）
│     └─ DBObject（实体：Line/Polyline/Region/Solid3d/DBText/BlockReference…）
├─ LayerTable（图层表）→ LayerTableRecord（图层，含名称/颜色/线型）
├─ LinetypeTable（线型表）→ LinetypeTableRecord
└─ DimStyleTable / TextStyleTable（标注/文字样式）
```

- 所有几何实体最终挂在某个 `BlockTableRecord` 上；**模型空间**=`bt[BlockTableRecord.ModelSpace]`。
- **块定义**是共享图元容器，用 `BlockReference` 引用（可带变换 Matrix3d）。

### 1.2 创建任何实体的固定套路（六步，全平台唯一入口）

```csharp
using (Transaction tr = db.TransactionManager.StartTransaction())
{
    BlockTable bt = tr.GetObject(db.BlockTableId, OpenMode.ForRead) as BlockTable;
    BlockTableRecord ms = tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite) as BlockTableRecord;
    using (DBObject obj = new Line(new Point3d(0,0,0), new Point3d(10,0,0)))
    {
        ms.AppendEntity(obj);            // ① 追加进记录
        tr.AddNewlyCreatedDBObject(obj, true); // ② 登记进事务
    }
    tr.Commit();                         // ③ 提交
}
```

**变体**：
- 改已有对象属性：`tr.GetObject(id, OpenMode.ForWrite)` → 设 `Layer`/`Color`/`Linetype` → `Commit`。
- 新建对象后要改：追加登记后仍持引用即可改（事务内）。
- 事务内对象必须 `Dispose`（`using`），否则报句柄冲突。

### 1.3 图层/线型/颜色管理（语义承载层）

```csharp
// 建图层：查 LayerTable → 无则 new LayerTableRecord → 设名/色/线型 → Add + 登记
LayerTable lyrTbl = tr.GetObject(db.LayerTableId, OpenMode.ForRead) as LayerTable;
if (!lyrTbl.Has("A-WALL"))
{
    tr.GetObject(db.LayerTableId, OpenMode.ForWrite);
    using (LayerTableRecord rec = new LayerTableRecord())
    {
        rec.Name = "A-WALL";
        rec.Color = Color.FromColorIndex(ColorMethod.ByAci, 7);  // ACI 索引色
        rec.LinetypeObjectId = lyrTbl["Continuous"];            // 线型引用
        lyrTbl.Add(rec);
        tr.AddNewlyCreatedDBObject(rec, true);
    }
}
// 给实体挂图层：obj.Layer = "A-WALL";  挂颜色：obj.Color = ...; 挂线型：obj.Linetype = ...;
```

**通法铁律**：CAD 没有"门/墙/柱"概念，一切语义靠 **图层名 + 块名 + XData 三件套** 表达。规划建模前先定图层字典（如 A-WALL/A-COLUMN/A-FLOR/A-ROOF/A-DOOR/A-WIND）。

### 1.4 几何创建与布尔运算（复杂实体成型）

**Region（2D 闭合轮廓，布尔的主力）**：
```csharp
// 闭合 Polyline → Region
DBObjectCollection curves = new DBObjectCollection();
curves.Add(poly); // 闭合多段线
DBObjectCollection regions = Region.CreateFromCurves(curves);
Region reg = regions[0] as Region;
```

**Region/Solid3d 布尔运算（三剑客）**：
```csharp
// 从一个实体调用，参数是被减/被并/被交的对象
outerReg.BooleanOperation(innerReg, BooleanOperationType.BoolSubtract); // 减=开洞
a.BooleanOperation(b, BooleanOperationType.BoolUnite);   // 并
a.BooleanOperation(b, BooleanOperationType.BoolIntersect); // 交
```
- 孔洞：圆柱 Solid3d 从墙中 `BoolSubtract` 减去。
- 复合轮廓（板留洞口）：外轮廓 Region 减内轮廓 Region → 再 Extrude。

**Solid3d 拉伸**：`Solid3d.Extrude(region, height, taperAngle)`；复杂可用 `ExtrudeAlongPath`/`Revolve`/`Sweep`。
**3D 实体图元**：可直接用命令对应实体（Box/Cylinder 类），或 `POLYSOLID` 式（高度+宽度+对正）快速生成墙形实体。

### 1.5 对象变换（移动/旋转/缩放/镜像/复制）

```csharp
// Matrix3d 4x4：前三列=旋转+缩放，第四列=平移
acObj.TransformBy(Matrix3d.Rotation(0.7854, curUCS.Zaxis, new Point3d(4,4.25,0))); // 绕 Z 转 45°
// 复制后变换：
DBObject copy = acObj.GetTransformedCopy(Matrix3d.Displacement(new Vector3d(10,0,0)));
```
- 常用矩阵：`Matrix3d.Rotation(angle, axis, basePoint)`、`Matrix3d.Displacement(vec)`、`Matrix3d.Scaling(scale, basePoint)`、`Matrix3d.Mirroring(plane)`。
- 世界/用户坐标：`Matrix3d.Identity`、`ed.CurrentUserCoordinateSystem`。

### 1.6 扩展数据 XData（语义标签）

- `obj.XData = new ResultBuffer(new TypedValue[] { new TypedValue((int)DxfCode.ExtendedDataRegAppName, "CADJSON"), ... })`。
- **应用名必须先注册**：`RegAppTable` 里 Add 登记，否则写入静默失败（EnsureRegApp 已实现）。
- 读取：`obj.GetXDataForApplication("CADJSON")`。

### 1.7 DXF 文件格式（外部交换/手工生成）

```
0        ← 实体类型标记（LINE/CIRCLE/ARC/TEXT/LWPOLYLINE…）
5        ← 句柄
100      ← AcDbEntity
8        ← 图层名
100      ← AcDbLine（子类标记，决定后面组码含义）
10/20/30 ← 起点 X/Y/Z
11/21/31 ← 终点 X/Y/Z
```
- **组码驱动**：DXF 是"组码(整数)+值"交替序列；`0`=实体类型、`8`=图层、`10/20/30`=坐标、`40`=半径/字高、`50/51`=起止角(度)、`62`=颜色、`70`=标志位（1=闭合）。
- LWPOLYLINE：`10/20` 逐顶点 + `40/41` 可选宽度 + `70` 闭合标志。
- 手工构造 DXF R12 最稳（simple_floor_plan.dxf 已验证）；中文用 `$DWGCODEPAGE` + ANSI 兼容编码。

### 1.8 文字与尺寸标注（2D 出图基础）

**DBText（单行文字）**：
```csharp
using (DBText txt = new DBText())
{
    txt.SetDatabaseDefaults(db);
    txt.TextString = "客厅";
    txt.Position = new Point3d(x, y, 0);      // 插入点
    txt.Height = 0.25;                        // 字高（模型单位，1:100 图纸 2.5mm → 0.25m）
    txt.Rotation = 0;                         // 旋转角（弧度）
    txt.WidthFactor = 0.8;                    // 宽度因子（长仿宋压缩）
    txt.HorizontalMode = TextHorizontalMode.Center; // 对齐
    txt.Layer = "A-TEXT";
    ms.AppendEntity(txt);
    tr.AddNewlyCreatedDBObject(txt, true);
}
```
**MText（多行文字）**：`MText.SetContents("{\\fSimSun|b0|i0|c134;\\H0.25x;}多行…")` 可带格式代码；`MText.Location` 设插入点、`MText.Width` 设行宽。

**AlignedDimension（对齐尺寸标注，无角度依赖，最常用）**：
```csharp
using (AlignedDimension dim = new AlignedDimension())
{
    dim.SetDatabaseDefaults(db);
    dim.XLine1Point = new Point3d(x1, y1, 0);   // 第一延伸线原点
    dim.XLine2Point = new Point3d(x2, y2, 0);   // 第二延伸线原点
    dim.DimLinePoint = new Point3d(xm, ym, 0);  // 尺寸线经过点（决定标注位置与方向）
    dim.DimensionStyle = db.DimStyleTable["A-DIM-STYLE"]; // 可挂标注样式
    dim.Layer = "A-DIM";
    ms.AppendEntity(dim);
    tr.AddNewlyCreatedDBObject(dim, true);
}
```
**常用标注类**：`AlignedDimension`（对齐，两点式）、`RotatedDimension`（旋转标注）、`LinearDimension`（线性）、`RadialDimension`/`DiametricDimension`（半径/直径）、`AngularDimension`（角度）；统一原则 = 定两个点 + 尺寸线经过点 + 样式/图层。
- 文字样式先注册 `TextStyleTable`（字体名/高度/宽度因子），标注样式先注册 `DimStyleTable`（箭头/文字高度/精度），再引用 ID。

### 1.9 无头与命令（已有实践）

- 无头：`accoreconsole.exe /i <seed.dwg> /s run.scr`，`.scr`=`NETLOAD 插件` + `命令`。
- 插件入口：`[CommandMethod("CADJSON_RUN")]`，轮询 `request.json` 原子消费。
- 每构件独立小事务：单构件失败不影响其他构件落库。

---

## 二、Revit 通法（构件引擎，有语义）

### 2.1 数据组织（一条链，记死）

```
Document（文档）
├─ Level（标高：定义"层"）
├─ Category（类别：墙/门/结构柱/楼板/标注…）
├─ Family（族）→ FamilySymbol（族类型）→ FamilyInstance（族实例）
│     - 系统族：墙/楼板/屋顶/标高/轴网（自带，不可载入文件）
│     - 可载入族：门/窗/柱/梁/家具（LoadFamily 载入）
│     - 内建族：In-Place（现场建模）
└─ Element（一切皆元素：实例/类型/数据，可被 FilteredElementCollector 过滤）
```

**核心心智**：Revit 造物本质 = **先有类型(Symbol)，后有实例(Instance)**。类型没有就 `LoadFamily`，没激活就 `Activate()`。

### 2.2 创建任何元素的固定套路（六步）

```csharp
// 1. 找类型（族类型）
FamilySymbol sym = new FilteredElementCollector(doc)
    .OfClass(typeof(FamilySymbol))
    .FirstOrDefault(e => e.Name == "类型名") as FamilySymbol;
if (sym == null) { doc.LoadFamily(族文件路径, out Family fam); /* 再查 */ }
if (!sym.IsActive) { sym.Activate(); doc.Regenerate(); }
// 2. 开事务
using (Transaction tr = new Transaction(doc, "创建构件"))
{
    tr.Start();
    // 3. 按放置方式选重载（见 2.3）
    FamilyInstance inst = doc.Create.NewFamilyInstance(pt, sym, level, StructuralType.Column);
    // 4. 设实例参数（柱的底/顶标高）
    inst.get_Parameter(BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).Set(topLevelId);
    // 5. 提交
    tr.Commit();
}
```

### 2.3 NewFamilyInstance 重载矩阵（按"放置方式"选）

| 放置方式 | 重载 | 适用 |
|---|---|---|
| 点 + 标高 | `(XYZ, symbol, level, structuralType)` | 柱、独立构件、家具 |
| 点 + 宿主 | `(XYZ, symbol, host, level, structuralType)` | 门/窗（host=墙）、挂墙构件 |
| 位置线 | `(Curve, symbol, level, structuralType)` | 梁、桁架（沿轴线） |
| 面上的点 | `(Reference, XYZ, XYZ, symbol)` | 面族、基于面的构件 |
| 直线面 | `(Line, symbol, view)` | 基于线的注释族 |

**StructuralType 五种**：Column / Beam / Brace / NonStructural / Footing。柱梁必须用带 Level 的重载。

### 2.4 系统族创建（不经 FamilySymbol）

| 构件 | API | 关键参数 |
|---|---|---|
| 墙 | `Wall.Create(doc, curve, wallTypeId, levelId, structural)` | 顶约束 `WALL_HEIGHT_TYPE` 指向上一层；`WALL_BASE_CONSTRAINT` |
| 楼板 | `Floor.Create(doc, IList<CurveLoop>, floorTypeId, levelId)` | 轮廓 Z=该层标高，否则 Height Offset 非 0；补救 `FLOOR_HEIGHTABOVELEVEL_PARAM`=0 |
| 楼梯 | `StairsEditScope` + `StairsRun.CreateStraightRun` | 底/顶标高；`CreateSketchedRun`/`CreateSpiralRun`；平台 `StairsLanding.CreateSketchedLanding` |
| 标高 | `Level.Create(doc, elevation)` | 每层一个 |
| 屋顶 | 迹线屋顶 `Roof.Create(doc, RoofType, Level, CurveLoop, ...)` | 轮廓闭合 + 坡角 |
| 轴网 | `Grid.Create(doc, curve)` | 定位线 |

### 2.5 参数体系（属性通法，最核心）

**内置参数 BuiltInParameter**（枚举，最常用）：
- 柱：`FAMILY_BASE_LEVEL_PARAM` / `FAMILY_TOP_LEVEL_PARAM` / `FAMILY_BASE_LEVEL_OFFSET_PARAM`
- 墙：`WALL_BASE_CONSTRAINT` / `WALL_HEIGHT_TYPE` / `WALL_BASE_OFFSET`
- 板：`FLOOR_HEIGHTABOVELEVEL_PARAM`
- 标高：`LEVEL_IS_BUILDING_STORY`

**参数类型**：Text/Integer/Number/Length/Area/Volume/Angle/URL/Material/YesNo/... 2022 后旧枚举标记过时，新写法用 `ForgeTypeId`（`SpecTypeId.Length` 等）。

**类型 vs 实例**：
- 类型参数：`sym.get_Parameter(名/枚举).Set(值)`，改全项目同类。
- 实例参数：`inst.get_Parameter(...)`，只改单个；角度一律弧度。
- 单位陷阱：Revit 内部一律英尺（feet），输入米必须 `UnitUtils.ConvertToInternalUnits(m, DisplayUnitType.DUT_METERS)`；CAD 保持绘图单位。

**在族里建参数**（FamilyManager，仅族文档）：
```csharp
FamilyManager mgr = doc.FamilyManager;
FamilyParameter fp = mgr.AddParameter("高度", SpecTypeId.Length, category, isInstance: true);
mgr.Set(fp, 3.0); // 设值（英尺）
```

**共享参数**（跨项目/族共享）：
- 先 `app.OpenSharedParameterFile(path)` 打开 .txt 定义文件 → `group.Define(definition, category, isInstance)` → 绑定 `BindingMap` 到文档。

### 2.6 元素变换（移动/复制/旋转/镜像）

```csharp
ElementTransformUtils.MoveElement(doc, elemId, new XYZ(10, 0, 0));          // 平移
ElementTransformUtils.CopyElement(doc, elemId, new XYZ(0, 10, 0));          // 复制一份
ElementTransformUtils.RotateElement(doc, elemId, Line.CreateUnbound(pt, XYZ.BasisZ), Math.PI/2); // 绕轴转（弧度）
ElementTransformUtils.MirrorElement(doc, elemId, Plane.CreateByNormalAndOrigin(XYZ.BasisX, XYZ.Zero)); // 镜像
// 批量：MoveElements(doc, ICollection<ElementId>, XYZ)
```
- 旋转轴：`Line.CreateUnbound(基点, 轴向)`；角度=弧度。
- 变换对象变换元素：`elem.Location`（LocationPoint/ LocationCurve）也可直接改坐标/曲线。

### 2.7 几何读取与分析（自证/碰撞/提量）

```csharp
Options opt = new Options { ComputeReferences = true, DetailLevel = ViewDetailLevel.Fine };
GeometryElement geo = elem.get_Geometry(opt);
foreach (GeometryObject obj in geo)
{
    if (obj is Solid s)        // 实体：体积 s.Volume，面/边遍历
    {
        foreach (Face f in s.Faces) { /* 面积 f.Area，法向 f.ComputeNormal */ }
        foreach (Edge e in s.Edges) { /* 边曲线 e.AsCurve() */ }
    }
    else if (obj is GeometryInstance gi)
    {
        // 族实例几何：GetSymbolGeometry()=族定义几何(未变换)；GetInstanceGeometry()=实例几何(已变换)
        GeometryElement instGeo = gi.GetInstanceGeometry();
    }
}
```
- 分析墙体量：遍历 Solid → 算体积/面积。
- 碰撞/重叠：几何交集可辅助判断（或 `SolidUtils`/`BooleanOperationsUtils`）。

### 2.8 文字、标注、视图与图纸（出图基础）

**TextNote（文字注释，仅存在于视图）**：
```csharp
// 先找注释族类型（TextNoteType），否则创建/复制
TextNoteType tnt = new FilteredElementCollector(doc)
    .OfClass(typeof(TextNoteType)).FirstOrDefault() as TextNoteType;
TextNote note = TextNote.Create(doc, viewId, new XYZ(x, y, z), "客厅", tnt.Id);
// 后改属性：note.Text = ...; note.Height = ...;
```
- 关键：`TextNote.Create(Document, ElementId viewId, XYZ position, string text, ElementId typeId)` 静态方法；**必须挂在某个视图上**（平面/剖面），不在模型空间。
- 视图专属：新建文字不会出现在所有视图，只落在传入 viewId 的视图；改 `note.get_Parameter(TextNoteType属性)` 设字高/对齐。

**Dimension（尺寸标注）**：
```csharp
// 选取参照线（用 ReferenceArray 包住要标注的线/边界）
ReferenceArray refs = new ReferenceArray();
refs.Append(new Reference(line));                       // 模型线/墙体边
using (Transaction tr = new Transaction(doc, "标注"))
{
    tr.Start();
    Dimension dim = doc.Create.NewDimension(view, line, refs); // 沿线的尺寸
    tr.Commit();
}
// 或两点式：NewDimension(view, Line.CreateBound(pt1, pt2), refs)
```
- 参照对象：`new Reference(element)` / 面 `face.Reference` / 线 `curve.Reference`；尺寸线与被标注对象必须共面于视图。
- 尺寸样式：`DimensionType`（对齐/线性/角度/弧长），找 `DimensionType` 后 `dim.DimensionType = type`。

**ViewPlan（平面视图，出图容器）**：
```csharp
// 创建平面视图：按标高创建
ViewPlan plan = ViewPlan.Create(doc, viewFamilyTypeId, levelId);
plan.Name = "一层平面";  // 改名需事务
// 常用视图族类型：ViewFamilyType 过滤 ViewFamily.Plan / Section / Sheet
ViewFamilyType vft = new FilteredElementCollector(doc)
    .OfClass(typeof(ViewFamilyType))
    .Cast<ViewFamilyType>()
    .First(v => v.ViewFamily == ViewFamily.FloorPlan);
```
**Section（剖面）**：`ViewSection.CreateSection(doc, viewFamilyTypeId, sectionBox)`，`BoundingBoxXYZ` 定剖切范围与方向。
**Sheet（图纸）**：`ViewSheet.Create(doc, titleBlockTypeId)`；`Viewport.Create(doc, sheetId, viewId, center)` 把视图拖进图纸；图框用 TitleBlock 族类型。

**出图通法**：模型建好 → 建平面/剖面视图 → 视图内放文字/尺寸标注 → 建图纸 Sheet → Viewport 排版 → 导出。所有注释元素一律挂视图，不挂模型。

### 2.9 无头与事件（已有实践）

- Revit 官方无 Core Console，纯 API 无头 = `Revit.exe <rvt>` 启动 + `.addin` 插件自动加载 + **Application.DocumentOpened 事件**检测请求文件 → 建模 → 保存 → 写 result.json → `SC_CLOSE` 退出。
- 不用 journal（非官方、跨版本不稳）；不用 BatchRvt（绑定 Dynamo 脚本，不适合自有 JSON 协议）。
- Idling 事件做延时建模（等文档完全就绪）；请求不存在则不动作，不干扰手工使用。

---

## 三、跨平台映射规律（把同一需求做成两套）

| 抽象层 | CAD 落地 | Revit 落地 |
|---|---|---|
| 坐标系统 | 直接三维坐标（绘图单位） | XYZ（内部英尺）+ Level 标高体系 |
| 构件语义 | 图元 + 图层/线型/颜色/XData 约定 | 类别/族/参数（原生语义） |
| 创建入口 | `AppendEntity + AddNewlyCreatedDBObject` | `doc.Create.NewFamilyInstance / Wall.Create / Floor.Create` |
| 事务 | `TransactionManager.StartTransaction` | `Transaction(doc)` |
| 属性 | 图层名、颜色、线型、XData | BuiltInParameter + 族参数 + 共享参数 |
| 类型复用 | 块定义（BlockReference 引用） | 族类型（FamilySymbol） |
| 变换 | `Matrix3d.TransformBy` | `ElementTransformUtils.*` |
| 几何读取 | 直接遍历 DBObject 图元 | `get_Geometry` → Solid/Face/Edge/GeometryInstance |
| 文字标注 | DBText/MText + AlignedDimension（挂模型空间） | TextNote + Dimension（挂视图） |
| 出图容器 | 图纸空间 + 视口 | ViewPlan/Section → Sheet + Viewport |
| 无头驱动 | accoreconsole + .scr（NETLOAD+命令） | Revit.exe + DocumentOpened/Idling 事件 |
| 自证 | 事务后遍历 ModelSpace 计数 entityCount | FilteredElementCollector 按类别计数 + 参数/体积校验 |

**同一份 request.json 翻译原则**：
- 墙/柱/梁：CAD=矩形截面 Solid3d 拉伸 + 图层；Revit=点/线 + FamilySymbol + Level。
- 楼板/屋顶：CAD=闭合 Polyline→Region→Extrude（Z=标高）；Revit=CurveLoop + FloorType + Level（Z=标高）。
- 门窗：CAD=块引用占位；Revit=host 重载挂墙。
- 楼梯：CAD=Polyline 轮廓近似（或台阶体块数组）；Revit=StairsEditScope 真楼梯（自动踢面+扶手）。
- 文字/尺寸：CAD=DBText + AlignedDimension 直接挂模型空间；Revit=TextNote + NewDimension 挂指定视图。

---

## 四、自证成功（两平台通用验收清单）

1. 计数：CAD 实体数 / Revit 类别实例数，与预期一致（result.json 的 entityCount）。
2. 归属：CAD 图层名正确；Revit 各构件 Level/参数正确（柱底顶标高、墙顶约束）。
3. 几何：包围盒尺寸符合输入（长宽高/层高）；Region/Solid 布尔后无碎屑。
4. 体积/面积：API 计算值与设计值误差在容差内（体积核对已跑通：86.9 / 191.75 / 178.25 m³）。
5. 无头模式：result.json 回写 status/created/errors/savedTo，断言成功。
6. 幂等：重复请求不重复建（CAD 用块名/图层查重；Revit 用名字/位置查重）。
7. 出图：文字/标注挂对视图、图纸 Sheet 含 Viewport；TextNote 在指定视图可见。

---

## 五、参考来源

- AutoCAD .NET 开发者文档：事务与对象创建、图层表、转换对象（TransformBy/GetTransformedCopy）、复合区域布尔、DBText/MText/AlignedDimension
- CAD 开发者社区（cadn.net.cn）：创建 Line/图层/旋转对象实战示例
- Autodesk 官方 OARX DevGuide：Create Composite Regions / Solid3d 拉伸
- DXF 标准详解（takuto-na.github.io）：实体结构、组码语义
- Revit API Docs（rvtdocs/revapidocs）：NewFamilyInstance 重载、ParameterType/ForgeTypeId、FamilyManager.AddParameter、ElementTransformUtils、GeometryInstance、TextNote.Create、NewDimension、ViewPlan/ViewSection/ViewSheet/Viewport
- Building Coder（Jeremy Tammik）：Wall.Create / Floor.Create / 楼板 Z 陷阱
- Infralligence：Inserting Families（族载入→符号→实例全流程）
- 已有 arch-bim-skill 实践：CADJSON 插件 + Revit Addin 双无头闭环、体积自证
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
