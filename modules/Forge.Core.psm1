#Requires -Version 5.1
<#
    Forge.Core.psm1  -  the engine behind Win11Forge.

    Everything here uses in-box Windows tooling only (DISM cmdlets, dism.exe, reg.exe,
    bcdboot.exe, Storage cmdlets).  The one optional external tool is oscdimg.exe from the
    Windows ADK "Deployment Tools", needed only to write a bootable ISO.

    Public entry points:
        Get-ForgeEditions      -IsoPath <iso or extracted folder>
        Get-ForgeUsbDisks
        New-ForgeUnattend      -Options <hashtable>      (returns XML text)
        Invoke-ForgeBuild      -Options <hashtable>      (does the whole job)
        Set-ForgeLogger        -Logger <scriptblock>
#>

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:ModuleRoot = Split-Path -Parent $PSScriptRoot
$script:Logger     = { param($m) Write-Host $m }

$script:ConfigDir = Join-Path $script:ModuleRoot 'config'
function Get-ForgeConfig([string]$Name) {
    Get-Content -Raw -Encoding UTF8 -Path (Join-Path $script:ConfigDir $Name) | ConvertFrom-Json
}

# ---------------------------------------------------------------------------------------------
#  Logging / helpers
# ---------------------------------------------------------------------------------------------
function Set-ForgeLogger {
    param([Parameter(Mandatory)][scriptblock]$Logger)
    $script:Logger = $Logger
}

function Write-ForgeLog {
    param([string]$Message)
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message
    & $script:Logger $line
}

function Test-ForgeAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-ForgeNative {
    <# Runs a native exe, streams its output to the log, throws on a bad exit code. #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int[]]$OkCodes = @(0)
    )
    Write-ForgeLog ("> {0} {1}" -f (Split-Path $FilePath -Leaf), ($ArgumentList -join ' '))
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $FilePath @ArgumentList 2>&1 | ForEach-Object {
            $t = "$_".Trim()
            # dism draws progress bars with lots of '=' - only keep the useful lines
            if ($t -and $t -notmatch '^\[=*\s*\d') { Write-ForgeLog "  $t" }
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($OkCodes -notcontains $code) {
        throw ("{0} failed with exit code {1}" -f (Split-Path $FilePath -Leaf), $code)
    }
    return $code
}

