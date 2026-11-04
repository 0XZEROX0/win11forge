#Requires -Version 5.1
<#
    Win11Forge - Windows GUI.
    Start it with Start-Win11Forge.cmd (asks for admin), or:
        powershell -ExecutionPolicy Bypass -STA -File .\Win11Forge.ps1
    Headless:
        powershell -ExecutionPolicy Bypass -File .\Win11Forge.ps1 -Config .\build.json
    USB installer from a finished ISO (as Administrator):
        powershell -ExecutionPolicy Bypass -File .\Win11Forge.ps1 -ListUsb
        powershell -ExecutionPolicy Bypass -File .\Win11Forge.ps1 -WriteUsb .\Win11-Forge.iso -Disk 2
#>
param([string]$Config, [string]$WriteUsb, [int]$Disk = -1, [switch]$ListUsb)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$ModulePath = Join-Path $Root 'modules\Forge.Core.psm1'
Import-Module $ModulePath -Force

# ---------------------------------------------------------------------------------- headless mode
if ($Config) {
    $o = @{}
    (Get-Content -Raw $Config | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $o[$_.Name] = $_.Value }
    Invoke-ForgeBuild -Options $o
    return
}
if ($ListUsb) {
    $d = @(Get-ForgeUsbDisks)
    if ($d.Count) { $d | ForEach-Object { $_.Label } } else { 'No USB drives found.' }
    return
}
if ($WriteUsb) {
    $d = @(Get-ForgeUsbDisks | Where-Object Number -eq $Disk)
    if (-not $d.Count) { throw "Pass -Disk with one of these USB drives: " + ((@(Get-ForgeUsbDisks) | ForEach-Object { $_.Number }) -join ', ') }
    $ans = Read-Host "Everything on $($d[0].Label) will be erased. Type YES to continue"
    if ($ans -cne 'YES') { 'Cancelled.'; return }
    Write-ForgeIsoToUsb -IsoPath $WriteUsb -DiskNumber $Disk
    return
}

if (-not (Test-ForgeAdmin)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-File', "`"$PSCommandPath`"")
    return
}

Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Windows.Forms

