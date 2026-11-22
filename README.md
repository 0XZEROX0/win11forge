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
sudo apt install wimtools libwin-hivex-perl xorriso p7zip-full parted dosfstools exfatprogs ntfs-3g python3-tk   # Debian/Ubuntu
sudo dnf install wimlib-utils hivex xorriso p7zip p7zip-plugins parted dosfstools exfatprogs ntfs-3g python3-tkinter      # Fedora
./win11forge.sh          # ISO output, no root needed
sudo ./win11forge.sh     # Windows To Go output (it writes to a disk)
```

Then:
1. **Pick the ISO.** Get it from <https://www.microsoft.com/software-download/windows11> (the window has a button for this). Then choose the edition. Pro is selected by default.
2. **Debloat.** Choose which apps to remove and leave "Remove ads…" on.
3. **Drivers.** *Add Intel RST/VMD storage driver* is ticked by default (see below). Optional: add folders of extracted driver packs.
4. **First boot.** Set the user name, an optional password, language, keyboard and time zone.
5. **Output.** Choose ISO or a USB disk, then press **Build**. A build takes about 20 to 40 minutes.
6. **Burn to USB (optional).** When an ISO finishes, Win11Forge asks whether to write it to a USB drive. Say yes, pick the
   drive and confirm, and it becomes a bootable installer. The **Write ISO to USB...** button does the same for any ISO later.

Headless: `Win11Forge.ps1 -Config build.json` or `./win11forge.sh --config build.json` (see `build.example.json`).
## Writing the ISO to a USB drive

- Only **USB** drives are listed (flash drives, external USB disks). The disk the PC runs from, internal SATA/NVMe disks,
  disks holding system mounts and the drive the ISO itself is stored on are never offered or are refused.
- You confirm the drive once more before it is erased.
- You pick the format in the same window:
  - **exFAT (default).** A small FAT32 boot partition (`FORGEBOOT`, about 1.2 GB: the UEFI boot files and
    `sources/boot.wim`) followed by an exFAT partition (`WIN11FORGE`) with the whole installer, `install.wim` in one
    piece, filling the rest of the drive. Use the free space for your own files, including files over 4 GB.
    The laptop boots Microsoft's own signed boot files from the FAT32 partition, so it works with **Secure Boot on**;
    Windows Setup then finds `install.wim` on the exFAT partition by itself. Drives over 2 TB get GPT instead of MBR.
  - **FAT32 only.** One FAT32 partition with the ISO's files. A `install.wim` over 4 GB is split into `install.swm`
    parts (Windows Setup reads those directly). Use it if a very old laptop does not boot the exFAT layout.
  - On Windows, MBR drives also get legacy BIOS boot code (`bootsect`); Linux-written drives are UEFI only.
- On Linux, writing a drive needs root. If you started the app without sudo it asks for your password through the
  desktop's normal prompt (`pkexec`). From a terminal: `sudo ./win11forge.sh --list-usb`, then
  `sudo ./win11forge.sh --write-usb Win11-Forge.iso --disk /dev/sdX` (add `--usb-format fat32` for FAT32 only).
- On Windows from a terminal: `Win11Forge.ps1 -ListUsb`, then `Win11Forge.ps1 -WriteUsb .\Win11-Forge.iso -Disk 2` (add `-UsbFormat FAT32` for FAT32 only).

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

### Intel RST / VMD storage driver (optional, on by default)

Many Intel laptops ship with **VMD** turned on in the BIOS. Stock Windows Setup then shows "no drives found" until you
load Intel's storage driver or turn VMD off. With **"Add Intel RST/VMD storage driver"** ticked (`"IncludeIntelRst": true`
in `build.json`), Win11Forge adds it to Windows Setup and to the installed Windows, so the disk shows up with no BIOS change.

- It downloads Intel's official `SetupRST.exe` from `downloadmirror.intel.com` and checks it against the SHA-256 Intel
  publishes. Only the "F6 / Load driver" files (`iaStorVD*.inf/.sys/.cat`) are taken out; the installer is never run.
- Versions included (listed in `config/intel-rst.json`):

  | Driver | Laptops | Intel download page |
  |---|---|---|
  | 20.4.0.1006 | 12th to 15th Gen Core, Core Ultra Series 1 and 2 | [930427](https://www.intel.com/content/www/us/en/download/930427/intel-rapid-storage-technology-intel-rst-installation-for-12th-to-15th-gen-platforms.html) |
  | 21.1.0.1006 | Core Ultra Series 3 | [920456](https://www.intel.com/content/www/us/en/download/920456/intel-rapid-storage-technology-driver-installation-software-for-intel-core-ultra-series-3-platforms.html) |
  | 18.7.6.1010 | 10th and 11th Gen Core | [19512](https://www.intel.com/content/www/us/en/download/19512/intel-rapid-storage-technology-driver-installation-software-with-intel-optane-memory-10th-and-11th-gen-platforms.html) |

- The download (~32 MB) is cached in the work folder. **Offline builds:** put the `SetupRST.exe` files from those pages in
  `tools/intel-rst/` (any file name); they are used when their hash matches.
- On laptops without VMD (or AMD laptops) the driver just isn't used, so leaving it on is harmless.
- When Intel releases a newer version, update the entry in `config/intel-rst.json` (URL + SHA-256 from Intel's page).

### Other driver packs

For full **offline** support, put extracted driver packs (`.inf` files, not `.exe`) in one or more folders and add them under *Drivers*:
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

- The tool only reads the ISO you give it. It never touches the disk it is running from, and for Windows To Go and USB installers it only lists **USB** disks and asks before wiping one.
- "Fully hands-off: wipe disk 0" is off by default. With it off, setup still stops once to ask which disk to install on, which protects the machine's data. Turn it on only for media you use on machines you intend to wipe.
- `autounattend.xml` / `unattend.xml` hold the account password in plain text. Leave the password empty, or keep the USB drive safe.
- **No activation is bypassed.** The answer file uses Microsoft's public *generic setup key* (`config/editions.json`) only so that setup does not stop to ask for a key. Windows activates as usual from the laptop's digital or OEM license, or from your own key.

## Layout

```
Start-Win11Forge.cmd       Windows launcher (elevates)
Win11Forge.ps1             Windows GUI + headless mode
modules/Forge.Core.psm1    Windows engine (DISM, reg.exe, bcdboot, oscdimg)
win11forge.sh              Linux launcher
linux/forge_engine.py      Linux engine (wimlib, hivex, 7-Zip, xorriso, parted)
linux/win11forge.py        Linux GUI + CLI
config/                    apps, registry tweaks, answer file template, generic setup keys, Intel RST list (shared)
scripts/firstlogon.ps1     runs once at first desktop: finishes debloat, keeps auto sign-in, driver scan
winpe-finish/              one-time boot fix-up used by Linux-built Windows To Go drives
```

## Test status

- **Linux ISO pipeline:** tested end to end on a synthetic Windows image. It extracts the edition, blocks apps through Deprovisioned keys, merges registry hives, stages drivers, writes a valid answer file and builds a BIOS+UEFI El Torito ISO. The camera app was kept as intended.
- **Intel RST/VMD option:** download, SHA-256 check and extraction tested on Linux (Python) and in PowerShell 7; both
  give byte-identical `iaStorVD` files. Not yet tried on a real VMD laptop.
- **Answer file:** generated for ISO, ISO with wipe, and WTG modes, and parsed as valid XML.
- **Write ISO to USB (Linux):** partitioning, the copy with progress, the >4 GB split and the drive filter were tested on
  a loop device and fake disk lists, and the drive picker in the GUI. The exFAT layout was tested with a real
  Win11Forge ISO on a 64 GB (MBR) and a 3 TB (GPT) loop disk: the drive booted in QEMU/OVMF with **Secure Boot on**
  (Microsoft keys), Windows Setup found the unsplit 7.6 GB `install.wim` on the exFAT partition and started installing.
  Not yet tried on a real USB stick.
- **Write ISO to USB (Windows):** written but not yet run.
- **Windows engine, both GUIs and Windows To Go on either OS:** written but **not yet run on real hardware**. Please try a first build in a VM (Hyper-V or VirtualBox, with EFI enabled) before using it on a laptop.
