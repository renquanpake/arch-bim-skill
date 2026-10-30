<#
  run-headless-all.ps1 — 双无头统一驱动入口（CAD accoreconsole 真无头 + Revit 无头）
  =====================================================================
  用途：
    按任务列表批量执行"无头建模"：
      - CAD 侧：循环调用 D:\cad\AutoCAD 2021\accoreconsole.exe /i <dwg> /s run.scr，
        由环境变量 CADJSON_REQUEST / CADJSON_RESULT / CADJSON_SAVE 传参，
        跑完收集 result.json；
      - Revit 侧：逐 RVT 启动 D:\Revit 2022\Revit.exe <rvt>，
        由环境变量 REVITJSON_REQUEST / REVITJSON_RESULT / REVITJSON_SAVE 传参，
        DocumentOpened+Idling 自动触发建模，脚本轮询 result.json 或超时。
    每任务写日志；单项失败不中断，配合 -Resume 可失败续跑。

  环境变量（插件消费，本脚本负责设置）：
    CADJSON_REQUEST / CADJSON_RESULT / CADJSON_SAVE
    REVITJSON_REQUEST / REVITJSON_RESULT / REVITJSON_SAVE

  参数：
    -Mode             cad | revit | all（默认 all）
    # ---- CAD 任务来源（可同时给，自动合并去重）----
    -CadRequest       <请求json>，字符串数组，可多次传
    -CadRequestDir    <目录>，自动收集 *.json（排除 *.result.json / *.done）
    -CadInputDwg      <种子dwg>，默认 headless\seed.dwg（CADJSON_RUN 按 XData 清场重建模）
    -CadResultDir     <目录>，默认与请求同目录，产物 <基名>.result.json
    -CadSaveDir       <目录>，默认与请求同目录；仅请求无 savePath 时经 CADJSON_SAVE 兜底
    # ---- Revit 任务来源 ----
    -RevitRequest     <请求json>，字符串数组，可多次传
    -RevitRequestDir  <目录>，自动收集 *.json（排除 *.result.json / *.done）
    -RevitRvtFiles    <rvt路径>，与请求一一对应；只给 1 个则所有请求复用
    -RevitResultDir   <目录>，默认与请求同目录
    -RevitSaveDir     <目录>，默认与请求同目录
    # ---- 通用 ----
    -TimeoutSec       每任务超时秒数（默认 600；Revit 冷启动需 >= 300）
    -LogFile          <日志路径>，默认 headless\run-headless-all.log
    -Resume           跳过 result 已存在且 status=ok 的任务（失败续跑）
    -NoClean          保留临时 .out/.err（默认只留 logs 汇总日志）

  用法示例：
    # 1) CAD 单请求
    .\run-headless-all.ps1 -Mode cad -CadRequest .\request.headless.json
    # 2) CAD 目录批量 + 续跑（成功过的自动跳过）
    .\run-headless-all.ps1 -Mode cad -CadRequestDir .\requests -CadResultDir .\results -Resume
    # 3) Revit 单请求（复用 seed rvt）
    .\run-headless-all.ps1 -Mode revit -RevitRequest .\revit\revit-request.json -RevitRvtFiles D:\models\seed.rvt
    # 4) 双无头全量（CAD 目录 + Revit 目录，各自带请求）
    .\run-headless-all.ps1 -Mode all -CadRequestDir .\cad-requests -RevitRequestDir .\revit\requests -RevitRvtFiles D:\models\seed.rvt -Resume
#>
param(
  [ValidateSet("cad", "revit", "all")]
  [string]$Mode = "all",
  [string[]]$CadRequest,
  [string]$CadRequestDir,
  [string]$CadInputDwg,
  [string]$CadResultDir,
  [string]$CadSaveDir,
  [string[]]$RevitRequest,
  [string]$RevitRequestDir,
  [string[]]$RevitRvtFiles,
  [string]$RevitResultDir,
  [string]$RevitSaveDir,
  [int]$TimeoutSec = 600,
  [string]$LogFile,
  [string]$AccoreExe,
  [string]$CadDll,
  [string]$RevitExePath,
  [switch]$Resume,
  [switch]$NoClean
)