[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Win11Forge - lighter Windows 11, straight to the desktop" Width="1120" Height="780"
        WindowStartupLocation="CenterScreen" Background="#F5F6F8" FontFamily="Segoe UI" FontSize="13">
  <Window.Resources>
    <Style TargetType="GroupBox">
      <Setter Property="Margin" Value="0,0,0,10"/>
      <Setter Property="Padding" Value="10,6"/>
      <Setter Property="Background" Value="White"/>
      <Setter Property="BorderBrush" Value="#D8DCE3"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
    </Style>
    <Style TargetType="TextBox"><Setter Property="Padding" Value="4,3"/><Setter Property="FontWeight" Value="Normal"/></Style>
    <Style TargetType="CheckBox"><Setter Property="Margin" Value="0,3"/><Setter Property="FontWeight" Value="Normal"/></Style>
    <Style TargetType="RadioButton"><Setter Property="Margin" Value="0,3"/><Setter Property="FontWeight" Value="Normal"/></Style>
    <Style TargetType="TextBlock"><Setter Property="FontWeight" Value="Normal"/><Setter Property="VerticalAlignment" Value="Center"/></Style>
    <Style TargetType="Button"><Setter Property="Padding" Value="12,4"/><Setter Property="Margin" Value="6,0,0,0"/><Setter Property="FontWeight" Value="Normal"/></Style>
  </Window.Resources>
  <Grid Margin="14">
    <Grid.ColumnDefinitions>
      <ColumnDefinition Width="560"/>
      <ColumnDefinition Width="14"/>
      <ColumnDefinition Width="*"/>
    </Grid.ColumnDefinitions>
    <ScrollViewer Grid.Column="0" VerticalScrollBarVisibility="Auto">
      <StackPanel>
        <GroupBox Header="1. Windows 11 source">
          <StackPanel>
            <DockPanel>
              <Button Name="BtnBrowseIso" DockPanel.Dock="Right" Content="Browse..."/>
              <TextBox Name="TxtIso" ToolTip="Official Windows 11 ISO, or a folder with its extracted files"/>
            </DockPanel>
            <DockPanel Margin="0,6,0,0">
              <Button Name="BtnDownload" DockPanel.Dock="Right" Content="Get latest official ISO"/>
              <ComboBox Name="CmbEdition" ToolTip="Edition inside the ISO"/>
            </DockPanel>
          </StackPanel>
        </GroupBox>
        <GroupBox Header="2. Debloat">
          <StackPanel>
            <CheckBox Name="ChkAds" IsChecked="True" Content="Remove ads, tips, suggested apps, Widgets, Copilot, web search and telemetry"/>
            <CheckBox Name="ChkCleanup" IsChecked="True" Content="Clean up the component store (smaller image, takes longer)"/>
            <TextBlock Margin="0,6,0,2" Text="Apps to remove (Camera and Sound Recorder are kept so webcam and mic work):"/>
            <ListBox Name="LstApps" Height="170">
              <ListBox.ItemTemplate>
                <DataTemplate><CheckBox IsChecked="{Binding Remove}" Content="{Binding Name}"/></DataTemplate>
              </ListBox.ItemTemplate>
            </ListBox>
          </StackPanel>
        </GroupBox>
        <GroupBox Header="3. Drivers (for wide laptop support)">
          <StackPanel>
            <DockPanel>
              <Button Name="BtnDrivers" DockPanel.Dock="Right" Content="Browse..."/>
              <TextBox Name="TxtDrivers" ToolTip="Folder of extracted driver packs (.inf). Several folders: separate with ;"/>
            </DockPanel>
            <CheckBox Name="ChkHostDrivers" Content="Also include all drivers from this PC"/>
            <DockPanel Margin="0,4,0,0">
              <Button Name="BtnUpdates" DockPanel.Dock="Right" Content="Browse..."/>
              <TextBox Name="TxtUpdates" ToolTip="Optional folder of .msu/.cab cumulative updates to slipstream"/>
            </DockPanel>
            <TextBlock TextWrapping="Wrap" Foreground="#5B6472" Margin="0,4,0,0"
              Text="Windows already ships drivers for most touchpads, touchscreens, USB webcams and HD audio, and fetches the rest from Windows Update on first boot. Add Intel RST/VMD storage, Wi-Fi and OEM driver packs here so they work offline."/>
          </StackPanel>
        </GroupBox>
        <GroupBox Header="4. First boot (no setup screens)">
          <Grid>
            <Grid.ColumnDefinitions><ColumnDefinition Width="120"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions>
            <Grid.RowDefinitions>
              <RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/><RowDefinition/>
            </Grid.RowDefinitions>
            <TextBlock Grid.Row="0" Text="User name"/>     <TextBox Grid.Row="0" Grid.Column="1" Name="TxtUser" Text="User" Margin="0,2"/>
            <TextBlock Grid.Row="1" Text="Password"/>      <PasswordBox Grid.Row="1" Grid.Column="1" Name="TxtPass" Margin="0,2" Padding="4,3" ToolTip="Leave empty for no password"/>
            <TextBlock Grid.Row="2" Text="Computer name"/> <TextBox Grid.Row="2" Grid.Column="1" Name="TxtPc" Margin="0,2" ToolTip="Empty = random name"/>
            <TextBlock Grid.Row="3" Text="Language"/>      <TextBox Grid.Row="3" Grid.Column="1" Name="TxtLocale" Margin="0,2" ToolTip="e.g. en-US, ar-EG. Must be a language included in the ISO"/>
            <TextBlock Grid.Row="4" Text="Keyboard"/>      <TextBox Grid.Row="4" Grid.Column="1" Name="TxtKbd" Margin="0,2" ToolTip="e.g. 0409:00000409 (US). Several: 0409:00000409;0401:00000401"/>
            <TextBlock Grid.Row="5" Text="Time zone"/>     <ComboBox Grid.Row="5" Grid.Column="1" Name="CmbTz" Margin="0,2" IsEditable="True"/>
            <StackPanel Grid.Row="6" Grid.ColumnSpan="2">
              <CheckBox Name="ChkAutoLogon" IsChecked="True" Content="Sign in automatically every boot"/>
              <CheckBox Name="ChkBypass" IsChecked="True" Content="Allow older laptops (skip TPM / Secure Boot / CPU / RAM checks)"/>
            </StackPanel>
          </Grid>
        </GroupBox>
        <GroupBox Header="5. Output">
          <StackPanel>
            <RadioButton Name="RadIso" GroupName="Out" IsChecked="True" Content="Bootable ISO (install on any laptop)"/>
            <StackPanel Name="PnlIso" Margin="22,2,0,6">
              <DockPanel>
                <Button Name="BtnOutIso" DockPanel.Dock="Right" Content="Save as..."/>
                <TextBox Name="TxtOutIso"/>
              </DockPanel>
              <CheckBox Name="ChkEsd" Content="Maximum compression (ESD, smaller ISO, slower build)"/>
              <CheckBox Name="ChkWipe" Content="Fully hands-off: wipe the laptop's disk 0 without asking" Foreground="#B42318"/>
            </StackPanel>
            <RadioButton Name="RadWtg" GroupName="Out" Content="Windows To Go (run Windows from a USB flash drive or external SSD)"/>
            <StackPanel Name="PnlWtg" Margin="22,2,0,0" IsEnabled="False">
              <DockPanel>
                <Button Name="BtnRefreshUsb" DockPanel.Dock="Right" Content="Refresh"/>
                <ComboBox Name="CmbUsb"/>
              </DockPanel>
              <CheckBox Name="ChkCompact" IsChecked="True" Content="Compact OS (saves ~3 GB, recommended for flash drives)"/>
              <CheckBox Name="ChkPortable" Content="Strict Windows To Go mode (PortableOperatingSystem flag)"/>
            </StackPanel>
            <DockPanel Margin="0,8,0,0">
              <TextBlock Text="Work folder  " DockPanel.Dock="Left"/>
              <TextBox Name="TxtWork" ToolTip="Needs about 25 GB free"/>
            </DockPanel>
          </StackPanel>
        </GroupBox>
      </StackPanel>
    </ScrollViewer>
    <DockPanel Grid.Column="2">
      <StackPanel DockPanel.Dock="Bottom" Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,10,0,0">
        <Button Name="BtnUnattend" Content="Save answer file only"/>
        <Button Name="BtnUsb" Content="Write ISO to USB..."/>
        <Button Name="BtnBuild" Content="Build" Padding="28,6" FontWeight="SemiBold" Background="#2563EB" Foreground="White" BorderBrush="#2563EB"/>
      </StackPanel>
      <ProgressBar Name="Prog" DockPanel.Dock="Bottom" Height="6" Margin="0,8,0,0"/>
      <TextBlock DockPanel.Dock="Top" Text="Log" FontWeight="SemiBold" Margin="0,0,0,4"/>
      <TextBox Name="TxtLog" IsReadOnly="True" FontFamily="Consolas" FontSize="12" TextWrapping="NoWrap"
               VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Auto" Background="#0F172A" Foreground="#E2E8F0"/>
    </DockPanel>
  </Grid>
</Window>
'@

$win = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $xaml))
$ui = @{}
$xaml.SelectNodes('//*[@Name]') | ForEach-Object { $ui[$_.Name] = $win.FindName($_.Name) }

