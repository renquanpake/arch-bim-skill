---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 047f4fed51e05cd4d8519ffeca080f15_9befa331d9f711f1a0ca525400de85a5
    ReservedCode1: GlhTLHNaQYc1BvKw26TyKN/jsw8LyC2MYBnFqMok9l34T3WAI/QbjrkDJPl4nTuoJ512UAHCzvs+ifLmfNyTAY68YHEYAb0ZSyJG9tj4PAyZP1G/FZnMA7Doa5RGW8liMUkrST30+jQ3HlZMvRWdEvorKJ2TiwKrW9/cvOT/340SlEZ7H2JkcNbsAaY=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 047f4fed51e05cd4d8519ffeca080f15_9befa331d9f711f1a0ca525400de85a5
    ReservedCode2: GlhTLHNaQYc1BvKw26TyKN/jsw8LyC2MYBnFqMok9l34T3WAI/QbjrkDJPl4nTuoJ512UAHCzvs+ifLmfNyTAY68YHEYAb0ZSyJG9tj4PAyZP1G/FZnMA7Doa5RGW8liMUkrST30+jQ3HlZMvRWdEvorKJ2TiwKrW9/cvOT/340SlEZ7H2JkcNbsAaY=
---

# Revit 无头启动铁律与 A02 实操 SOP

> 定位：**SKILL.md §1D 的展开细节**，与 `revit-precision-playbook.md`（建模精度）并列。
> 来源：A02 住宅楼 B1+1F 逆向建模实战（2026-11）。用户明确要求：**无头就是无头，不许把 Revit 窗口甩到用户桌面上、不许反复弹提醒**。
> 读本文的时机：任何一次要启动 Revit 之前。哪怕只是"开一下看看"。

---

## 0. 一句话铁律

**任务需要 Revit，就必然走 `headless\launch-revit-hidden.ps1`（私有桌面）；裸 `Start-Process Revit.exe` 一律禁止。**

---

## 1. 五条硬规则（每条都有现场判据）

| # | 规则 | 现场判据（不合格即停工整改） |
|---|---|---|
| R1 | **禁止裸启动** | 出现 `Start-Process "D:\Revit 2022\Revit.exe"` 即违规；必须 `.\launch-revit-hidden.ps1` |
| R2 | **dll 必须签名** | `Get-AuthenticodeSignature` 状态必须是 `Valid`，`NotSigned`/`UnknownError` 禁止启动 |
| R3 | **一个插件承载所有动作** | 不许每个新任务往 `Addins\2022\` 里塞新目录，动作走 request 的 `action` 字段分支 |
| R4 | **必须自动退出** | 插件执行完写 result → 发 `WM_SYSCOMMAND/SC_CLOSE`；残留进程由启动器兜底 kill |
| R5 | **改 json 即生效，不重启 Revit** | 同一 Revit 实例内，`Idling` 比对 `LastWriteTime` 换动作重跑；"要重启才能生效"属于设计缺陷 |

### 为什么 R1 是铁律（踩坑实录）

`Revit.exe` **没有真正的 headless 模式**——没有 `-nosplash` 之外的静默开关，`Start-Process -WindowStyle Hidden` 无效（splash 照弹）。A02 过程中几次直接用 `Start-Process` 带环境变量启动，结果是：

- Revit 主窗口、启动画面、许可提示全部出现在用户桌面，抢焦点；
- 每次重新编译 `RevitGrid.dll` 都触发一次「未签名加载项」TaskDialog，用户被迫手动点；
- 用户原话：「你后来直接给我打开 revit 了，总提醒我」——这不是抱怨，是流程设计错了。

唯一正确做法：`CreateDesktop` 建私有桌面 `MarvisHidden`，`CreateProcess` 的 `lpDesktop` 指向它。窗口存在于系统里但不在用户桌面，建模/保存/退出逻辑完全不受影响。

### R2 的根因：重编译 = 换签名 = 信任失效

未签名弹窗不是"偶尔出现"，是**每次重编 dll 必然出现**。当前机器实测状态（2026-11-06）：

| 插件 dll | 签名状态 | 结论 |
|---|---|---|
| `RevitGrid\RevitGrid.dll`（A02 主力） | **NotSigned** | 弹窗真凶，必须签 |
| `RevitAIModeling\*.dll` | **NotSigned** | 旧轮询插件，应禁用 |
| `RevitClean\*.dll` | **NotSigned** | 弹窗来源之一 |
| RevitAddin / Revit1F / RevitCadLink / RevitRebuild / RevitShot / RevitVerify / RevitViewFix | Valid | 已签 |

`Addins\2022\` 下现存 **12 个** 自建插件目录——这就是"总提醒"的累积成因。R3 的意义：动作收敛到一个插件，目录不再增殖。

签名 + 信任（证书 `CN=Marvis BIM`）：

```powershell
$dll  = "C:\ProgramData\Autodesk\Revit\Addins\2022\RevitGrid\RevitGrid.dll"
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -like '*CN=Marvis BIM*' }
Set-AuthenticodeSignature -FilePath $dll -Certificate $cert