$ScriptRoot = $PSScriptRoot
$SkillRoot  = Split-Path -Parent $PSScriptRoot
$ConfigFile = Join-Path $SkillRoot "config.json"
$Accore   = "D:\cad\AutoCAD 2021\accoreconsole.exe"
$CadDll   = "D:\cad\AutoCAD 2021\CadJsonPlugin.dll"
$ScrPath  = Join-Path $ScriptRoot "run.scr"
$RevitExe = "D:\Revit 2022\Revit.exe"
if (Test-Path $ConfigFile) {
  try {
    $cfg = Get-Content $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.paths.accoreconsoleExe) { $Accore = [Environment]::ExpandEnvironmentVariables([string]$cfg.paths.accoreconsoleExe) }
    if ($cfg.paths.cadJsonDll)       { $CadDll = [Environment]::ExpandEnvironmentVariables([string]$cfg.paths.cadJsonDll) }
    if ($cfg.paths.revitExe)         { $RevitExe = [Environment]::ExpandEnvironmentVariables([string]$cfg.paths.revitExe) }
  } catch {
    Write-Output "config.json 解析失败，使用内置默认路径"
  }
} else {
  Write-Output "未找到 config.json，使用内置默认路径"
}
if ($AccoreExe)     { $Accore   = [Environment]::ExpandEnvironmentVariables($AccoreExe) }
if ($CadDll)        { $CadDll   = [Environment]::ExpandEnvironmentVariables($CadDll) }
if ($RevitExePath)  { $RevitExe = [Environment]::ExpandEnvironmentVariables($RevitExePath) }
$LogsDir  = Join-Path $ScriptRoot "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }
if (-not $LogFile) { $LogFile = Join-Path $ScriptRoot "run-headless-all.log" }

function Write-Log {
  param([string]$Msg, [string]$Level = "INFO")
  $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Msg
  Write-Output $line
  Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Get-TaskRequests {
  param([string[]]$Explicit, [string]$Dir)
  $list = @()
  if ($Explicit) {
    foreach ($r in $Explicit) {
      if (Test-Path $r) { $list += (Resolve-Path $r).Path }
      else { Write-Log ("请求不存在: " + $r) "WARN" }
    }
  }
  if ($Dir) {
    if (Test-Path $Dir) {
      $files = Get-ChildItem -Path $Dir -Filter *.json -File | Where-Object {
        $_.Name -notmatch '\.result\.json$' -and $_.Name -notmatch '\.done$' -and $_.Name -notmatch '\.(out|err)$'
      }
      foreach ($f in $files) { $list += $f.FullName }
    } else { Write-Log ("请求目录不存在: " + $Dir) "WARN" }
  }
  $seen = @{}; $out = @()
  foreach ($p in $list) { if (-not $seen.ContainsKey($p)) { $seen[$p] = $true; $out += $p } }
  return $out
}

function Test-ResultOk {
  param([string]$Result)
  if (-not $Result -or -not (Test-Path $Result)) { return $false }
  try {
    $j = Get-Content $Result -Raw | ConvertFrom-Json
    return ($j.status -eq "ok")
  } catch { return $false }
}

function Invoke-CadTask {
  param([string]$Request, [string]$Result, [string]$Save, [string]$InputDwg)
  if (-not (Test-Path $Accore)) { Write-Log ("accoreconsole 不存在: " + $Accore) "WARN"; return $false }
  if (-not (Test-Path $CadDll)) { Write-Log ("CAD 插件 DLL 不存在: " + $CadDll) "WARN"; return $false }
  $base = [IO.Path]::GetFileNameWithoutExtension($Request)
  $out  = Join-Path $LogsDir ("cad-" + $base + ".out")
  $err  = Join-Path $LogsDir ("cad-" + $base + ".err")
  $oldReq = $env:CADJSON_REQUEST; $oldRes = $env:CADJSON_RESULT; $oldSave = $env:CADJSON_SAVE
  try {
    $env:CADJSON_REQUEST = $Request
    $env:CADJSON_RESULT  = $Result
    if ($Save) { $env:CADJSON_SAVE = $Save }
    Write-Log ("CAD 开始: {0} -> {1} (dwg={2})" -f $Request, $Result, $InputDwg)
    $p = Start-Process -FilePath $Accore `
         -ArgumentList ('/i "' + $InputDwg + '" /s "' + $ScrPath + '"') `
         -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -NoNewWindow
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
      $p.Kill()
      Write-Log ("CAD 超时已终止: " + $Request) "WARN"
      return $false
    }
    if (-not (Test-Path $Result)) { Write-Log ("CAD 未生成 result: " + $Result) "WARN"; return $false }
    $j = Get-Content $Result -Raw | ConvertFrom-Json
    if ($j.status -ne "ok") { Write-Log ("CAD status=" + $j.status + " : " + $Request) "WARN"; return $false }
    Write-Log ("CAD 成功: {0} entityCount={1}" -f $Result, $j.entityCount)
    return $true
  } catch {
    Write-Log ("CAD 异常: " + $_.Exception.Message) "WARN"
    return $false
  } finally {
    $env:CADJSON_REQUEST = $oldReq; $env:CADJSON_RESULT = $oldRes
    if ($oldSave) { $env:CADJSON_SAVE = $oldSave } else { Remove-Item Env:CADJSON_SAVE -ErrorAction SilentlyContinue }
  }
}