function Add-Log([string]$m) { $ui.TxtLog.AppendText($m + "`r`n"); $ui.TxtLog.ScrollToEnd() }
Set-ForgeLogger -Logger { param($m) Add-Log $m }

# ------------------------------------------------------------------------------------- defaults
if (-not ('ForgeApp' -as [type])) {
    Add-Type -TypeDefinition 'public class ForgeApp { public string Name { get; set; } public string Match { get; set; } public bool Remove { get; set; } }'
}
$apps = (Get-Content -Raw (Join-Path $Root 'config\apps.json') | ConvertFrom-Json).apps | ForEach-Object {
    $a = New-Object ForgeApp; $a.Name = $_.name; $a.Match = $_.match; $a.Remove = [bool]$_.remove; $a
}
$appList = New-Object System.Collections.ObjectModel.ObservableCollection[object]
$apps | ForEach-Object { $appList.Add($_) }
$ui.LstApps.ItemsSource = $appList

$loc = Get-ForgeHostLocale
$ui.TxtLocale.Text = $loc.Locale
$ui.TxtKbd.Text    = $loc.Keyboard
[TimeZoneInfo]::GetSystemTimeZones() | ForEach-Object { [void]$ui.CmbTz.Items.Add($_.Id) }
$ui.CmbTz.Text     = $loc.TimeZone
$ui.TxtWork.Text   = Join-Path $env:SystemDrive 'Win11Forge-work'
$ui.TxtOutIso.Text = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Win11-Forge.iso'
if (-not (Find-ForgeOscdimg)) {
    Add-Log "Note: oscdimg.exe (Windows ADK Deployment Tools) was not found. It is only needed for ISO output."
    Add-Log "      Install with:  winget install Microsoft.WindowsADK   (choose 'Deployment Tools' only)"
}

