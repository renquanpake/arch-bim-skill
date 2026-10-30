<#
  launch-revit-hidden.ps1 — 在私有桌面启动 Revit，用户桌面零窗口

  原理：CreateDesktop 建一个独立桌面，CreateProcess 时 lpDesktop 指向它。
  Revit 的所有窗口（splash/主窗/对话框）都落在该桌面上，用户看不到、不被抢焦点。
  进程照常运行：插件 DocumentOpened/Idling 触发、建模、保存、SC_CLOSE 退出，均不受影响。

  用法（通常由 run-headless-all.ps1 调用）：
    .\launch-revit-hidden.ps1 -Document D:\models\seed.rvt `
        -Request .\revit\req.json -Result .\revit\res.json -Save D:\models\out.rvt

  退出码：0 成功（result 出现且 status 非 error）；1 失败/超时
#>
param(
  [Parameter(Mandatory=$true)][string]$Document,
  [Parameter(Mandatory=$true)][string]$Request,
  [Parameter(Mandatory=$true)][string]$Result,
  [string]$Save,
  [string]$RevitExe = "D:\Revit 2022\Revit.exe",
  [string]$DesktopName = "MarvisHidden",
  [int]$TimeoutSec = 300,
  [int]$PollSec = 3
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $RevitExe)) { throw "Revit.exe 不存在: $RevitExe" }
if (-not (Test-Path $Document)) { throw "RVT 不存在: $Document" }
if (-not (Test-Path $Request)) { throw "请求文件不存在: $Request" }

$code = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public class HiddenDesktop {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  struct STARTUPINFO {
    public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
    public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
  }
  [StructLayout(LayoutKind.Sequential)]
  struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }

  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern IntPtr CreateDesktop(string name, IntPtr dev, IntPtr devmode, int flags, uint access, IntPtr sa);
  [DllImport("user32.dll", SetLastError=true)] static extern bool CloseDesktop(IntPtr h);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern bool CreateProcess(string app, string cmd, IntPtr pa, IntPtr ta, bool inherit,
    uint flags, IntPtr env, string dir, ref STARTUPINFO si, out PROCESS_INFORMATION pi);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);

  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc lp, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  delegate bool EnumProc(IntPtr h, IntPtr l);

  static IntPtr _desk = IntPtr.Zero;

  /// <summary>在私有桌面启动进程；返回 pid。桌面句柄保留到 Close()。</summary>
  public static int Launch(string exe, string args, string desktop) {
    _desk = CreateDesktop(desktop, IntPtr.Zero, IntPtr.Zero, 0, 0x10000000 /*GENERIC_ALL*/, IntPtr.Zero);
    if (_desk == IntPtr.Zero) throw new Exception("CreateDesktop 失败: " + Marshal.GetLastWin32Error());
    var si = new STARTUPINFO();
    si.cb = Marshal.SizeOf(si);
    si.lpDesktop = desktop;
    PROCESS_INFORMATION pi;
    if (!CreateProcess(exe, args, IntPtr.Zero, IntPtr.Zero, false, 0, IntPtr.Zero, null, ref si, out pi)) {
      int err = Marshal.GetLastWin32Error();
      CloseDesktop(_desk); _desk = IntPtr.Zero;
      throw new Exception("CreateProcess 失败: " + err);
    }
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return pi.dwProcessId;
  }

  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }

  /// <summary>按私有桌面找最大的可见顶层窗口（Revit 主窗），发 SC_CLOSE 让它自己退</summary>
  public static bool CloseMainWindow() {
    if (_desk == IntPtr.Zero) return false;
    IntPtr best = IntPtr.Zero; long bestArea = 0;
    EnumDesktopWindows(_desk, delegate(IntPtr h, IntPtr l) {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(512); GetWindowText(h, sb, 512);
      if (sb.Length == 0) return true;
      RECT r; if (!GetWindowRect(h, out r)) return true;
      long area = (long)(r.R - r.L) * (r.B - r.T);
      if (area > bestArea) { bestArea = area; best = h; }
      return true;
    }, IntPtr.Zero);
    if (best == IntPtr.Zero) return false;
    PostMessage(best, 0x0112 /*WM_SYSCOMMAND*/, (IntPtr)0xF060 /*SC_CLOSE*/, IntPtr.Zero);
    return true;
  }

  [DllImport("user32.dll")] static extern bool EnumDesktopWindows(IntPtr desk, EnumProc lp, IntPtr l);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);

  /// <summary>自动确认私有桌面上的 TaskDialog（如“未签名加载项”安全提示）。
  /// TDM_CLICK_BUTTON = WM_USER + 102；按钮 1003 = 默认“总是加载”。</summary>
  public static int DismissTaskDialogs() {
    if (_desk == IntPtr.Zero) return 0;
    int clicked = 0;
    var dialogs = new List<IntPtr>();
    EnumDesktopWindows(_desk, delegate(IntPtr h, IntPtr l) {
      var cn = new StringBuilder(64);
      GetClassNameW(h, cn, 64);
      if (cn.ToString() == "#32770") dialogs.Add(h);
      return true;
    }, IntPtr.Zero);
    foreach (var d in dialogs) {
      SendMessage(d, 0x0466 /*TDM_CLICK_BUTTON*/, (IntPtr)1003, IntPtr.Zero);
      clicked++;
    }
    return clicked;
  }

  public static void Close() {
    if (_desk != IntPtr.Zero) { CloseDesktop(_desk); _desk = IntPtr.Zero; }
  }

  /// <summary>用户所在桌面（WinSta0\Default）上该进程的可见窗口列表——用于自证"没弹窗"</summary>
  public static string[] VisibleOnUserDesktop(int pid) {
    var list = new List<string>();
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p == (uint)pid && IsWindowVisible(h)) {
        var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
        var cn = new StringBuilder(128); GetClassName(h, cn, 128);
        if (sb.Length > 0) list.Add(cn + "::" + sb);
      }
      return true;
    }, IntPtr.Zero);
    return list.ToArray();
  }
}
'@

if (-not ("HiddenDesktop" -as [type])) {
  Add-Type -TypeDefinition $code -Language CSharp
}

$quote = { param($s) '"' + $s + '"' }
$cmdLine = (& $quote $RevitExe) + ' ' + (& $quote $Document)

$env:REVITJSON_REQUEST = (Resolve-Path $Request).Path
$env:REVITJSON_RESULT = $Result
if ($Save) { $env:REVITJSON_SAVE = $Save }

if (Test-Path $Result) { Remove-Item $Result -Force -ErrorAction SilentlyContinue }

$pid2 = [HiddenDesktop]::Launch($RevitExe, $cmdLine, $DesktopName)
Write-Output "[hidden] Revit 已启动于私有桌面 '$DesktopName'，pid=$pid2"

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$leaked = 0
$status = $null

try {
  while ((Get-Date) -lt $deadline) {
    $vis = [HiddenDesktop]::VisibleOnUserDesktop($pid2)
    if ($vis.Count -gt $leaked) {
      $leaked = $vis.Count
      Write-Output "[hidden] !!! 用户桌面出现可见窗口: $($vis -join ' | ')"
    }
    $dismissed = [HiddenDesktop]::DismissTaskDialogs()
    if ($dismissed -gt 0) { Write-Output "[hidden] 自动确认 TaskDialog $dismissed 个（安全提示）" }
    if (Test-Path $Result) {
      try {
        $j = Get-Content $Result -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($j.status) {
          $status = $j.status
          Start-Sleep -Seconds 2
          if (Get-Process -Id $pid2 -ErrorAction SilentlyContinue) {
            $sent = [HiddenDesktop]::CloseMainWindow()
            Write-Output "[hidden] 结果已出，发送 SC_CLOSE 令 Revit 自行退出 (sent=$sent)"
            $exitDeadline = (Get-Date).AddSeconds(25)
            while ((Get-Date) -lt $exitDeadline) {
              if (-not (Get-Process -Id $pid2 -ErrorAction SilentlyContinue)) { break }
              Start-Sleep -Seconds 1
            }
          }
          break
        }
      } catch { }
    }
    if (-not (Get-Process -Id $pid2 -ErrorAction SilentlyContinue)) {
      Write-Output "[hidden] Revit 进程已退出（未写结果）"
      break
    }
    Start-Sleep -Seconds $PollSec
  }
} finally {
  if (Get-Process -Id $pid2 -ErrorAction SilentlyContinue) {
    Write-Output "[hidden] 超时/未完成，终止 pid=$pid2"
    & taskkill /PID $pid2 /T /F 2>&1 | Out-Null
  }
  [HiddenDesktop]::Close()
}

$visFinal = [HiddenDesktop]::VisibleOnUserDesktop($pid2)
Write-Output "[hidden] 用户桌面可见窗口峰值=$leaked，结束时=$($visFinal.Count)"

if (-not (Test-Path $Result)) { Write-Output "[hidden] 失败：无结果文件"; exit 1 }
if ($status -eq "error") { Write-Output "[hidden] 失败：status=error"; exit 1 }
Write-Output "[hidden] 完成：status=$status result=$Result"
exit 0