# 机器级信任（一次性，之后同证书签的 dll 全部免弹窗）
$cer = "$env:TEMP\marvis-bim.cer"
Export-Certificate -Cert $cert -FilePath $cer | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\Root | Out-Null

# 校验：必须 Valid
Get-AuthenticodeSignature $dll | Select-Object Status
```

批量体检（一条命令看全部）：

```powershell
Get-ChildItem "C:\ProgramData\Autodesk\Revit\Addins\2022" -Directory | ForEach-Object {
  Get-ChildItem $_.FullName -Filter *.dll -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch '\.bak' } |
    ForEach-Object { [pscustomobject]@{ Plugin=$_.Directory.Name; Dll=$_.Name; Sig=(Get-AuthenticodeSignature $_.FullName).Status } }
} | Where-Object Sig -ne 'Valid' | Format-Table -AutoSize
```

> 输出非空 = 下次启动 Revit 必弹窗，先签名再干别的。

---

## 2. A02 无头闭环 SOP（环境变量版）

自研插件 `RevitGrid`（`MarvisGrid.App : IExternalApplication`）经**环境变量**驱动，不依赖任何界面操作。

### 2.1 协议

| 项 | 值 |
|---|---|
| 请求变量 | `MARVIS_GRID_REQ` → 请求 JSON 的绝对路径 |
| 结果变量 | `MARVIS_GRID_OUT` → 结果日志的绝对路径 |
| 触发时机 | `OnStartup` 订阅 `Idling`，首次空闲即执行 |
| 完成信号 | 结果文件末行 `DONE` |
| 部署位置 | `C:\ProgramData\Autodesk\Revit\Addins\2022\RevitGrid\RevitGrid.dll` |
| 清单 | `C:\ProgramData\Autodesk\Revit\Addins\2022\RevitGrid.addin`（`Type="Application"`, `FullClassName="MarvisGrid.App"`, `AddInId=B2C3D4E5-2222-4A03-9E02-000000000002`） |

请求 JSON 通用字段：

```json
{ "action": "grid", "vx": [0, 3600, 7200], "hy": [0, 4200, 8400], "bbox": [-2400, 41000, 800, 15800] }
```

### 2.2 action 分支清单（RevitGrid6 实测）

| action | 作用 | 关键入参 |
|---|---|---|
| `build`（缺省） | 通用建模分支 | elements 等 |
| `clean` | 清场（按命名前缀删构件） | 前缀 |
| `diag2` | 诊断：统计当前文档构件/标高/视图 | 无 |
| `floor` | 建楼板（含楼梯洞口内环） | `bbox` / elevation |
| `iso` | 出轴测视图并导出 PNG | `bbox` |
| `views` | 批量出三视图（平/立/剖） | `bbox` |
| `dump` | 全量转储构件清单到 out | 无 |
| `grid` | 建轴网（vx 竖轴 + hy 横轴） | `vx` / `hy` |
| `restoregrid` | 回退/重建旧轴网（先清符号族再按坐标重建） | `vx` / `hy` / `bbox` |
| `gridhead` | 轴号圈处理 | — |
| `gridbubble` | 轴号气泡处理 | — |

> 新增 action = 在插件里加 `if (action == "xxx")` 分支，**不新建插件工程**（R3）。

### 2.3 标准执行序列

```powershell
$task = "C:\...\<session>\temp"          # 请求/结果目录
$req  = Join-Path $task "grid_req.json"
$out  = Join-Path $task "grid_out.txt"
$rvt  = "D:\A02_BIM_归档\02_过程版本\v9img.rvt"

# 1) 写请求
'{"action":"grid","vx":[0,3600,7200],"hy":[0,4200,8400],"bbox":[-2400,41000,800,15800]}' |
  Set-Content $req -Encoding UTF8

# 2) 私有桌面启动（关键：不是 Start-Process）
.\headless\launch-revit-hidden.ps1 -Document $rvt -Request $req `
    -Result (Join-Path $task "result.json") `
    -Env @{ MARVIS_GRID_REQ = $req; MARVIS_GRID_OUT = $out } `
    -ResultSignal $out -TimeoutSec 600

# 3) 核验完成信号
Select-String -Path $out -Pattern 'DONE' | Select-Object -Last 1
```

> 注：`-Env` / `-ResultSignal` 为启动器扩展参数，见 §4。改造前的老脚本只认 `REVITJSON_*`，不支持 `MARVIS_GRID_*`——这正是当初只能裸启动、导致窗口弹到用户桌面的直接原因。