# ------------------------------------------------------------------------------------- helpers
function Select-Folder([string]$start) {
    $d = New-Object System.Windows.Forms.FolderBrowserDialog
    if ($start -and (Test-Path $start)) { $d.SelectedPath = $start }
    if ($d.ShowDialog() -eq 'OK') { return $d.SelectedPath }
    return $null
}

function Update-Editions {
    $ui.CmbEdition.Items.Clear()
    if (-not $ui.TxtIso.Text) { return }
    try {
        $win.Cursor = 'Wait'
        $script:Editions = @(Get-ForgeEditions -IsoPath $ui.TxtIso.Text)
        foreach ($e in $script:Editions) {
            [void]$ui.CmbEdition.Items.Add(('{0}. {1}  -  {2}  build {3}  [{4}]' -f $e.Index, $e.Name, $e.EditionId, $e.Version, $e.Language))
        }
        $pro = [array]::FindIndex([object[]]$script:Editions, [Predicate[object]] { param($x) $x.EditionId -eq 'Professional' })
        $ui.CmbEdition.SelectedIndex = [math]::Max(0, $pro)
        $lang = $script:Editions[0].Language
        if ($lang -and $ui.TxtLocale.Text -ne $lang) { Add-Log "ISO language is $lang (you can still pick another display language only if it is in the ISO)." }
        Add-Log ("Found {0} editions." -f $script:Editions.Count)
    } catch {
        Add-Log "Could not read the ISO: $($_.Exception.Message)"
    } finally {
        $win.Cursor = $null
        if ($ui.TxtIso.Text -like '*.iso') { Dismount-ForgeSource -IsoPath $ui.TxtIso.Text }
    }
}

function Update-Usb {
    $ui.CmbUsb.Items.Clear()
    $script:UsbDisks = @(Get-ForgeUsbDisks)
    foreach ($d in $script:UsbDisks) { [void]$ui.CmbUsb.Items.Add($d.Label) }
    if ($script:UsbDisks.Count) { $ui.CmbUsb.SelectedIndex = 0 } else { Add-Log "No USB disks found. Plug in the flash drive / SSD and press Refresh." }
}

