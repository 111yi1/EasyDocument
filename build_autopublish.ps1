#requires -Version 5.1
<#
.SYNOPSIS
    把 EasyDocument 打包为独立 exe（PyInstaller）。

.DESCRIPTION
    默认生成 onedir 目录版本（启动快）；加 -OneFile 生成单文件版本。
    打包前会先跑单元测试，避免把坏版本发出去。
    成功后自动发布到 DSH 工作区之外，避免打包产物继承 Low 完整性标签。

.PARAMETER Python
    使用的 Python 解释器。默认依次尝试环境变量 EASY_DOCUMENT_PYTHON、
    conda 环境 pi_thon、PATH 中的 python。

.PARAMETER OneFile
    生成单文件 exe（启动稍慢），默认 onedir。

.PARAMETER SkipTests
    跳过打包前的单元测试。

.PARAMETER PublishDir
    正式发布目录。默认 onedir: D:\EasyDocument-release；
    OneFile: D:\EasyDocument-release-onefile。
    必须位于 DSH 项目工作区之外。

.PARAMETER NoPublish
    只打包、不复制到正式发布目录。

.EXAMPLE
    .\build.ps1
    .\build.ps1 -OneFile
    .\build.ps1 -Python E:\ProgramData\anaconda3\envs\pi_thon\python.exe
    .\build.ps1 -SkipTests
    .\build.ps1 -SkipTests -PublishDir "D:\MyReleases\EasyDocument"
    .\build.ps1 -SkipTests -NoPublish
#>
param(
    [string]$Python = "",
    [switch]$OneFile,
    [switch]$SkipTests,
    [string]$PublishDir = "",
    [switch]$NoPublish
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path (Join-Path $root 'main.py'))) {
    throw "未找到 main.py，请在项目根目录运行本脚本。"
}

if (-not $Python) {
    if ($env:EASY_DOCUMENT_PYTHON) {
        $Python = $env:EASY_DOCUMENT_PYTHON
    }
    elseif (Test-Path 'E:\ProgramData\anaconda3\envs\pi_thon\python.exe') {
        $Python = 'E:\ProgramData\anaconda3\envs\pi_thon\python.exe'
    }
    else {
        $Python = 'python'
    }
}

Write-Host "使用 Python：$Python"
& $Python -c "import sys, PyInstaller; print('Python', sys.version.split()[0], '| PyInstaller', PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw '该解释器不可用或未安装 PyInstaller。请先执行：python -m pip install pyinstaller'
}

if (-not $SkipTests) {
    Write-Host '运行单元测试…'
    & $Python -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) {
        throw '单元测试未通过，已中止打包。'
    }
}

$pythonDir = Split-Path -Parent (Resolve-Path $Python)
$condaBin = Join-Path $pythonDir 'Library\bin'
$condaLib = Join-Path $pythonDir 'Library\lib'
if (Test-Path $condaBin) {
    # conda 环境的 C 运行库 / Tcl / Tk 不在标准 DLLs 目录，需要显式加入搜索路径
    $env:PATH = "$condaBin;$pythonDir\DLLs;$env:PATH"
    Write-Host "已加入 conda DLL 搜索路径：$condaBin"
}

foreach ($dir in @('build', 'dist')) {
    $target = Join-Path $root $dir
    if (Test-Path $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

$arguments = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--windowed',
    '--name', 'EasyDocument'
)
if ($OneFile) {
    $arguments += '--onefile'
}
else {
    $arguments += '--onedir'
}

# conda 环境：把 Tcl/Tk 运行库与数据目录一起打包，避免 exe 启动时缺 DLL
if (Test-Path $condaBin) {
    Get-ChildItem $condaBin -Filter '*.dll' -File | ForEach-Object {
        $arguments += @('--add-binary', "$($_.FullName);.")
    }
}
if (Test-Path (Join-Path $condaLib 'tcl8.6')) {
    $arguments += @('--add-data', "$(Join-Path $condaLib 'tcl8.6');_tcl_data")
}
if (Test-Path (Join-Path $condaLib 'tk8.6')) {
    $arguments += @('--add-data', "$(Join-Path $condaLib 'tk8.6');_tk_data")
}
$arguments += 'main.py'

Write-Host "执行：$Python $($arguments -join ' ')"
& $Python @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller 打包失败。'
}

