# Win11Forge first-logon script. Runs once, hidden, the first time the new account reaches the desktop.
# Reads firstlogon.json next to it (written by the build). Log: C:\Windows\Setup\Scripts\firstlogon.log
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Start-Transcript -Path (Join-Path $here 'firstlogon.log') -Append | Out-Null

$cfg = $null
try { $cfg = Get-Content -Raw (Join-Path $here 'firstlogon.json') | ConvertFrom-Json } catch { Write-Output "No config: $_" }

if ($cfg) {
    # 1. Finish debloat. The Windows engine already removed these offline; the Linux engine can only
    #    block them (Deprovisioned keys), so the actual removal happens here.
    foreach ($pat in @($cfg.AppPatterns)) {
        if (-not $pat) { continue }
        Get-AppxProvisionedPackage -Online | Where-Object DisplayName -like $pat | ForEach-Object {
            Write-Output "Unprovisioning $($_.DisplayName)"
            Remove-AppxProvisionedPackage -Online -PackageName $_.PackageName -ErrorAction SilentlyContinue | Out-Null
        }
        Get-AppxPackage -AllUsers -Name $pat -ErrorAction SilentlyContinue | ForEach-Object {
            Write-Output "Removing $($_.Name)"
            Remove-AppxPackage -Package $_.PackageFullName -AllUsers -ErrorAction SilentlyContinue
        }
    }

    # 2. Keep signing in automatically on every boot (Windows counts AutoLogon down otherwise).
    if ($cfg.PermanentAutoLogon) {
        $wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
        Set-ItemProperty -Path $wl -Name AutoAdminLogon -Value '1' -Type String
        Remove-ItemProperty -Path $wl -Name AutoLogonCount -ErrorAction SilentlyContinue
    }

    # 3. Windows To Go: no hibernation file, so the drive can move between laptops safely.
    if ($cfg.DisableHibernate) { powercfg.exe /hibernate off }
}

# Never let the drive or install encrypt itself behind the user's back.
try { Disable-BitLocker -MountPoint $env:SystemDrive -ErrorAction SilentlyContinue | Out-Null } catch {}

# Ask Windows Update for drivers right away (covers webcams, card readers, touchpads not in the image).
try { Start-Process -FilePath "$env:SystemRoot\System32\UsoClient.exe" -ArgumentList 'StartInteractiveScan' -WindowStyle Hidden } catch {}

Stop-Transcript | Out-Null