function Get-Options {
    $o = @{
        IsoPath        = $ui.TxtIso.Text
        ImageIndex     = $null
        Mode           = if ($ui.RadIso.IsChecked) { 'ISO' } else { 'WTG' }
        WorkDir        = $ui.TxtWork.Text
        RemoveApps     = @($appList | Where-Object Remove | ForEach-Object Match)
        RemoveAds      = [bool]$ui.ChkAds.IsChecked
        CleanupImage   = [bool]$ui.ChkCleanup.IsChecked
        UseEsd         = [bool]$ui.ChkEsd.IsChecked
        DriverDirs     = @($ui.TxtDrivers.Text -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        IncludeHostDrivers = [bool]$ui.ChkHostDrivers.IsChecked
        UpdatesDir     = $ui.TxtUpdates.Text
        UserName       = $ui.TxtUser.Text.Trim()
        Password       = $ui.TxtPass.Password
        ComputerName   = $ui.TxtPc.Text.Trim()
        Locale         = $ui.TxtLocale.Text.Trim()
        Keyboard       = $ui.TxtKbd.Text.Trim()
        TimeZone       = $ui.CmbTz.Text.Trim()
        AutoLogon      = [bool]$ui.ChkAutoLogon.IsChecked
        BypassHardware = [bool]$ui.ChkBypass.IsChecked
        OutputIso      = $ui.TxtOutIso.Text
        WipeDisk0      = [bool]$ui.ChkWipe.IsChecked
        CompactOS      = [bool]$ui.ChkCompact.IsChecked
        PortableMode   = [bool]$ui.ChkPortable.IsChecked
        DiskNumber     = $null
    }
    if ($ui.CmbEdition.SelectedIndex -ge 0) {
        $e = $script:Editions[$ui.CmbEdition.SelectedIndex]
        $o.ImageIndex = $e.Index; $o.EditionId = $e.EditionId
    }
    if ($o.Mode -eq 'WTG' -and $ui.CmbUsb.SelectedIndex -ge 0) { $o.DiskNumber = $script:UsbDisks[$ui.CmbUsb.SelectedIndex].Number }
    return $o
}

# ------------------------------------------------------------------------------------- events
$ui.BtnBrowseIso.Add_Click({
    $d = New-Object Microsoft.Win32.OpenFileDialog
    $d.Filter = 'Windows ISO (*.iso)|*.iso|All files|*.*'
    if ($d.ShowDialog()) { $ui.TxtIso.Text = $d.FileName; Update-Editions }
})
$ui.TxtIso.Add_LostFocus({ if ($ui.TxtIso.Text -and $ui.CmbEdition.Items.Count -eq 0) { Update-Editions } })
$ui.BtnDownload.Add_Click({
    Start-Process 'https://www.microsoft.com/software-download/windows11'
    Add-Log "Opened Microsoft's download page. Pick 'Download Windows 11 Disk Image (ISO) for x64 devices', then Browse to it here."
})
$ui.BtnDrivers.Add_Click({ $f = Select-Folder $ui.TxtDrivers.Text; if ($f) { $ui.TxtDrivers.Text = (@($ui.TxtDrivers.Text, $f) | Where-Object { $_ }) -join ';' } })
$ui.BtnUpdates.Add_Click({ $f = Select-Folder $ui.TxtUpdates.Text; if ($f) { $ui.TxtUpdates.Text = $f } })
$ui.BtnOutIso.Add_Click({
    $d = New-Object Microsoft.Win32.SaveFileDialog
    $d.Filter = 'ISO image (*.iso)|*.iso'; $d.FileName = Split-Path $ui.TxtOutIso.Text -Leaf
    if ($d.ShowDialog()) { $ui.TxtOutIso.Text = $d.FileName }
})
$modeChanged = { $ui.PnlIso.IsEnabled = [bool]$ui.RadIso.IsChecked; $ui.PnlWtg.IsEnabled = [bool]$ui.RadWtg.IsChecked; if ($ui.RadWtg.IsChecked -and $ui.CmbUsb.Items.Count -eq 0) { Update-Usb } }
$ui.RadIso.Add_Checked($modeChanged); $ui.RadWtg.Add_Checked($modeChanged)
$ui.BtnRefreshUsb.Add_Click({ Update-Usb })
$ui.ChkWipe.Add_Checked({
    $r = [Windows.MessageBox]::Show("With this on, the installer erases the first internal disk of whatever laptop boots it, without asking. Only use this ISO/USB on machines you mean to wipe.`n`nKeep it on?", 'Win11Forge', 'YesNo', 'Warning')
    if ($r -ne 'Yes') { $ui.ChkWipe.IsChecked = $false }
})

$ui.BtnUnattend.Add_Click({
    $o = Get-Options
    $d = New-Object Microsoft.Win32.SaveFileDialog
    $d.Filter = 'Answer file|autounattend.xml'; $d.FileName = 'autounattend.xml'
    if ($d.ShowDialog()) {
        Set-Content -Path $d.FileName -Value (New-ForgeUnattend -Options $o) -Encoding UTF8
        Add-Log "Saved $($d.FileName). Put it in the root of any Windows 11 USB installer."
    }
})

# ------------------------------------------------------------------------------ write ISO to USB
function Show-UsbPicker([string]$Iso) {
    <# Small dialog listing USB drives only. Returns the chosen disk, or $null. #>
    [xml]$px = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="Write to a USB drive" Width="520" SizeToContent="Height" ResizeMode="NoResize"
        WindowStartupLocation="CenterOwner" Background="White" FontFamily="Segoe UI" FontSize="13">
  <StackPanel Margin="20,16">
    <TextBlock Text="Write the ISO to a USB drive" FontSize="15" FontWeight="SemiBold"/>
    <TextBlock Name="TxtIsoName" Foreground="#5B6472" Margin="0,2,0,10"/>
    <TextBlock TextWrapping="Wrap" Foreground="#5B6472" Margin="0,0,0,10"
      Text="Only USB flash drives and external USB disks are listed. This PC's own disks never appear here. The drive you pick will be erased."/>
    <DockPanel>
      <Button Name="BtnRefresh" DockPanel.Dock="Right" Content="Refresh" Padding="12,4" Margin="6,0,0,0"/>
      <ComboBox Name="CmbDrive"/>
    </DockPanel>
    <TextBlock Name="TxtHint" Foreground="#B42318" Margin="0,6,0,0" TextWrapping="Wrap"/>
    <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" Margin="0,14,0,0">
      <Button Name="BtnCancel" Content="Not now" Padding="14,5" IsCancel="True"/>
      <Button Name="BtnWrite" Content="Erase and write" Padding="18,5" Margin="8,0,0,0" FontWeight="SemiBold"
              Background="#2563EB" Foreground="White" BorderBrush="#2563EB"/>
    </StackPanel>
  </StackPanel>
</Window>
'@
    $dlg = [Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader $px))
    $dlg.Owner = $win
    $c = @{}
    foreach ($nm in 'TxtIsoName', 'BtnRefresh', 'CmbDrive', 'TxtHint', 'BtnCancel', 'BtnWrite') { $c[$nm] = $dlg.FindName($nm) }
    $isoSize = (Get-Item $Iso).Length
    $need = $isoSize + 64MB
    $c.TxtIsoName.Text = ('{0}  ({1:N1} GB)' -f (Split-Path $Iso -Leaf), ($isoSize / 1GB))
    $st = @{ Disks = @(); Chosen = $null }
    $refresh = {
        $c.CmbDrive.Items.Clear()
        $st.Disks = @(Get-ForgeUsbDisks)
        foreach ($d in $st.Disks) {
            $t = $d.Label; if ($d.Size -lt $need) { $t += '   (too small)' }
            [void]$c.CmbDrive.Items.Add($t)
        }
        if ($st.Disks.Count) { $c.CmbDrive.SelectedIndex = 0; $c.TxtHint.Text = '' }
        else { $c.TxtHint.Text = 'No USB drive found. Plug one in (8 GB or bigger) and press Refresh.' }
        $c.BtnWrite.IsEnabled = [bool]$st.Disks.Count
    }.GetNewClosure()
    $c.BtnRefresh.Add_Click($refresh)
    $c.BtnWrite.Add_Click({
        if ($c.CmbDrive.SelectedIndex -lt 0) { return }
        $d = $st.Disks[$c.CmbDrive.SelectedIndex]
        if ($d.Size -lt $need) { $c.TxtHint.Text = 'That drive is too small for this ISO.'; return }
        $r = [Windows.MessageBox]::Show($dlg, "Everything on`n`n    $($d.Label)`n`nwill be erased. Continue?", 'Win11Forge', 'YesNo', 'Warning')
        if ($r -eq 'Yes') { $st.Chosen = $d; $dlg.Close() }
    }.GetNewClosure())
    & $refresh
    [void]$dlg.ShowDialog()
    return $st.Chosen
}

