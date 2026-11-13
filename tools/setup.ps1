param([switch]$DetectOnly)
# Windows PowerShell 5.1 bootstrap. No system settings or PATH modifications.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Write-Host "PetPal · 本地安装 / 修复安装" -ForegroundColor Cyan
Write-Host "目录：$root"
Write-Host "[1/5] 搜索本机 Python（支持官方 CPython 3.10–3.14，64 位）"
$candidates = New-Object 'System.Collections.Generic.List[string]'
function Add-Candidate([string]$candidate) {
    if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf) -and $candidate -notmatch '\\WindowsApps\\' -and $candidate -notlike "$root*" -and -not $candidates.Contains($candidate)) {
        $candidates.Add($candidate)
    }
}
Get-Command python.exe,python3.exe -All -ErrorAction SilentlyContinue | ForEach-Object { Add-Candidate $_.Source }
$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($pyLauncher) {
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $pyLauncher.Source -0p 2>$null | ForEach-Object {
        if ($_ -match '([A-Za-z]:\\.*?python(?:[0-9.]*)?\.exe)\s*$') { Add-Candidate $Matches[1] }
    }
    $ErrorActionPreference = $savedPreference
}
foreach ($hive in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
    if (Test-Path $hive) {
        Get-ChildItem $hive | ForEach-Object {
            $key = Join-Path $_.PSPath 'InstallPath'
            if (Test-Path $key) {
                $props = Get-ItemProperty $key
                Add-Candidate $props.ExecutablePath
                $folder = (Get-Item $key).GetValue('')
                if ($folder) { Add-Candidate (Join-Path $folder 'python.exe') }
            }
        }
    }
}
foreach ($pattern in @("$env:LOCALAPPDATA\Programs\Python\Python*\python.exe", "$env:ProgramFiles\Python*\python.exe", 'C:\Python*\python.exe', 'D:\Python*\python.exe')) {
    Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object { Add-Candidate $_.FullName }
}
$probe = 'import sys,sysconfig,json,pathlib;print(json.dumps(dict(exe=sys.executable,version=sys.version.split()[0],ok=sys.implementation.name==''cpython'' and (3,10)<=sys.version_info[:2]<(3,15) and sysconfig.get_platform() in (''win-amd64'',''win-arm64'') and not sysconfig.get_config_var(''Py_GIL_DISABLED'') and (pathlib.Path(sys.base_prefix)/''pythonw.exe'').is_file())))'
function Probe-Python([string]$candidate) {
    try {
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = $candidate
        $info.Arguments = '-I -c "' + $probe + '"'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $p = [Diagnostics.Process]::Start($info)
        if (-not $p.WaitForExit(5000)) { $p.Kill(); return $null }
        if ($p.ExitCode -ne 0) { return $null }
        return ($p.StandardOutput.ReadToEnd() | ConvertFrom-Json)
    } catch { return $null }
}
$valid = @()
foreach ($candidate in $candidates) {
    $result = Probe-Python $candidate
    if ($result -and $result.ok) {
        if ($valid.exe -notcontains $result.exe) { $valid += $result }
    } else { Write-Host "跳过不兼容的解释器：$candidate" -ForegroundColor DarkGray }
}
if ($valid.Count -eq 0) {
    Write-Host '没有找到兼容版本。下载官网：https://www.python.org/downloads/windows/' -ForegroundColor Yellow
    Start-Process 'https://www.python.org/downloads/windows/'
    $manual = Read-Host '如果已安装在其他位置，请输入 python.exe 完整路径（留空退出）'
    if ($manual) {
        $result = Probe-Python $manual.Trim('"')
        if ($result -and $result.ok) { $valid = @($result) }
    }
    if ($valid.Count -eq 0) { exit 1 }
}
for ($i = 0; $i -lt $valid.Count; $i++) {
    Write-Host ("  [{0}] Python {1}  {2}" -f ($i + 1), $valid[$i].version, $valid[$i].exe)
}
$index = 1
if ($valid.Count -gt 1) {
    do {
        $answer = Read-Host "选择用于创建虚拟环境的 Python [1-$($valid.Count)]"
        $parsed = [int]::TryParse($answer, [ref]$index)
    } until ($parsed -and $index -ge 1 -and $index -le $valid.Count)
}
if ($DetectOnly) { $valid | ConvertTo-Json -Compress; exit 0 }
$selected = $valid[$index - 1].exe
Write-Host "使用：$selected" -ForegroundColor Green
& $selected -I -X utf8 -B (Join-Path $root 'tools\install.py')
exit $LASTEXITCODE