function Invoke-RevitTask {
  param([string]$Request, [string]$Result, [string]$Save, [string]$Rvt)
  if (-not $Result) { Write-Log ("结果路径为空，拒绝执行: " + $Request) "WARN"; return $false }
  if (-not (Test-Path $RevitExe)) { Write-Log ("Revit.exe 不存在: " + $RevitExe) "WARN"; return $false }
  if (-not (Test-Path $Rvt)) { Write-Log ("RVT 不存在: " + $Rvt) "WARN"; return $false }
  $launcher = Join-Path $PSScriptRoot "launch-revit-hidden.ps1"
  if (-not (Test-Path $launcher)) { Write-Log ("缺少私有桌面启动器: " + $launcher) "WARN"; return $false }
  Write-Log ("Revit 开始（私有桌面）: {0} (rvt={1})" -f $Request, $Rvt)
  if (Test-Path $Result) { Remove-Item $Result -Force -ErrorAction SilentlyContinue }
  $argv = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$launcher,
            "-Document",$Rvt,"-Request",$Request,"-Result",$Result,
            "-RevitExe",$RevitExe,"-TimeoutSec",$TimeoutSec)
  if ($Save) { $argv += @("-Save",$Save) }
  & powershell @argv 2>&1 | ForEach-Object { Write-Log ([string]$_) }
  if ($LASTEXITCODE -ne 0) { Write-Log ("Revit 失败: " + $Request) "WARN"; return $false }
  try {
    $j = Get-Content $Result -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($j.status -eq "ok") {
      Write-Log ("Revit 成功: {0} entityCount={1}" -f $Result, $j.entityCount)
      return $true
    }
    Write-Log ("Revit status=" + $j.status + " : " + $Request) "WARN"
    return $false
  } catch {
    Write-Log ("Revit 结果解析失败: " + $_.Exception.Message) "WARN"
    return $false
  }
}

# ---------- 主流程 ----------
Write-Log ("========== 双无头驱动启动 mode=" + $Mode + " resume=" + $Resume + " ==========")

$okCount = 0; $skipCount = 0; $failCount = 0
$failList = @()