function Start-UsbWrite([string]$Iso) {
    if (-not $Iso -or -not (Test-Path $Iso -PathType Leaf)) {
        $d = New-Object Microsoft.Win32.OpenFileDialog
        $d.Filter = 'Windows ISO (*.iso)|*.iso|All files|*.*'; $d.Title = 'ISO to write'
        if (-not $d.ShowDialog()) { return }
        $Iso = $d.FileName
    }
    $disk = Show-UsbPicker -Iso $Iso
    if (-not $disk) { return }
    Add-Log ''
    Start-ForgeJob -Kind 'usb' -Arg @{ Iso = $Iso; Disk = $disk.Number }
}

$ui.BtnUsb.Add_Click({ Start-UsbWrite -Iso $ui.TxtOutIso.Text })

# Builds and USB writes run in a background runspace so the window stays responsive.
$script:Sync = [hashtable]::Synchronized(@{ Queue = New-Object System.Collections.Concurrent.ConcurrentQueue[string]; Done = $false; Ok = $false; Kind = '' })
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(300)
$timer.Add_Tick({
    $m = $null
    while ($script:Sync.Queue.TryDequeue([ref]$m)) { Add-Log $m }
    if ($script:Sync.Done) {
        $timer.Stop()
        $ui.Prog.IsIndeterminate = $false; $ui.Prog.Value = if ($script:Sync.Ok) { 100 } else { 0 }
        $ui.BtnBuild.IsEnabled = $true; $ui.BtnUsb.IsEnabled = $true
        $script:Job.Dispose(); $script:Rs.Dispose()
        if ($script:Sync.Kind -eq 'usb') {
            $msg = if ($script:Sync.Ok) { "The USB drive is ready. You can unplug it now.`n`nBoot the laptop from it with the F12 / F9 / Esc boot menu." } else { 'Writing the USB drive stopped with an error. See the log.' }
            [void][Windows.MessageBox]::Show($msg, 'Win11Forge')
        } elseif ($script:Sync.Ok -and $script:LastMode -eq 'ISO' -and (Test-Path $script:LastIso -PathType Leaf)) {
            $r = [Windows.MessageBox]::Show("The ISO is ready:`n`n    $($script:LastIso)`n`nWrite it to a USB drive now?", 'Win11Forge', 'YesNo', 'Question')
            if ($r -eq 'Yes') { Start-UsbWrite -Iso $script:LastIso }
        } else {
            $msg = if ($script:Sync.Ok) { 'Finished. See the log for where the output is.' } else { 'The build stopped with an error. See the log.' }
            [void][Windows.MessageBox]::Show($msg, 'Win11Forge')
        }
    }
})