if ($OneFile) {
    $exe = Join-Path $root 'dist\EasyDocument.exe'
}
else {
    $exe = Join-Path $root 'dist\EasyDocument\EasyDocument.exe'
}
if (-not (Test-Path $exe)) {
    throw "未找到打包产物：$exe"
}

$sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "打包完成：$exe（$sizeMB MB）"
Write-Host '打包成功；请运行正式发布目录内的 EXE。数据目录依然由应用原有配置决定。'


# 自动发布：工作区继续保留 DSH 的 Low 标签，正式 EXE 复制到独立目录。
# /COPY:DAT 和 /DCOPY:DAT 只复制数据、属性、时间戳，不复制源文件的安全 ACL。
if (-not $NoPublish) {
    if (-not $PublishDir) {
        # 不同打包模式使用不同的默认发布目录，避免残留不匹配的旧依赖文件。
        if ($OneFile) {
            $PublishDir = 'D:\EasyDocument-release-onefile'
        }
        else {
            $PublishDir = 'D:\EasyDocument-release'
        }
    }

    $publishFull = [System.IO.Path]::GetFullPath($PublishDir).TrimEnd('\')
    $rootFull = [System.IO.Path]::GetFullPath($root).TrimEnd('\')
    $volumeRoot = [System.IO.Path]::GetPathRoot($publishFull).TrimEnd('\')
    $cmp = [System.StringComparison]::OrdinalIgnoreCase

    if ($publishFull.Equals($rootFull, $cmp) -or
        $publishFull.StartsWith($rootFull + '\', $cmp)) {
        throw "发布目录不能位于 DSH 工作区内：$publishFull"
    }
    if ($publishFull.Equals($volumeRoot, $cmp)) {
        throw "请不要把磁盘根目录作为发布目录：$publishFull"
    }

    # 不清空发布目录、不使用 /MIR，以免误删用户的其他文件。
    if (-not (Test-Path -LiteralPath $publishFull)) {
        New-Item -ItemType Directory -Path $publishFull -Force | Out-Null
    }

    if ($OneFile) {
        $copySource = Join-Path $root 'dist'
        $copyArgs = @($copySource, $publishFull, 'EasyDocument.exe',
            '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:1', '/NP')
    }
    else {
        $copySource = Join-Path $root 'dist\EasyDocument'
        $copyArgs = @($copySource, $publishFull, '/E',
            '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:1', '/NP')
    }

    Write-Host "发布到（不复制权限）：$publishFull"
    & robocopy.exe @copyArgs
    $copyExitCode = $LASTEXITCODE
    # Robocopy 的 0~7 都属于成功/无致命错误，8 及以上才是失败。
    if ($copyExitCode -ge 8) {
        throw "发布复制失败（Robocopy 返回码：$copyExitCode）。请确认发布目录的程序已退出。"
    }

    $publishedExe = Join-Path $publishFull 'EasyDocument.exe'
    if (-not (Test-Path -LiteralPath $publishedExe -PathType Leaf)) {
        throw "发布后未找到 EXE：$publishedExe"
    }

    # 如果目标目录本身也被施加 Low 标签，复制时仍可能继承它。
    # 用 Windows 内置 icacls 检查发布产物；绝不在脚本里改动 DSH 安全权限。
    $aclOutput = (& icacls.exe $publishedExe 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "无法检查已发布 EXE 的权限：$publishedExe`n$aclOutput"
    }
    if ($aclOutput -match '(?i)Low Mandatory Level|低强制级别|S-1-16-4096') {
        throw "发布的 EXE 仍带有 Low 完整性标签，请换用工作区之外且没有 Low 标签的发布目录：$publishedExe"
    }

    Write-Host "发布完成：$publishedExe" -ForegroundColor Green
    Write-Host '请从发布目录启动或分发 EXE，不要从工作区 dist 目录启动。'
}
else {
    Write-Host '已跳过发布，仅在工作区 dist 中生成打包产物。'
}