### 2.4 验收（没有这一步不算完成）

1. 结果文件末行含 `DONE`，且 `saved=OK`；
2. 导出 PNG（`view/`、`rs/`、`hd/` 前缀）存在且尺寸非 0；
3. 启动器日志含 `用户桌面可见窗口峰值=0`；
4. `Get-Process Revit` 无残留（或只剩用户自己开的那一个）。

---

## 3. 免重启改造（R5 落地）

当前 `RevitGrid6.cs` 是 **`OnStartup` → 首次 `Idling` 执行一次**，改 action 必须关掉 Revit 重启——这就是"总要重启"的根源。

目标形态：

```csharp
// OnStartup 里注册常驻 Idling 轮询，而不是一次性执行
a.Idling += (s, e) =>
{
    var reqPath = Environment.GetEnvironmentVariable("MARVIS_GRID_REQ");
    if (string.IsNullOrEmpty(reqPath) || !File.Exists(reqPath)) return;

    var stamp = File.GetLastWriteTimeUtc(reqPath);
    if (stamp == _lastStamp) return;      // 文件没变 → 不执行
    _lastStamp = stamp;                    // 变了 → 换动作重跑
    Execute(reqPath);
};
```

要点：

- `_lastStamp` 为 `static DateTime`，跨 Idling 保持；
- 执行体必须在 `Idling` 内、且每次开独立 `Transaction`（事件内改模型限制）；
- 不允许在 `DocumentOpened` 里改模型；
- 改 json 时用 `Set-Content` 重写（`LastWriteTime` 必变）；只改内容不改时间戳的方式（如 `[IO.File]::WriteAllText` 后立即 `SetLastWriteTime` 回旧值）会漏触发。

---

## 4. 启动器扩展：`-Env` 与 `-ResultSignal`

`headless\launch-revit-hidden.ps1` 的 `CreateProcess(..., lpEnvironment: IntPtr.Zero, ...)` 意味着**子进程继承父进程环境变量**，所以注入环境变量无需构造环境块，直接设 `$env:` 再启动即可。

新增两个参数：

| 参数 | 作用 |
|---|---|
| `-Env @{K=V}` | 启动前注入环境变量（结束后还原），用于 `MARVIS_GRID_*` 等自定义变量 |
| `-ResultSignal <path>` | 完成判据改为「该文件出现且末行含 DONE」，替代默认的 JSON `status` 判定 |

用途区分：

- `REVITJSON_*` 协议（RevitAddin 通用建模）→ 用默认 `-Request/-Result` JSON 判定；
- `MARVIS_GRID_*` 协议（RevitGrid 专项动作）→ 用 `-Env` + `-ResultSignal`。

---

## 5. 反模式清单（见到就停手）

| 反模式 | 后果 | 正确做法 |
|---|---|---|
| `Start-Process "D:\Revit 2022\Revit.exe" -ArgumentList $rvt` | 窗口弹到用户桌面、抢焦点 | `launch-revit-hidden.ps1` |
| `Start-Process -WindowStyle Hidden` | 无效，splash 照弹 | 同上 |
| 每次任务新建 `Addins\2022\RevitXXX\` | 目录增殖、弹窗叠加 | 复用 `RevitGrid`，加 action 分支 |
| 编译后直接跑，不重签 | 弹「未签名加载项」卡流程 | R2 签名 + 机器级信任 |
| 改 action 后关掉 Revit 手动重启 | 打断用户、耗时 3 分钟冷启动 | R5 Idling 比对时间戳 |
| 插件跑完不退出等用户关 | 进程残留、下次许可冲突 | SC_CLOSE + 启动器兜底 kill |
| 把"未签名弹窗我点了"当正常步骤 | 用户被打扰 | 签名根治，弹窗不是流程的一部分 |

---

## 6. 交付前 Runbook 自检

```
[ ] 1. 要启动 Revit？→ 是 → 只能 launch-revit-hidden.ps1
[ ] 2. 目标 dll 签名 Valid？→ 否 → 先签名，不启动
[ ] 3. Addins\2022 有无新增插件目录？→ 有 → 合并进 RevitGrid，删多余
[ ] 4. 请求走 action 分支还是新建工程？→ 新建 → 停手，改 action
[ ] 5. 跑完 out 有 DONE？→ 无 → 查 out 日志，不谎报完成
[ ] 6. 用户桌面可见窗口峰值 = 0？→ 非 0 → 查是否走了裸启动
[ ] 7. Revit 进程残留？→ 有 → SC_CLOSE / kill
```

七项全绿才算"无头交付"。任何一项红，不许对用户说"搞定了"。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