function Find-ForgeOscdimg {
    $candidates = @(
        (Join-Path $script:ModuleRoot 'tools\oscdimg.exe'),
        "${env:ProgramFiles(x86)}\Windows Kits\10\Assessment and Deployment Kit\Deployment Tools\amd64\Oscdimg\oscdimg.exe",
        "$env:ProgramFiles\Windows Kits\10\Assessment and Deployment Kit\Deployment Tools\amd64\Oscdimg\oscdimg.exe"
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    $cmd = Get-Command oscdimg.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-ForgeHostLocale {
    <# Sensible first-boot defaults copied from the PC running the tool. #>
    $lang = 'en-US'; $kbd = '0409:00000409'; $tz = 'UTC'
    try { $lang = (Get-Culture).Name } catch {}
    try {
        $tip = (Get-WinUserLanguageList)[0].InputMethodTips[0]
        if ($tip) { $kbd = $tip }
    } catch {}
    try { $tz = (Get-TimeZone).Id } catch {}
    [pscustomobject]@{ Locale = $lang; Keyboard = $kbd; TimeZone = $tz }
}

# ---------------------------------------------------------------------------------------------
#  Source media
# ---------------------------------------------------------------------------------------------
function Mount-ForgeSource {
    <# Accepts an .iso (mounted read-only) or an already-extracted folder. Returns media root. #>
    param([Parameter(Mandatory)][string]$IsoPath)
    if (Test-Path $IsoPath -PathType Container) { return (Resolve-Path $IsoPath).Path.TrimEnd('\') }
    if (-not (Test-Path $IsoPath)) { throw "Source not found: $IsoPath" }

    $img = Get-DiskImage -ImagePath $IsoPath
    if (-not $img.Attached) {
        Write-ForgeLog "Mounting $IsoPath"
        $img = Mount-DiskImage -ImagePath $IsoPath -Access ReadOnly -StorageType ISO -PassThru
    }
    $vol = $null
    for ($i = 0; $i -lt 20 -and -not $vol; $i++) {
        $vol = $img | Get-Volume -ErrorAction SilentlyContinue | Where-Object DriveLetter | Select-Object -First 1
        if (-not $vol) { Start-Sleep -Milliseconds 500 }
    }
    if (-not $vol) { throw "Mounted the ISO but Windows gave it no drive letter." }
    return ('{0}:' -f $vol.DriveLetter)
}

function Dismount-ForgeSource {
    param([string]$IsoPath)
    if ($IsoPath -and (Test-Path $IsoPath -PathType Leaf)) {
        try { Dismount-DiskImage -ImagePath $IsoPath | Out-Null } catch {}
    }
}

function Get-ForgeInstallImage {
    param([Parameter(Mandatory)][string]$MediaRoot)
    foreach ($n in 'install.wim', 'install.esd') {
        $p = Join-Path $MediaRoot "sources\$n"
        if (Test-Path $p) { return $p }
    }
    if (Test-Path (Join-Path $MediaRoot 'sources\install.swm')) {
        throw "Split install.swm images are not supported. Use the original Microsoft ISO."
    }
    throw "No sources\install.wim or install.esd found. Is this a Windows 11 ISO?"
}

function Get-ForgeEditions {
    param([Parameter(Mandatory)][string]$IsoPath)
    $root = Mount-ForgeSource -IsoPath $IsoPath
    $img  = Get-ForgeInstallImage -MediaRoot $root
    Get-WindowsImage -ImagePath $img | ForEach-Object {
        $d = Get-WindowsImage -ImagePath $img -Index $_.ImageIndex
        [pscustomobject]@{
            Index     = $d.ImageIndex
            Name      = $d.ImageName
            EditionId = $d.EditionId
            Version   = $d.Version
            Language  = @($d.Languages)[0]
            Arch      = $d.Architecture
            SizeGB    = [math]::Round($d.ImageSize / 1GB, 1)
        }
    }
}

function Get-ForgeUsbDisks {
    <# Only USB disks, never the disk Windows is running from. #>
    Get-Disk | Where-Object { $_.BusType -eq 'USB' -and -not $_.IsBoot -and -not $_.IsSystem } |
        ForEach-Object {
            [pscustomobject]@{
                Number = $_.Number
                Name   = $_.FriendlyName
                SizeGB = [math]::Round($_.Size / 1GB, 1)
                Label  = ('Disk {0}: {1} ({2} GB)' -f $_.Number, $_.FriendlyName, [math]::Round($_.Size / 1GB, 1))
            }
        }
}

# ---------------------------------------------------------------------------------------------
#  Offline registry
# ---------------------------------------------------------------------------------------------
function Test-ForgeWhen {
    param([string]$When, [hashtable]$Options)
    switch ($When) {
        'always'   { return $true }
        'ads'      { return [bool]$Options.RemoveAds }
        'bypass'   { return [bool]$Options.BypassHardware }
        'wtg'      { return $Options.Mode -eq 'WTG' }
        'portable' { return ($Options.Mode -eq 'WTG' -and [bool]$Options.PortableMode) }
        default    { return $false }
    }
}

function Get-ForgeTweaks {
    <# Registry changes from config\tweaks.json that apply to these options. #>
    param([hashtable]$Options)
    $cfg = Get-ForgeConfig 'tweaks.json'
    foreach ($t in $cfg.set) {
        if (Test-ForgeWhen $t.when $Options) {
            [pscustomobject]@{ Hive = $t.hive; Key = $t.key; Name = $t.name; Value = $t.value; Type = 'REG_DWORD' }
        }
    }
}

function Get-ForgeDeleteKeys {
    param([hashtable]$Options)
    $cfg = Get-ForgeConfig 'tweaks.json'
    @($cfg.delete | Where-Object { $_.hive -eq 'SOFT' -and (Test-ForgeWhen $_.when $Options) } | ForEach-Object { $_.key })
}

function Invoke-ForgeOfflineRegistry {
    param(
        [Parameter(Mandatory)][string]$MountDir,
        [Parameter(Mandatory)][object[]]$Tweaks,
        [string[]]$DeleteKeys = @()
    )
    $hives = [ordered]@{
        SOFT = Join-Path $MountDir 'Windows\System32\config\SOFTWARE'
        SYS  = Join-Path $MountDir 'Windows\System32\config\SYSTEM'
        USER = Join-Path $MountDir 'Users\Default\NTUSER.DAT'
    }
    $reg = "$env:SystemRoot\System32\reg.exe"
    # reg.exe writes to stderr for harmless cases (e.g. deleting a key that is not there);
    # under 'Stop' Windows PowerShell 5.1 would turn that into a terminating error.
    $ErrorActionPreference = 'Continue'
    $loaded = @()
    try {
        foreach ($h in $hives.Keys) {
            if (-not ($Tweaks | Where-Object Hive -eq $h) -and -not ($h -eq 'SOFT' -and $DeleteKeys)) { continue }
            if (-not (Test-Path $hives[$h])) { Write-ForgeLog "  (hive $h not present, skipped)"; continue }
            & $reg load "HKLM\FORGE_$h" $hives[$h] | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Could not load offline hive $($hives[$h])" }
            $loaded += $h
        }
        foreach ($tw in $Tweaks) {
            if ($loaded -notcontains $tw.Hive) { continue }
            $path = "HKLM\FORGE_$($tw.Hive)\$($tw.Key)"
            & $reg add $path /v $tw.Name /t $tw.Type /d $tw.Value /f | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-ForgeLog "  warning: could not set $path\$($tw.Name)" }
        }
        foreach ($k in $DeleteKeys) {
            if ($loaded -contains 'SOFT') { & $reg delete "HKLM\FORGE_SOFT\$k" /f 2>$null | Out-Null }
        }
        Write-ForgeLog ("  applied {0} registry settings" -f @($Tweaks).Count)
    } finally {
        [GC]::Collect(); [GC]::WaitForPendingFinalizers()
        foreach ($h in $loaded) {
            for ($i = 0; $i -lt 5; $i++) {
                & $reg unload "HKLM\FORGE_$h" 2>$null | Out-Null
                if ($LASTEXITCODE -eq 0) { break }
                Start-Sleep -Seconds 1
            }
        }
    }
}

# ---------------------------------------------------------------------------------------------
#  Answer file - this is what makes the first boot go straight to the desktop
# ---------------------------------------------------------------------------------------------
function Expand-ForgeTemplate {
    <# Keeps or drops <!--#IF NAME--> ... <!--#ENDIF NAME--> blocks, then fills {{TOKENS}}. #>
    param([string]$Text, [hashtable]$Flags, [hashtable]$Tokens)
    $rx = '(?s)<!--#IF (\w+)-->\r?\n?(.*?)<!--#ENDIF \1-->\r?\n?'
    while ($Text -match $rx) {
        $Text = [regex]::Replace($Text, $rx, {
            param($m)
            if ($Flags[$m.Groups[1].Value]) { $m.Groups[2].Value } else { '' }
        })
    }
    foreach ($k in $Tokens.Keys) { $Text = $Text.Replace("{{$k}}", [string]$Tokens[$k]) }
    return $Text
}

function New-ForgeUnattend {
    <#
      Options used: UserName, Password, ComputerName, Locale, Keyboard, TimeZone, EditionId,
                    AutoLogon, WipeDisk0, BypassHardware, Mode ('ISO' or 'WTG')
    #>
    param([Parameter(Mandatory)][hashtable]$Options)
    $keys = (Get-ForgeConfig 'editions.json').genericKeys
    $key  = $null
    if ($Options.EditionId -and ($keys.PSObject.Properties.Name -contains [string]$Options.EditionId)) {
        $key = $keys.([string]$Options.EditionId)
    }
    $esc = { param($s) [Security.SecurityElement]::Escape([string]$s) }
    $pc  = if ([string]::IsNullOrWhiteSpace($Options.ComputerName)) { '*' } else { & $esc $Options.ComputerName }
    $flags = @{
        ISO       = ($Options.Mode -eq 'ISO')
        WIPE      = [bool]$Options.WipeDisk0
        KEY       = [bool]$key
        BYPASS    = [bool]$Options.BypassHardware
        AUTOLOGON = [bool]$Options.AutoLogon
    }
    $tokens = @{
        LOCALE = & $esc $Options.Locale;  KEYBOARD = & $esc $Options.Keyboard; TIMEZONE = & $esc $Options.TimeZone
        USER   = & $esc $Options.UserName; PASSWORD = & $esc $Options.Password; COMPUTERNAME = $pc
        PRODUCTKEY = $key
    }
    $tpl = Get-Content -Raw -Encoding UTF8 -Path (Join-Path $script:ConfigDir 'unattend.template.xml')
    return (Expand-ForgeTemplate -Text $tpl -Flags $flags -Tokens $tokens)
}

# ---------------------------------------------------------------------------------------------
#  Image servicing steps
# ---------------------------------------------------------------------------------------------
function Remove-ForgeApps {
    param([string]$MountDir, [string[]]$Patterns)
    if (-not $Patterns) { Write-ForgeLog "No apps selected for removal."; return }
    $pkgs = Get-AppxProvisionedPackage -Path $MountDir
    $removed = 0
    foreach ($p in $pkgs) {
        foreach ($pat in $Patterns) {
            if ($p.DisplayName -like $pat) {
                try {
                    Remove-AppxProvisionedPackage -Path $MountDir -PackageName $p.PackageName | Out-Null
                    Write-ForgeLog "  removed $($p.DisplayName)"
                    $removed++
                } catch { Write-ForgeLog "  could not remove $($p.DisplayName): $($_.Exception.Message)" }
                break
            }
        }
    }
    Write-ForgeLog "Removed $removed provisioned apps."
}

function Add-ForgeDrivers {
    param([string]$MountDir, [string[]]$DriverDirs)
    foreach ($d in $DriverDirs) {
        if (-not $d -or -not (Test-Path $d)) { continue }
        $infCount = @(Get-ChildItem -Path $d -Filter *.inf -Recurse -ErrorAction SilentlyContinue).Count
        if ($infCount -eq 0) { Write-ForgeLog "  no .inf files in $d (extract .exe/.zip driver packs first)"; continue }
        Write-ForgeLog "  adding $infCount driver(s) from $d"
        try {
            Add-WindowsDriver -Path $MountDir -Driver $d -Recurse -ErrorAction Stop | Out-Null
        } catch {
            # One bad .inf should not sink the build: retry one by one
            Write-ForgeLog "  bulk add failed ($($_.Exception.Message)); adding one by one"
            Get-ChildItem -Path $d -Filter *.inf -Recurse | ForEach-Object {
                try { Add-WindowsDriver -Path $MountDir -Driver $_.FullName -ErrorAction Stop | Out-Null }
                catch { Write-ForgeLog "    skipped $($_.Name)" }
            }
        }
    }
}

function Add-ForgeUpdates {
    param([string]$MountDir, [string]$UpdatesDir)
    if (-not $UpdatesDir -or -not (Test-Path $UpdatesDir)) { return }
    $files = Get-ChildItem -Path $UpdatesDir -Include *.msu, *.cab -Recurse | Sort-Object Name
    foreach ($f in $files) {
        Write-ForgeLog "  integrating update $($f.Name)"
        try { Add-WindowsPackage -Path $MountDir -PackagePath $f.FullName -ErrorAction Stop | Out-Null }
        catch { Write-ForgeLog "  update failed: $($_.Exception.Message)" }
    }
}

function Copy-ForgeFirstLogon {
    param([string]$MountDir, [hashtable]$Options, [string[]]$AppPatterns)
    $dst = Join-Path $MountDir 'Windows\Setup\Scripts'
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Copy-Item -Path (Join-Path $script:ModuleRoot 'scripts\firstlogon.ps1') -Destination $dst -Force
    $cfg = [ordered]@{
        UserName         = $Options.UserName
        PermanentAutoLogon = [bool]$Options.AutoLogon
        DisableHibernate = ($Options.Mode -eq 'WTG')
        AppPatterns      = $AppPatterns
    }
    # No password in this file - autologon already holds it.
    $cfg | ConvertTo-Json | Set-Content -Path (Join-Path $dst 'firstlogon.json') -Encoding UTF8
}

function Get-ForgeDriverDirs {
    param([hashtable]$Options, [string]$WorkDir)
    $dirs = @()
    if ($Options.DriverDirs) { $dirs += @($Options.DriverDirs | Where-Object { $_ }) }
    if ($Options.IncludeHostDrivers) {
        $hostDir = Join-Path $WorkDir 'host-drivers'
        if (-not (Test-Path $hostDir)) {
            Write-ForgeLog "Exporting this PC's third-party drivers..."
            New-Item -ItemType Directory -Path $hostDir -Force | Out-Null
            Export-WindowsDriver -Online -Destination $hostDir | Out-Null
        }
        $dirs += $hostDir
    }
    return $dirs
}

function Clear-ForgeStaleMounts {
    Get-WindowsImage -Mounted -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*-forge*' } | ForEach-Object {
        Write-ForgeLog "Discarding stale mount $($_.Path)"
        try { Dismount-WindowsImage -Path $_.Path -Discard | Out-Null } catch {}
    }
    try { Clear-WindowsCorruptMountPoint | Out-Null } catch {}
}

# ---------------------------------------------------------------------------------------------
#  Outputs
# ---------------------------------------------------------------------------------------------
function Update-ForgeBootWim {
    <# Adds drivers to Windows Setup so it can see NVMe drives behind Intel VMD/RST, USB3 hubs, etc. #>
    param([string]$BootWim, [string]$WorkDir, [string[]]$DriverDirs)
    if (-not $DriverDirs) { return }
    attrib -r $BootWim
    $mnt = Join-Path $WorkDir 'mount-boot-forge'
    if (Test-Path $mnt) { Remove-Item $mnt -Recurse -Force }
    New-Item -ItemType Directory -Path $mnt -Force | Out-Null
    $count = @(Get-WindowsImage -ImagePath $BootWim).Count
    $index = if ($count -ge 2) { 2 } else { 1 }   # index 2 = Windows Setup
    Write-ForgeLog "Adding drivers to Windows Setup (boot.wim index $index)"
    Mount-WindowsImage -ImagePath $BootWim -Index $index -Path $mnt | Out-Null
    try {
        Add-ForgeDrivers -MountDir $mnt -DriverDirs $DriverDirs
        Dismount-WindowsImage -Path $mnt -Save | Out-Null
    } catch {
        Dismount-WindowsImage -Path $mnt -Discard | Out-Null
        throw
    }
}

function New-ForgeIso {
    param([hashtable]$Options, [string]$MediaRoot, [string]$WorkDir, [string]$FinalImage, [string[]]$DriverDirs)
    $osc = Find-ForgeOscdimg
    if (-not $osc) {
        throw "oscdimg.exe not found. Install the Windows ADK 'Deployment Tools' (button in the app, or: winget install Microsoft.WindowsADK) or drop oscdimg.exe into the tools folder."
    }
    $isoDir = Join-Path $WorkDir 'iso'
    if (Test-Path $isoDir) { Remove-Item $isoDir -Recurse -Force }
    Write-ForgeLog "Copying installer files"
    Invoke-ForgeNative -FilePath "$env:SystemRoot\System32\robocopy.exe" `
        -ArgumentList @($MediaRoot, $isoDir, '/E', '/XF', 'install.wim', 'install.esd', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1') `
        -OkCodes @(0, 1, 2, 3, 4, 5, 6, 7) | Out-Null
    attrib -r "$isoDir\*.*" /s /d | Out-Null

    $ext = [IO.Path]::GetExtension($FinalImage)
    Move-Item -Path $FinalImage -Destination (Join-Path $isoDir "sources\install$ext") -Force

    Update-ForgeBootWim -BootWim (Join-Path $isoDir 'sources\boot.wim') -WorkDir $WorkDir -DriverDirs $DriverDirs

    $xml = New-ForgeUnattend -Options $Options
    Set-Content -Path (Join-Path $isoDir 'autounattend.xml') -Value $xml -Encoding UTF8

    $etfs = Join-Path $isoDir 'boot\etfsboot.com'
    $efi  = Join-Path $isoDir 'efi\microsoft\boot\efisys_noprompt.bin'   # no "press any key" prompt
    if (-not (Test-Path $efi)) { $efi = Join-Path $isoDir 'efi\microsoft\boot\efisys.bin' }
    $out = $Options.OutputIso
    $outDir = Split-Path $out -Parent
    if ($outDir) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    if (Test-Path $out) { Remove-Item $out -Force }

    $label = 'WIN11_FORGE'
    if (Test-Path $etfs) {
        $boot = "-bootdata:2#p0,e,b$etfs#pEF,e,b$efi"
    } else {
        $boot = "-bootdata:1#pEF,e,b$efi"
    }
    Write-ForgeLog "Writing ISO $out"
    Invoke-ForgeNative -FilePath $osc -ArgumentList @('-m', '-o', '-u2', '-udfver102', "-l$label", $boot, $isoDir, $out) | Out-Null
    Remove-Item $isoDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-ForgeLog ("ISO ready: {0} ({1:N1} GB)" -f $out, ((Get-Item $out).Length / 1GB))
}

function New-ForgeWindowsToGo {
    param([hashtable]$Options, [string]$WorkDir, [string]$Image)
    $n = [int]$Options.DiskNumber
    $disk = Get-Disk -Number $n
    if ($disk.IsBoot -or $disk.IsSystem) { throw "Disk $n is the disk this PC is running from. Refusing." }
    if ($disk.BusType -ne 'USB' -and -not $Options.AllowNonUsb) { throw "Disk $n is not a USB disk. Refusing." }
    if ($disk.Size -lt 32GB) { throw "Disk $n is smaller than 32 GB - too small for Windows 11." }

    Write-ForgeLog ("Wiping disk {0}: {1} ({2:N0} GB)" -f $n, $disk.FriendlyName, ($disk.Size / 1GB))
    if ($disk.IsOffline)  { Set-Disk -Number $n -IsOffline $false }
    if ($disk.IsReadOnly) { Set-Disk -Number $n -IsReadOnly $false }
    if ($disk.PartitionStyle -ne 'RAW') { Clear-Disk -Number $n -RemoveData -RemoveOEM -Confirm:$false }

    # MBR + FAT32 system partition boots on both UEFI and legacy BIOS laptops and works on
    # "removable" flash drives as well as external SSDs.
    Initialize-Disk -Number $n -PartitionStyle MBR
    $sys = New-Partition -DiskNumber $n -Size 350MB -IsActive -AssignDriveLetter
    Format-Volume -Partition $sys -FileSystem FAT32 -NewFileSystemLabel 'SYSTEM' -Confirm:$false | Out-Null
    $win = New-Partition -DiskNumber $n -UseMaximumSize -AssignDriveLetter
    Format-Volume -Partition $win -FileSystem NTFS -NewFileSystemLabel 'Windows' -Confirm:$false | Out-Null
    $sys = Get-Partition -DiskNumber $n -PartitionNumber $sys.PartitionNumber
    $win = Get-Partition -DiskNumber $n -PartitionNumber $win.PartitionNumber
    $S = "$($sys.DriveLetter):"; $W = "$($win.DriveLetter):"
    if ($S -eq ':' -or $W -eq ':') { throw "Windows did not assign drive letters to the new partitions." }

    Write-ForgeLog "Applying Windows to $W (this is the long part on a flash drive)"
    $applyArgs = @('/Apply-Image', "/ImageFile:$Image", '/Index:1', "/ApplyDir:$W\")
    if ($Options.CompactOS) { $applyArgs += '/Compact' }
    Invoke-ForgeNative -FilePath "$env:SystemRoot\System32\dism.exe" -ArgumentList $applyArgs | Out-Null

    $panther = Join-Path $W 'Windows\Panther'
    New-Item -ItemType Directory -Path $panther -Force | Out-Null
    Set-Content -Path (Join-Path $panther 'unattend.xml') -Value (New-ForgeUnattend -Options $Options) -Encoding UTF8

    Write-ForgeLog "Writing boot files"
    Invoke-ForgeNative -FilePath "$env:SystemRoot\System32\bcdboot.exe" -ArgumentList @("$W\Windows", '/s', $S, '/f', 'ALL') | Out-Null

    # Hide the system partition letter again
    try { Remove-PartitionAccessPath -DiskNumber $n -PartitionNumber $sys.PartitionNumber -AccessPath "$S\" } catch {}
    Write-ForgeLog "Windows To Go drive is ready. Boot the laptop from USB (F12/F9/Esc boot menu)."
}

# ---------------------------------------------------------------------------------------------
#  The whole pipeline
# ---------------------------------------------------------------------------------------------
function Invoke-ForgeBuild {
    <#
      Options:
        IsoPath, ImageIndex, Mode ('ISO'|'WTG'), WorkDir,
        RemoveApps (string[] patterns), RemoveAds, CleanupImage, UseEsd,
        DriverDirs (string[]), IncludeHostDrivers, UpdatesDir,
        UserName, Password, ComputerName, Locale, Keyboard, TimeZone, AutoLogon, BypassHardware,
        ISO:  OutputIso, WipeDisk0
        WTG:  DiskNumber, CompactOS, PortableMode
    #>
    param([Parameter(Mandatory)][hashtable]$Options)

    if (-not (Test-ForgeAdmin)) { throw "Run Win11Forge as Administrator." }
    if ([string]::IsNullOrWhiteSpace($Options.UserName)) { throw "A user name is required." }
    if ($Options.UserName -match '[\\/\[\]:;|=,+*?<>"@]') { throw "User name has characters Windows does not allow." }
    if ($Options.UserName -in @('Administrator', 'Guest', 'DefaultAccount', 'WDAGUtilityAccount')) { throw "Pick a different user name; '$($Options.UserName)' is reserved." }
    if ($null -eq $Options.Password) { $Options.Password = '' }
    if ($null -eq $Options.RemoveApps) {
        $Options.RemoveApps = @((Get-ForgeConfig 'apps.json').apps | Where-Object { $_.remove } | ForEach-Object { $_.match })
    }
    if (-not $Options.ImageIndex) { throw "Pick a Windows edition." }

    $work = $Options.WorkDir
    if (-not $work) { $work = Join-Path $env:SystemDrive 'Win11Forge-work' }
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $drive = (Get-Item $work).PSDrive
    if ($drive -and $drive.Free -lt 25GB) {
        throw ("Work folder drive needs about 25 GB free (has {0:N0} GB)." -f ($drive.Free / 1GB))
    }

    $sw = [Diagnostics.Stopwatch]::StartNew()
    $mountDir = Join-Path $work 'mount-forge'
    $tmpWim   = Join-Path $work 'stage.wim'
    $dism     = "$env:SystemRoot\System32\dism.exe"
    $mounted  = $false

    Clear-ForgeStaleMounts
    try {
        $root = Mount-ForgeSource -IsoPath $Options.IsoPath
        $src  = Get-ForgeInstallImage -MediaRoot $root
        $info = Get-WindowsImage -ImagePath $src -Index $Options.ImageIndex
        $Options.EditionId = $info.EditionId
        Write-ForgeLog ("Source: {0}  build {1}  ({2})" -f $info.ImageName, $info.Version, $info.EditionId)
        if ($info.Architecture -ne 9) { Write-ForgeLog "Warning: image is not x64; the answer file targets amd64." }

        # 1. Pull the chosen edition out on its own (also converts .esd to .wim)
        if (Test-Path $tmpWim) { Remove-Item $tmpWim -Force }
        Write-ForgeLog "Step 1/6  Extracting edition"
        Invoke-ForgeNative -FilePath $dism -ArgumentList @('/Export-Image', "/SourceImageFile:$src", "/SourceIndex:$($Options.ImageIndex)", "/DestinationImageFile:$tmpWim", '/Compress:max', '/CheckIntegrity') | Out-Null

        # 2. Mount
        Write-ForgeLog "Step 2/6  Mounting image"
        if (Test-Path $mountDir) { Remove-Item $mountDir -Recurse -Force }
        New-Item -ItemType Directory -Path $mountDir -Force | Out-Null
        Mount-WindowsImage -ImagePath $tmpWim -Index 1 -Path $mountDir | Out-Null
        $mounted = $true

        # 3. Debloat
        Write-ForgeLog "Step 3/6  Removing bloat apps"
        Remove-ForgeApps -MountDir $mountDir -Patterns $Options.RemoveApps
        Write-ForgeLog "Applying registry settings (ads, OOBE, power)"
        $deleteKeys = Get-ForgeDeleteKeys -Options $Options
        Invoke-ForgeOfflineRegistry -MountDir $mountDir -Tweaks (Get-ForgeTweaks -Options $Options) -DeleteKeys $deleteKeys

        # 4. Drivers + updates
        Write-ForgeLog "Step 4/6  Drivers and updates"
        $driverDirs = Get-ForgeDriverDirs -Options $Options -WorkDir $work
        Add-ForgeDrivers -MountDir $mountDir -DriverDirs $driverDirs
        Add-ForgeUpdates -MountDir $mountDir -UpdatesDir $Options.UpdatesDir
        Copy-ForgeFirstLogon -MountDir $mountDir -Options $Options -AppPatterns $Options.RemoveApps

        # 5. Shrink + save
        if ($Options.CleanupImage) {
            Write-ForgeLog "Step 5/6  Cleaning component store (slow, saves space)"
            Invoke-ForgeNative -FilePath $dism -ArgumentList @("/Image:$mountDir", '/Cleanup-Image', '/StartComponentCleanup', '/ResetBase') -OkCodes @(0, 3010) | Out-Null
        } else {
            Write-ForgeLog "Step 5/6  (component cleanup skipped)"
        }
        Write-ForgeLog "Saving image"
        Dismount-WindowsImage -Path $mountDir -Save | Out-Null
        $mounted = $false

        $ext   = if ($Options.Mode -eq 'ISO' -and $Options.UseEsd) { '.esd' } else { '.wim' }
        $comp  = if ($ext -eq '.esd') { 'recovery' } else { 'max' }
        $final = Join-Path $work "final$ext"
        if (Test-Path $final) { Remove-Item $final -Force }
        Write-ForgeLog "Re-compressing image ($comp)"
        Invoke-ForgeNative -FilePath $dism -ArgumentList @('/Export-Image', "/SourceImageFile:$tmpWim", '/SourceIndex:1', "/DestinationImageFile:$final", "/Compress:$comp") | Out-Null
        Remove-Item $tmpWim -Force -ErrorAction SilentlyContinue
        Write-ForgeLog ("Image size now {0:N2} GB" -f ((Get-Item $final).Length / 1GB))

        # 6. Output
        Write-ForgeLog "Step 6/6  Writing output"
        if ($Options.Mode -eq 'ISO') {
            New-ForgeIso -Options $Options -MediaRoot $root -WorkDir $work -FinalImage $final -DriverDirs $driverDirs
        } elseif ($Options.Mode -eq 'WTG') {
            New-ForgeWindowsToGo -Options $Options -WorkDir $work -Image $final
            if (-not $Options.KeepImage) { Remove-Item $final -Force -ErrorAction SilentlyContinue }
        } else {
            throw "Unknown mode '$($Options.Mode)'"
        }
        Write-ForgeLog ("Done in {0:N0} minutes." -f $sw.Elapsed.TotalMinutes)
    } finally {
        if ($mounted) {
            Write-ForgeLog "Discarding mounted image after error"
            try { Dismount-WindowsImage -Path $mountDir -Discard | Out-Null } catch {}
        }
        Dismount-ForgeSource -IsoPath $Options.IsoPath
    }
}

Export-ModuleMember -Function Set-ForgeLogger, Write-ForgeLog, Test-ForgeAdmin, Find-ForgeOscdimg, Get-ForgeHostLocale,
    Get-ForgeEditions, Get-ForgeUsbDisks, New-ForgeUnattend, Expand-ForgeTemplate, Get-ForgeTweaks, Invoke-ForgeBuild,
    Dismount-ForgeSource
