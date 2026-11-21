<#
.SYNOPSIS
    Build a ctgedit release for Windows: the one-folder program, its smoke
    test and the installer.

.DESCRIPTION
    1. PyInstaller turns the source tree into dist\ctgedit\ (ctgedit.exe +
       _internal\ with Python, Qt and the data files) -- packaging\ctgedit.spec.
    2. tools\release_check.py --frozen runs the headless open / edit / save /
       close smoke test on the built exe (the demo's Overview.ctg and Pump.ctg in
       tests\fixtures, and Example\Mixer.ctg and ExampleSA\Mixing_L3.ctg of an
       installed Plant SCADA; on copies -- the projects are never written).
    3. Inno Setup 6 compiles packaging\installer.iss into
       dist\ctgedit-<version>-setup.exe.

    Run the full release gate (python tools\release_check.py) before a build
    you hand out: this script only smoke-tests what it built.

.PARAMETER Python
    The Python that has requirements.txt and packaging\requirements-build.txt
    installed. Default: .venv\Scripts\python.exe of this checkout, else the
    python on PATH.

.PARAMETER PyInstallerPath
    A folder PyInstaller was installed into with "pip install --target"
    (instead of into the Python environment itself). Optional.

.PARAMETER InnoSetup
    ISCC.exe. Default: Inno Setup 6 under Program Files, the per-user
    install, or ISCC on PATH.

.PARAMETER Page
    Pages for the smoke test (default: the demo's Overview + Pump, and Mixer +
    Mixing_L3 of the installed AVEVA projects when there are any).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build.ps1

.EXAMPLE
    packaging\build.ps1 -SkipInstaller
#>
param(
    [string]$Python = "",
    [string]$PyInstallerPath = "",
    [string]$InnoSetup = "",
    [string[]]$Page = @(),
    [switch]$SkipSmoke,
    [switch]$SkipInstaller,
    # build without the Cicode simulator (ctgedit then has no Simulate engine)
    [switch]$WithoutSimulator
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot

function Test-Import([string]$Exe, [string]$Module) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe -c "import $Module" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $old
    }
}

function Invoke-Step([string]$What, [string]$Exe, [string[]]$Arguments) {
    Write-Host ""
    Write-Host "== $What" -ForegroundColor Cyan
    # a native program's progress on stderr is not an error (Windows PowerShell
    # would otherwise stop at PyInstaller's first INFO line)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $old
    }
    if ($code -ne 0) {
        throw "$What failed (exit code $code)."
    }
}

# -- the Python to build with
if (-not $Python) {
    $venv = Join-Path $Repo ".venv\Scripts\python.exe"
    if (Test-Path $venv) {
        $Python = $venv
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) {
            throw "No Python found. Create the environment first (README.md, 'Build a release') or pass -Python <path to python.exe>."
        }
        $Python = $cmd.Source
    }
}