function Start-ForgeJob([string]$Kind, $Arg) {
    $ui.BtnBuild.IsEnabled = $false; $ui.BtnUsb.IsEnabled = $false
    $ui.Prog.IsIndeterminate = $true
    $script:Sync.Done = $false; $script:Sync.Ok = $false; $script:Sync.Kind = $Kind

    $script:Rs = [runspacefactory]::CreateRunspace()
    $script:Rs.ApartmentState = 'STA'
    $script:Rs.Open()
    $script:Job = [powershell]::Create()
    $script:Job.Runspace = $script:Rs
    [void]$script:Job.AddScript({
        param($ModulePath, $Kind, $Arg, $Sync)
        try {
            Import-Module $ModulePath -Force
            Set-ForgeLogger -Logger ({ param($m) $Sync.Queue.Enqueue($m) }.GetNewClosure())
            if ($Kind -eq 'usb') { Write-ForgeIsoToUsb -IsoPath $Arg.Iso -DiskNumber $Arg.Disk }
            else { Invoke-ForgeBuild -Options $Arg }
            $Sync.Ok = $true
        } catch {
            $Sync.Queue.Enqueue("ERROR: $($_.Exception.Message)")
        } finally {
            $Sync.Done = $true
        }
    }).AddArgument($ModulePath).AddArgument($Kind).AddArgument($Arg).AddArgument($script:Sync)
    [void]$script:Job.BeginInvoke()
    $timer.Start()
}

$ui.BtnBuild.Add_Click({
    $o = Get-Options
    if (-not $o.IsoPath)    { [void][Windows.MessageBox]::Show('Pick a Windows 11 ISO first.', 'Win11Forge'); return }
    if (-not $o.ImageIndex) { [void][Windows.MessageBox]::Show('Pick an edition.', 'Win11Forge'); return }
    if ($o.Mode -eq 'WTG') {
        if ($null -eq $o.DiskNumber) { [void][Windows.MessageBox]::Show('Pick the USB drive.', 'Win11Forge'); return }
        $label = $script:UsbDisks[$ui.CmbUsb.SelectedIndex].Label
        $r = [Windows.MessageBox]::Show("Everything on`n`n    $label`n`nwill be erased. Continue?", 'Win11Forge', 'YesNo', 'Warning')
        if ($r -ne 'Yes') { return }
    }
    $ui.TxtLog.Clear()
    $script:LastMode = $o.Mode; $script:LastIso = $o.OutputIso
    Start-ForgeJob -Kind 'build' -Arg $o
})

[void]$win.ShowDialog()
