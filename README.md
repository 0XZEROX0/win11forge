# Win11Forge

Takes an **official, unmodified Windows 11 ISO** and turns it into a lighter Windows that boots
**straight to the desktop**: no language, keyboard, network, Microsoft account or privacy screens.
It runs on **Windows** (DISM engine, WPF window) and **Linux** (wimlib engine, Tk window), and builds
either:

1. **A bootable ISO** that installs on any laptop, or
2. **Windows To Go**: Windows installed directly onto a USB flash drive or external SSD, which you then boot on any laptop.

## Quick start

**Windows 10/11 host.** Double-click `Start-Win11Forge.cmd`. It asks for admin rights and opens the window.
For ISO output, install `oscdimg` once: `winget install Microsoft.WindowsADK` and tick only *Deployment Tools*,
or copy `oscdimg.exe` into `tools\`.

**Linux host.**
```bash
sudo apt install wimtools libwin-hivex-perl xorriso parted dosfstools ntfs-3g python3-tk   # Debian/Ubuntu
sudo dnf install wimlib-utils hivex xorriso parted dosfstools ntfs-3g python3-tkinter      # Fedora
./win11forge.sh          # ISO output, no root needed
sudo ./win11forge.sh     # Windows To Go output (it writes to a disk)
```

Then:
1. **Pick the ISO.** Get it from <https://www.microsoft.com/software-download/windows11> (the window has a button for this). Then choose the edition. Pro is selected by default.
2. **Debloat.** Choose which apps to remove and leave "Remove ads…" on.
3. **Drivers.** Optional: add folders of extracted driver packs (see below).
4. **First boot.** Set the user name, an optional password, language, keyboard and time zone.
5. **Output.** Choose ISO or a USB disk, then press **Build**. A build takes about 20 to 40 minutes.

Headless: `Win11Forge.ps1 -Config build.json` or `./win11forge.sh --config build.json` (see `build.example.json`).
The **Save answer file only** button writes just `autounattend.xml`. You can drop that file into any Windows 11 USB stick, including one made with Rufus.

## What it does to the image

| Area | Change |
|---|---|
| Apps | Removes Clipchamp, Copilot, Office hub, new Outlook, Teams, News, Weather, Bing Search, Tips, Solitaire, To Do, Feedback Hub, Maps, Phone Link, Family, Quick Assist, Dev Home, Xbox app, Widgets and more. The list is in `config/apps.json`. **Camera, Sound Recorder, Photos, Store, Terminal, Notepad, Paint, Calculator and Snipping Tool are kept.** |
| Ads | Turns off consumer features and "suggested" apps, Start recommendations, lock screen tips, Widgets, the Copilot button, Bing in Start search, the "finish setting up" nag, advertising ID and tailored experiences. It also stops Teams, Outlook and Dev Home from being pushed in after setup. |
| Space | Returns the ~7 GB of reserved storage, cleans up the component store (`/ResetBase`) and re-compresses the image (WIM max, or ESD). |
| Stability | Prevents automatic BitLocker device encryption and turns off Fast Startup. With Windows To Go it also keeps the host laptop's internal disks offline (SanPolicy 4) and turns off hibernation. |
| Setup | Uses an `autounattend.xml` that skips every OOBE page, creates a local administrator account, can sign in automatically on every boot, and can skip TPM, Secure Boot, CPU and RAM checks for older laptops. |

All registry changes are listed in `config/tweaks.json`, and the answer file is `config/unattend.template.xml`. Both engines use these same files.

## Laptop compatibility (audio, mic, camera, touchpad, touchscreen)

Windows 11 already includes class drivers for HD Audio, USB webcams (UVC), I2C/HID precision touchpads,
HID touchscreens, USB, NVMe and most Ethernet. The tool **does not disable Windows Update driver delivery**, and the first-logon script
starts a driver scan, so the rest (Realtek/Conexant audio extras, IR cameras, card readers) install on their own once the laptop is online.

For full **offline** support, put extracted driver packs (`.inf` files, not `.exe`) in one or more folders and add them under *Drivers*:
- **Intel RST / VMD storage driver.** This one matters most. Without it many Intel laptops show "no drives found" in setup.
- Wi-Fi drivers: Intel, Realtek, MediaTek, Qualcomm.
- OEM driver packs: Dell "Driver Packs", HP "Driver Packs", Lenovo "SCCM packages". These are official, extracted, multi-model bundles.

On a Windows host you can also tick **"Also include all drivers from this PC"**.
Drivers go into both the installed Windows and Windows Setup.
On Linux they are staged in `C:\Windows\Setup\Drivers` (installed with `pnputil` during setup) and in `\$WinPEDriver$` on the ISO.

## Windows To Go notes

- Use a **USB 3 drive of 64 GB or more**. An external NVMe/SATA SSD in a USB enclosure is much faster than a flash stick.
- Boot it from the laptop's one-time boot menu (usually F12, F9, F8 or Esc). Windows sets itself up on that laptop and opens the desktop. The same drive works on other laptops too, because Windows detects new hardware at every boot.
- **Linux-built drives are UEFI only.** On their first boot they spend about 30 seconds in a small Windows PE taken from your ISO, which writes the final boot files with Microsoft's own `bcdboot`. Then they restart into Windows. Windows-built drives boot on both UEFI and legacy BIOS.
- On Linux, unwanted Store apps are blocked offline, so they never install for the account, and their files are deleted on the first logon. The Windows engine removes them offline.

## Safety and licensing

- The tool only reads the ISO you give it. It never touches the disk it is running from, and for Windows To Go it only lists **USB** disks and asks before wiping one.
- "Fully hands-off: wipe disk 0" is off by default. With it off, setup still stops once to ask which disk to install on, which protects the machine's data. Turn it on only for media you use on machines you intend to wipe.
- `autounattend.xml` / `unattend.xml` hold the account password in plain text. Leave the password empty, or keep the USB drive safe.
- **No activation is bypassed.** The answer file uses Microsoft's public *generic setup key* (`config/editions.json`) only so that setup does not stop to ask for a key. Windows activates as usual from the laptop's digital or OEM license, or from your own key.

## Layout

```
Start-Win11Forge.cmd       Windows launcher (elevates)
Win11Forge.ps1             Windows GUI + headless mode
modules/Forge.Core.psm1    Windows engine (DISM, reg.exe, bcdboot, oscdimg)
win11forge.sh              Linux launcher
linux/forge_engine.py      Linux engine (wimlib, hivex, xorriso, parted)
linux/win11forge.py        Linux GUI + CLI
config/                    apps, registry tweaks, answer file template, generic setup keys (shared)
scripts/firstlogon.ps1     runs once at first desktop: finishes debloat, keeps auto sign-in, driver scan
winpe-finish/              one-time boot fix-up used by Linux-built Windows To Go drives
```

## Test status

- **Linux ISO pipeline:** tested end to end on a synthetic Windows image. It extracts the edition, blocks apps through Deprovisioned keys, merges registry hives, stages drivers, writes a valid answer file and builds a BIOS+UEFI El Torito ISO. The camera app was kept as intended.
- **Answer file:** generated for ISO, ISO with wipe, and WTG modes, and parsed as valid XML.
- **Windows engine, both GUIs and Windows To Go on either OS:** written but **not yet run on real hardware**. Please try a first build in a VM (Hyper-V or VirtualBox, with EFI enabled) before using it on a laptop.
