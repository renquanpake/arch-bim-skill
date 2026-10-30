<#
  arch-bim-skill CAD 真无头驱动脚本
  用法:
    .\run-headless.ps1 -Request .\request.headless.json -Result .\result.headless.json -Save D:\models\out.dwg -InputDwg .\seed.dwg
  说明: 以 accoreconsole.exe (AutoCAD Core Console) 无 GUI 方式加载 CadJsonPlugin.dll 并执行 CADJSON_RUN
#>
param(
  [Parameter(Mandatory=$true)][string]$Request,
  [string]$Result,
  [string]$Save,
  [string]$InputDwg,
  [int]$TimeoutSec = 300,
  [string]$AccoreExe,
  [string]$CadDll
)
$ErrorActionPreference = "Stop"
$SkillRoot = Split-Path -Parent $PSScriptRoot
$ConfigFile = Join-Path $SkillRoot "config.json"
$core = "D:\cad\AutoCAD 2021\accoreconsole.exe"
$dll  = "D:\cad\AutoCAD 2021\CadJsonPlugin.dll"
if (Test-Path $ConfigFile) {
  try {
    $cfg = Get-Content $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.paths.accoreconsoleExe) { $core = [Environment]::ExpandEnvironmentVariables([string]$cfg.paths.accoreconsoleExe) }
    if ($cfg.paths.cadJsonDll)       { $dll  = [Environment]::ExpandEnvironmentVariables([string]$cfg.paths.cadJsonDll) }
  } catch {
    Write-Output "config.json 解析失败，使用内置默认路径"
  }
} else {
  Write-Output "未找到 config.json，使用内置默认路径"
}
if ($AccoreExe) { $core = [Environment]::ExpandEnvironmentVariables($AccoreExe) }
if ($CadDll)    { $dll  = [Environment]::ExpandEnvironmentVariables($CadDll) }
if (-not (Test-Path $core)) { throw "accoreconsole 不存在: $core" }
if (-not (Test-Path $dll))  { throw "插件 DLL 不存在: $dll" }
$Request = (Resolve-Path $Request).Path
if (-not $Result)   { $Result   = [IO.Path]::ChangeExtension($Request, ".result.json") }
if (-not $InputDwg) { $InputDwg = Join-Path $PSScriptRoot "seed.dwg" }

$scr = Join-Path $env:TEMP ("cadjson_" + [guid]::NewGuid().ToString("N") + ".scr")
$lines = @("NETLOAD", ('"' + $dll + '"'), "CADJSON_RUN")
[IO.File]::WriteAllText($scr, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.ASCIIEncoding))

$out = Join-Path $env:TEMP ("cadjson_" + [guid]::NewGuid().ToString("N") + ".out")
$err = Join-Path $env:TEMP ("cadjson_" + [guid]::NewGuid().ToString("N") + ".err")
$env:CADJSON_REQUEST = $Request
$env:CADJSON_RESULT  = $Result
if ($Save) { $env:CADJSON_SAVE = $Save }

$p = Start-Process -FilePath $core -ArgumentList ('/i "' + $InputDwg + '" /s "' + $scr + '"') `
     -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -NoNewWindow
if (-not $p.WaitForExit($TimeoutSec * 1000)) { $p.Kill(); throw "accoreconsole 超时 ${TimeoutSec}s" }

$console = Get-Content $out -Encoding Unicode -ErrorAction SilentlyContinue
$console | Where-Object { $_ -match "CADJSON_RUN" } | ForEach-Object { Write-Output $_ }
if (-not (Test-Path $Result)) { throw "未生成 result.json: $Result" }
Get-Content $Result -Raw