# --- CAD 任务 ---
if ($Mode -eq "cad" -or $Mode -eq "all") {
  $cadReqs = @(Get-TaskRequests -Explicit $CadRequest -Dir $CadRequestDir)
  if (-not $CadInputDwg) { $CadInputDwg = Join-Path $ScriptRoot "seed.dwg" }
  Write-Log ("CAD 任务数: " + $cadReqs.Count)
  foreach ($req in $cadReqs) {
    $base   = [IO.Path]::GetFileNameWithoutExtension($req)
    $resDir = if ($CadResultDir) { $CadResultDir } else { [IO.Path]::GetDirectoryName($req) }
    $result = Join-Path $resDir ($base + ".result.json")
    $save   = $null
    if ($CadSaveDir) { $save = Join-Path $CadSaveDir ($base + ".dwg") }
    if ($Resume -and (Test-ResultOk $result)) { $skipCount++; Write-Log ("CAD 跳过(已成功): " + $result); continue }
    if (Invoke-CadTask -Request $req -Result $result -Save $save -InputDwg $CadInputDwg) { $okCount++ }
    else { $failCount++; $failList += $req }
  }
}

# --- Revit 任务 ---
if ($Mode -eq "revit" -or $Mode -eq "all") {
  $revitReqs = @(Get-TaskRequests -Explicit $RevitRequest -Dir $RevitRequestDir)
  $rvtList = @()
  foreach ($r in @($RevitRvtFiles)) {
    if (Test-Path $r) { $rvtList += (Resolve-Path $r).Path }
    else { Write-Log ("RVT 不存在: " + $r) "WARN" }
  }
  if ($rvtList.Count -eq 1) { while ($rvtList.Count -lt $revitReqs.Count) { $rvtList += $rvtList[0] } }
  elseif ($rvtList.Count -gt 1 -and $rvtList.Count -ne $revitReqs.Count) {
    Write-Log ("RevitRvtFiles 数量(" + $rvtList.Count + ")须与请求数(" + $revitReqs.Count + ")一致或为 1") "WARN"
  }
  if ($revitReqs.Count -gt 0 -and $rvtList.Count -eq 0) {
    Write-Log ("Revit 任务需要 -RevitRvtFiles 提供 RVT 文件") "WARN"
  }
  Write-Log ("Revit 任务数: " + $revitReqs.Count)
  for ($i = 0; $i -lt $revitReqs.Count; $i++) {
    $req    = $revitReqs[$i]
    if (Test-Path -LiteralPath $req) { $req = (Resolve-Path -LiteralPath $req).Path }
    $rvt    = if ($i -lt $rvtList.Count) { $rvtList[$i] } else { "" }
    if ($rvt -and (Test-Path -LiteralPath $rvt)) { $rvt = (Resolve-Path -LiteralPath $rvt).Path }
    if (-not $rvt) { $failCount++; $failList += $req; Write-Log ("Revit 无配对 RVT: " + $req) "WARN"; continue }
    $base   = [IO.Path]::GetFileNameWithoutExtension($req)
    $resDir = if ($RevitResultDir) { $RevitResultDir } else { [IO.Path]::GetDirectoryName($req) }
    $result = Join-Path $resDir ($base + ".result.json")
    $save   = $null
    if ($RevitSaveDir) { $save = Join-Path $RevitSaveDir ($base + ".rvt") }
    if ($Resume -and (Test-ResultOk $result)) { $skipCount++; Write-Log ("Revit 跳过(已成功): " + $result); continue }
    if (Invoke-RevitTask -Request $req -Result $result -Save $save -Rvt $rvt) { $okCount++ }
    else { $failCount++; $failList += $req }
  }
}

Write-Log ("========== 完成: 成功=" + $okCount + " 跳过=" + $skipCount + " 失败=" + $failCount + " ==========")
if ($failCount -gt 0) {
  Write-Log ("失败任务列表（可修正后加 -Resume 续跑）:")
  foreach ($f in $failList) { Write-Log ("  - " + $f) "WARN" }
  exit 1
}
exit 0