# this may run in the caller's session: PYTHONPATH is put back afterwards
$OldPythonPath = $env:PYTHONPATH
# relative arguments are relative to where the caller is, not to the checkout
if (Test-Path $Python) { $Python = (Resolve-Path $Python).Path }
if ($PyInstallerPath -and (Test-Path $PyInstallerPath)) { $PyInstallerPath = (Resolve-Path $PyInstallerPath).Path }
if ($InnoSetup -and (Test-Path $InnoSetup)) { $InnoSetup = (Resolve-Path $InnoSetup).Path }
$Page = @($Page | ForEach-Object { if (Test-Path $_) { (Resolve-Path $_).Path } else { $_ } })
# run from the checkout whatever the caller's current folder is: "python -c"
# and "python -m" put the current folder ahead of PYTHONPATH, so another
# checkout's ctgedit would be the one built and versioned
Push-Location $Repo
try {
    $env:PYTHONPATH = $Repo
    if ($PyInstallerPath) {
        $env:PYTHONPATH = "$Repo;$PyInstallerPath"
    }
    if (-not (Test-Import $Python "PyInstaller")) {
        throw ("PyInstaller is not installed for $Python. Install it with:`n" +
               "    & `"$Python`" -m pip install -r packaging\requirements-build.txt`n" +
               "or pass -PyInstallerPath <folder it was installed into with pip --target>.")
    }
    $Version = (& $Python -c "import ctgedit; print(ctgedit.__version__)").Trim()
    Write-Host "ctgedit $Version -- building with $Python"

    # -- 0. the Cicode simulator (external\cicode-sim): built here, never left out unasked
    if ($WithoutSimulator) {
        $env:CTG_BUILD_WITHOUT_SIM = "1"
        Write-Host "Cicode simulator: left out (-WithoutSimulator)"
    } else {
        $env:CTG_BUILD_WITHOUT_SIM = ""
        $Csproj = Join-Path $Repo "external\cicode-sim\engine\src\ctsim\ctsim.csproj"
        if (-not (Test-Path $Csproj)) {
            throw ("The Cicode simulator's source is missing ($Csproj): run`n" +
                   "    git submodule update --init external/cicode-sim`n" +
                   "or build with -WithoutSimulator.")
        }
        if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
            throw "The .NET SDK (dotnet) is needed to build the Cicode simulator, or build with -WithoutSimulator."
        }
        Invoke-Step "Cicode simulator" "dotnet" @("build", $Csproj, "-c", "Release", "-f", "net48", "-nologo", "-v", "q")
        $Ctsim = Join-Path $Repo "external\cicode-sim\engine\src\ctsim\bin\Release\net48\ctsim.exe"
        if (-not (Test-Path $Ctsim)) { throw "The Cicode simulator build finished but $Ctsim is missing." }
        $SimVersion = (& $Ctsim --version 2>&1 | Out-String).Trim()
        Write-Host "Cicode simulator: $SimVersion"
    }

    # -- 1. the one-folder program
    $Dist = Join-Path $Repo "dist"
    $Work = Join-Path $Repo "build\pyinstaller"
    Invoke-Step "PyInstaller" $Python @("-m", "PyInstaller", (Join-Path $Repo "packaging\ctgedit.spec"),
                                        "--noconfirm", "--log-level", "WARN",
                                        "--distpath", $Dist, "--workpath", $Work)
    $Exe = Join-Path $Dist "ctgedit\ctgedit.exe"
    if (-not (Test-Path $Exe)) {
        throw "PyInstaller finished but $Exe is missing."
    }

    # -- 2. the smoke test on the built exe
    if (-not $SkipSmoke) {
        $check = @((Join-Path $Repo "tools\release_check.py"), "--only", "frozen", "--frozen", $Exe)
        foreach ($p in $Page) {
            $check += @("--page", $p)
        }
        Invoke-Step "Smoke test of the built program" $Python $check
    }

    # -- 3. the installer
    if ($SkipInstaller) {
        Write-Host ""
        Write-Host "Done: $Exe (no installer: -SkipInstaller)." -ForegroundColor Green
        return
    }
    if (-not $InnoSetup) {
        $candidates = @(
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
        )
        $InnoSetup = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $InnoSetup) {
            $cmd = Get-Command ISCC -ErrorAction SilentlyContinue
            if ($cmd) {
                $InnoSetup = $cmd.Source
            }
        }
    }
    if (-not $InnoSetup) {
        Write-Host ""
        Write-Warning ("Inno Setup 6 is not installed, so no installer was made. Install Inno Setup 6.3 " +
                       "or later from https://jrsoftware.org/isdl.php (or: winget install JRSoftware.InnoSetup) " +
                       "and run this script again, or pass -InnoSetup <path to ISCC.exe>.")
        Write-Host "The program itself is complete: $Exe (copy the whole dist\ctgedit folder)." -ForegroundColor Green
        return
    }
    $Icon = Join-Path $Work "ctgedit\generated\ctgedit.ico"
    Invoke-Step "Inno Setup" $InnoSetup @("/Q", "/DAppVersion=$Version", "/DIconFile=$Icon",
                                          (Join-Path $Repo "packaging\installer.iss"))
    $Setup = Join-Path $Dist "ctgedit-$Version-setup.exe"
    Write-Host ""
    Write-Host "Done: $Setup" -ForegroundColor Green
} finally {
    Pop-Location
    $env:PYTHONPATH = $OldPythonPath
}
