"""
Win11Forge Linux engine.

Same job as modules/Forge.Core.psm1, built on Linux tools instead of DISM:
  wimlib-imagex   (wimtools)            export / edit / apply Windows images
  hivexregedit    (libwin-hivex-perl)   edit offline registry hives
  7z (p7zip / 7zip)                     read the source ISO (Microsoft ISOs keep their files in UDF only)
  xorriso                               write the new ISO
  parted, mkfs.vfat, mkfs.ntfs          only for Windows To Go (needs root)

Differences from the Windows engine, by necessity:
  * Store apps are blocked offline (Deprovisioned registry keys, so they never install for the
    new account) and physically removed by firstlogon.ps1 on the first boot.
  * Drivers are staged in C:\\Windows\\Setup\\Drivers and installed by pnputil during setup;
    for ISO output they also go in \\$WinPEDriver$ so Windows Setup itself sees them.
  * Windows To Go drives get their final boot files from Microsoft's bcdboot on the first boot,
    via a one-time mini Windows PE taken from the ISO. UEFI boot only.
"""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"

TOOLS_ISO = ["wimlib-imagex", "hivexregedit", "xorriso", "7z"]
TOOLS_WTG = ["wimlib-imagex", "hivexregedit", "xorriso", "7z", "parted", "mkfs.vfat", "mkfs.ntfs", "lsblk", "wipefs", "mount", "umount"]
SPLIT_LIMIT = 4000 * 1024 * 1024
HIVE_PREFIX = {"SOFT": r"HKEY_LOCAL_MACHINE\SOFTWARE", "SYS": r"HKEY_LOCAL_MACHINE\SYSTEM", "USER": r"HKEY_CURRENT_USER"}
HIVE_FILE = {"SOFT": "SOFTWARE", "SYS": "SYSTEM", "USER": "NTUSER.DAT"}
HIVE_WIM_PATH = {"SOFT": "/Windows/System32/config/SOFTWARE", "SYS": "/Windows/System32/config/SYSTEM", "USER": "/Users/Default/NTUSER.DAT"}

# Common Linux (IANA) zones -> Windows time zone ids, used for the default only.
TZ_MAP = {
    "UTC": "UTC", "Etc/UTC": "UTC", "Europe/London": "GMT Standard Time", "Europe/Berlin": "W. Europe Standard Time",
    "Europe/Paris": "Romance Standard Time", "Europe/Madrid": "Romance Standard Time", "Europe/Rome": "W. Europe Standard Time",
    "Europe/Istanbul": "Turkey Standard Time", "Europe/Moscow": "Russian Standard Time", "Africa/Cairo": "Egypt Standard Time",
    "Asia/Riyadh": "Arab Standard Time", "Asia/Dubai": "Arabian Standard Time", "Asia/Karachi": "Pakistan Standard Time",
    "Asia/Kolkata": "India Standard Time", "Asia/Shanghai": "China Standard Time", "Asia/Tokyo": "Tokyo Standard Time",
    "Asia/Singapore": "Singapore Standard Time", "Australia/Sydney": "AUS Eastern Standard Time",
    "America/New_York": "Eastern Standard Time", "America/Chicago": "Central Standard Time",
    "America/Denver": "Mountain Standard Time", "America/Los_Angeles": "Pacific Standard Time",
    "America/Sao_Paulo": "E. South America Standard Time", "Africa/Johannesburg": "South Africa Standard Time",
    "Africa/Lagos": "W. Central Africa Standard Time", "Asia/Amman": "Jordan Standard Time", "Asia/Baghdad": "Arabic Standard Time",
}
WINDOWS_TZ_CHOICES = sorted(set(TZ_MAP.values()))
LCID_KEYBOARDS = {"en-US": "0409:00000409", "en-GB": "0809:00000809", "ar-SA": "0401:00000401", "ar-EG": "0c01:00000401",
                  "fr-FR": "040c:0000040c", "de-DE": "0407:00000407", "es-ES": "0c0a:0000040a", "tr-TR": "041f:0000041f"}


INSTALL_HINT = ("Debian/Ubuntu: sudo apt install wimtools libwin-hivex-perl xorriso p7zip-full parted dosfstools ntfs-3g\n"
                "Fedora:        sudo dnf install wimlib-utils hivex xorriso p7zip p7zip-plugins parted dosfstools ntfs-3g\n"
                "Arch:          sudo pacman -S wimlib hivex libisoburn 7zip parted dosfstools ntfs-3g")


class ForgeError(Exception):
    pass


def find_7z():
    for n in ("7z", "7zz", "7za"):
        if shutil.which(n):
            return n
    return None


def load_json(name):
    with open(CONFIG / name, encoding="utf-8") as f:
        return json.load(f)


class Engine:
    def __init__(self, log=print, progress=None):
        self.log = log
        self.progress = progress  # optional callback(percent) for long wimlib steps

    # ------------------------------------------------------------------ process helpers
    def run(self, args, ok=(0,), capture=False, quiet=False):
        args = [str(a) for a in args]
        if not quiet:
            self.log("> " + " ".join(args))
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        out = []
        for line in p.stdout:
            line = line.rstrip()
            out.append(line)
            if not capture and line.strip() and not quiet:
                # wimlib progress lines repeat a lot; keep the log readable
                pm = re.search(r"(\d+)%\)?\s*done\s*$", line)
                if pm and self.progress:
                    self.progress(int(pm.group(1)))
                if pm and "(100%)" not in line:
                    continue
                self.log("  " + line.strip())
        code = p.wait()
        if code not in ok:
            tail = "\n".join(out[-15:])
            raise ForgeError(f"{Path(args[0]).name} failed (exit {code})\n{tail}")
        return "\n".join(out)

    @staticmethod
    def missing_tools(mode):
        return [t for t in (TOOLS_WTG if mode == "WTG" else TOOLS_ISO)
                if (find_7z() is None if t == "7z" else shutil.which(t) is None)]

    def iso_extract(self, iso, dest, members=None, quiet=False):
        """Copy files out of an ISO. Official Microsoft ISOs keep everything in a UDF file system
        (the ISO9660 part only has a README), which xorriso cannot read, so 7-Zip is used."""
        sz = find_7z()
        if sz is None:
            raise ForgeError("7-Zip is needed to read Windows ISOs (they use UDF).\n" + INSTALL_HINT)
        Path(dest).mkdir(parents=True, exist_ok=True)
        args = [sz, "x", "-y", "-bsp0", "-bso0", f"-o{dest}", str(iso)]
        if members:
            args += list(members)
        self.run(args, ok=(0, 1), quiet=quiet)

    # ------------------------------------------------------------------ source
    def find_install(self, media):
        for n in ("install.wim", "install.esd"):
            p = Path(media) / "sources" / n
            if p.exists():
                return p
        if (Path(media) / "sources" / "install.swm").exists():
            raise ForgeError("Split install.swm images are not supported. Use the original Microsoft ISO.")
        raise ForgeError("No sources/install.wim or install.esd. Is this a Windows 11 ISO?")

    def editions(self, iso, scratch=None):
        """List editions. For an .iso only the install image is copied out (to read its metadata)."""
        if Path(iso).is_dir():
            img = self.find_install(iso)
            return self.parse_info(self.run(["wimlib-imagex", "info", img], capture=True, quiet=True))
        if not Path(iso).is_file():
            raise ForgeError(f"ISO not found: {iso}")
        if scratch:
            Path(scratch).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="forge-", dir=scratch) as tmp:
            self.iso_extract(iso, tmp, ["sources/install.wim", "sources/install.esd", "sources/install.swm"], quiet=True)
            img = self.find_install(tmp)
            return self.parse_info(self.run(["wimlib-imagex", "info", img], capture=True, quiet=True))

    @staticmethod
    def parse_info(text):
        eds, cur = [], None
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Za-z ]+?):\s*(.*)$", line)
            if not m:
                continue
            k, v = m.group(1).strip(), m.group(2).strip()
            if k == "Index":
                cur = {"Index": int(v), "Name": "", "EditionId": "", "Build": "", "Language": "", "Arch": ""}
                eds.append(cur)
            elif cur is not None:
                if k == "Name":
                    cur["Name"] = v
                elif k == "Edition ID":
                    cur["EditionId"] = v
                elif k == "Build":
                    cur["Build"] = v
                elif k == "Default Language" or (k == "Languages" and not cur["Language"]):
                    cur["Language"] = v.split()[0] if v else ""
                elif k == "Architecture":
                    cur["Arch"] = v
        return eds

    # ------------------------------------------------------------------ answer file
    @staticmethod
    def expand_template(text, flags, tokens):
        rx = re.compile(r"<!--#IF (\w+)-->\r?\n?(.*?)<!--#ENDIF \1-->\r?\n?", re.S)
        while rx.search(text):
            text = rx.sub(lambda m: m.group(2) if flags.get(m.group(1)) else "", text)
        for k, v in tokens.items():
            text = text.replace("{{%s}}" % k, "" if v is None else str(v))
        return text

    def unattend(self, o):
        keys = load_json("editions.json")["genericKeys"]
        key = keys.get(o.get("EditionId") or "")
        pc = (o.get("ComputerName") or "").strip()
        flags = {"ISO": o["Mode"] == "ISO", "WIPE": bool(o.get("WipeDisk0")), "KEY": bool(key),
                 "BYPASS": bool(o.get("BypassHardware")), "AUTOLOGON": bool(o.get("AutoLogon", True))}
        tokens = {"LOCALE": escape(o["Locale"]), "KEYBOARD": escape(o["Keyboard"]), "TIMEZONE": escape(o["TimeZone"]),
                  "USER": escape(o["UserName"]), "PASSWORD": escape(o.get("Password") or ""),
                  "COMPUTERNAME": escape(pc) if pc else "*", "PRODUCTKEY": key}
        tpl = (CONFIG / "unattend.template.xml").read_text(encoding="utf-8")
        return self.expand_template(tpl, flags, tokens)

    # ------------------------------------------------------------------ registry
    @staticmethod
    def when(w, o):
        return {"always": True, "ads": bool(o.get("RemoveAds")), "bypass": bool(o.get("BypassHardware")),
                "wtg": o["Mode"] == "WTG", "portable": o["Mode"] == "WTG" and bool(o.get("PortableMode"))}.get(w, False)

    def reg_files(self, o, deprovision):
        """Build one .reg text per hive."""
        cfg = load_json("tweaks.json")
        per = {h: {} for h in HIVE_PREFIX}
        for t in cfg["set"]:
            if self.when(t["when"], o):
                per[t["hive"]].setdefault(t["key"], []).append((t["name"], int(t["value"])))
        for fam in sorted(deprovision):
            per["SOFT"].setdefault(r"Microsoft\Windows\CurrentVersion\Appx\AppxAllUserStore\Deprovisioned" + "\\" + fam, [])
        files = {}
        for h, keys in per.items():
            if not keys:
                continue
            lines = ["Windows Registry Editor Version 5.00", ""]
            # hivexregedit cannot create missing parent keys, so every ancestor gets its own
            # (empty) section first. Existing values are kept; listed values are added/replaced.
            ordered = []
            for k in keys:
                parts = k.split("\\")
                for i in range(1, len(parts) + 1):
                    a = "\\".join(parts[:i])
                    if a.lower() not in (x.lower() for x in ordered):
                        ordered.append(a)
            for k in ordered:
                vals = next((vv for kk, vv in keys.items() if kk.lower() == k.lower()), [])
                lines.append(f"[{HIVE_PREFIX[h]}\\{k}]")
                for n, v in vals:
                    lines.append(f'"{n}"=dword:{v & 0xFFFFFFFF:08x}')
                lines.append("")
            files[h] = "\n".join(lines) + "\n"
        deletes = [d["key"] for d in cfg["delete"] if d["hive"] == "SOFT" and self.when(d["when"], o)]
        return files, deletes

    def merge_reg(self, hive_file, hive, text):
        with tempfile.NamedTemporaryFile("w", suffix=".reg", delete=False, encoding="utf-8", newline="") as f:
            f.write(text)
            name = f.name
        try:
            self.run(["hivexregedit", "--merge", "--prefix", HIVE_PREFIX[hive], hive_file, name], quiet=True)
        finally:
            os.unlink(name)

    def edit_hives(self, hive_dir, o, deprovision):
        files, deletes = self.reg_files(o, deprovision)
        for h, text in files.items():
            hf = Path(hive_dir) / HIVE_FILE[h]
            if not hf.exists():
                self.log(f"  (hive {HIVE_FILE[h]} not in image, skipped)")
                continue
            self.merge_reg(hf, h, text)
        for k in deletes:
            try:
                self.merge_reg(Path(hive_dir) / "SOFTWARE", "SOFT",
                               f"Windows Registry Editor Version 5.00\n\n[-{HIVE_PREFIX['SOFT']}\\{k}]\n")
            except ForgeError:
                pass  # key not present in this build
        n = sum(t.count('"=dword:') for t in files.values())
        self.log(f"  applied {n} registry values, blocked {len(deprovision)} apps")
        return list(files)

    # ------------------------------------------------------------------ apps
    def provisioned_apps(self, wim):
        out = self.run(["wimlib-imagex", "dir", wim, "1", "--path=/Program Files/WindowsApps"], capture=True, quiet=True, ok=(0, 1))
        names = set()
        for line in out.splitlines():
            m = re.match(r"^/Program Files/WindowsApps/([^/]+)$", line.strip())
            if m and m.group(1).count("_") >= 4:
                names.add(m.group(1))
        return sorted(names)

    @staticmethod
    def families_to_block(folders, patterns):
        fams = set()
        for f in folders:
            parts = f.split("_")
            name, pub = parts[0], parts[-1]
            if any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns):
                fams.add(f"{name}_{pub}")
        return fams

    # ------------------------------------------------------------------ USB disks
    @staticmethod
    def usb_disks():
        out = subprocess.run(["lsblk", "-J", "-b", "-d", "-o", "NAME,PATH,SIZE,MODEL,TRAN,TYPE"], capture_output=True, text=True).stdout
        root_disk = ""
        try:
            src = subprocess.run(["findmnt", "-no", "SOURCE", "/"], capture_output=True, text=True).stdout.strip()
            root_disk = subprocess.run(["lsblk", "-no", "PKNAME", src], capture_output=True, text=True).stdout.strip().splitlines()[0]
        except Exception:
            pass
        disks = []
        for d in json.loads(out or '{"blockdevices":[]}')["blockdevices"]:
            if d.get("type") == "disk" and d.get("tran") == "usb" and d.get("name") != root_disk:
                gb = int(d.get("size") or 0) / 1e9
                disks.append({"path": d["path"], "size": int(d.get("size") or 0),
                              "label": f"{d['path']}: {(d.get('model') or 'USB disk').strip()} ({gb:.0f} GB)"})
        return disks

    def partitions(self, dev):
        out = self.run(["lsblk", "-lnpo", "NAME,TYPE", dev], capture=True, quiet=True)
        return [l.split()[0] for l in out.splitlines() if l.split()[1:] == ["part"]]

    # ------------------------------------------------------------------ build
    def build(self, o):
        o = dict(o)
        o.setdefault("AutoLogon", True)
        o.setdefault("RemoveApps", [a["match"] for a in load_json("apps.json")["apps"] if a["remove"]])
        if not o.get("UserName"):
            raise ForgeError("A user name is required.")
        if re.search(r'[\\/\[\]:;|=,+*?<>"@]', o["UserName"]):
            raise ForgeError("User name has characters Windows does not allow.")
        if o["UserName"].lower() in ("administrator", "guest", "defaultaccount", "wdagutilityaccount"):
            raise ForgeError(f"Pick a different user name; '{o['UserName']}' is reserved.")
        miss = self.missing_tools(o["Mode"])
        if miss:
            raise ForgeError("Missing tools: " + ", ".join(miss) + "\n" + INSTALL_HINT)
        if o["Mode"] == "WTG" and os.geteuid() != 0:
            raise ForgeError("Windows To Go needs root (run with sudo).")

        work = Path(o.get("WorkDir") or (Path.home() / "win11forge-work"))
        work.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(work).free
        if free < 25 * 1024 ** 3:
            raise ForgeError(f"Work folder needs about 25 GB free (has {free / 1024 ** 3:.0f} GB).")
        t0 = time.time()

        media = work / "media"
        if media.exists():
            shutil.rmtree(media)
        self.log("Step 1/6  Unpacking the ISO")
        if Path(o["IsoPath"]).is_dir():
            shutil.copytree(o["IsoPath"], media)
        else:
            self.iso_extract(o["IsoPath"], media)
        self.run(["chmod", "-R", "u+w", media], quiet=True)
        src = self.find_install(media)
        eds = {e["Index"]: e for e in self.parse_info(self.run(["wimlib-imagex", "info", src], capture=True, quiet=True))}
        idx = int(o["ImageIndex"])
        if idx not in eds:
            raise ForgeError(f"Edition index {idx} not in this ISO.")
        o["EditionId"] = eds[idx]["EditionId"]
        self.log(f"Source: {eds[idx]['Name']}  build {eds[idx]['Build']}  ({o['EditionId']})")

        stage = work / "stage.wim"
        if stage.exists():
            stage.unlink()
        self.log("Step 2/6  Extracting edition")
        self.run(["wimlib-imagex", "export", src, idx, stage, "--compress=LZX"])
        src.unlink()

        self.log("Step 3/6  Debloat and registry")
        folders = self.provisioned_apps(stage)
        fams = self.families_to_block(folders, o.get("RemoveApps") or [])
        hives = work / "hives"
        if hives.exists():
            shutil.rmtree(hives)
        hives.mkdir()
        self.run(["wimlib-imagex", "extract", stage, "1", *HIVE_WIM_PATH.values(), f"--dest-dir={hives}", "--no-acls", "--nullglob"], quiet=True)
        edited = self.edit_hives(hives, o, fams)

        self.log("Step 4/6  Drivers, scripts and answer file")
        cmds = []
        q = lambda s: '"' + str(s).replace('"', '') + '"'
        for h in edited:
            cmds.append(f"add {q(hives / HIVE_FILE[h])} {q(HIVE_WIM_PATH[h])}")
            for ext in (".LOG1", ".LOG2"):
                cmds.append(f"delete --force {q(HIVE_WIM_PATH[h] + ext)}")
        sc = work / "scripts"
        if sc.exists():
            shutil.rmtree(sc)
        sc.mkdir()
        shutil.copy(ROOT / "scripts" / "firstlogon.ps1", sc / "firstlogon.ps1")
        (sc / "firstlogon.json").write_text(json.dumps({
            "UserName": o["UserName"], "PermanentAutoLogon": bool(o["AutoLogon"]),
            "DisableHibernate": o["Mode"] == "WTG", "AppPatterns": o.get("RemoveApps") or []}, indent=1), encoding="utf-8")
        cmds.append(f"add {q(sc)} \"/Windows/Setup/Scripts\"")
        drivers = [d for d in (o.get("DriverDirs") or []) if d and Path(d).is_dir()]
        for i, d in enumerate(drivers):
            n = len(list(Path(d).rglob("*.inf")))
            if n == 0:
                self.log(f"  no .inf files in {d} (extract .exe/.zip driver packs first)")
                continue
            self.log(f"  staging {n} driver(s) from {d}")
            cmds.append(f"add {q(d)} \"/Windows/Setup/Drivers/pack{i}\"")
        if o.get("IncludeHostDrivers"):
            self.log("  (\"this PC's drivers\" only applies on Windows; ignored on Linux)")
        if o["Mode"] == "WTG":
            ua = work / "unattend.xml"
            ua.write_text(self.unattend(o), encoding="utf-8")
            tag = work / "forge-windows.tag"
            tag.write_text("win11forge\r\n")
            cmds.append(f"add {q(ua)} \"/Windows/Panther/unattend.xml\"")
            cmds.append(f"add {q(tag)} \"/forge-windows.tag\"")
        self.wim_update(stage, cmds)

        if o["Mode"] == "ISO":
            self.make_iso(o, media, work, stage, drivers)
        else:
            self.make_wtg(o, media, work, stage)
        self.log(f"Done in {(time.time() - t0) / 60:.0f} minutes.")

    def wim_update(self, wim, cmds, index="1"):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("\n".join(cmds) + "\n")
            name = f.name
        try:
            with open(name) as fh:
                p = subprocess.run(["wimlib-imagex", "update", str(wim), index], stdin=fh, capture_output=True, text=True)
            if p.returncode != 0:
                raise ForgeError("wimlib-imagex update failed:\n" + (p.stdout + p.stderr)[-1500:])
        finally:
            os.unlink(name)

    # ------------------------------------------------------------------ outputs
    def make_iso(self, o, media, work, stage, drivers):
        self.log("Step 5/6  Compressing image")
        sources = media / "sources"
        if o.get("UseEsd"):
            dst = sources / "install.esd"
            self.run(["wimlib-imagex", "export", stage, "1", dst, "--solid"])
        else:
            dst = sources / "install.wim"
            self.run(["wimlib-imagex", "export", stage, "1", dst, "--compress=LZX"])
        stage.unlink()
        if dst.stat().st_size > SPLIT_LIMIT:
            self.log("  image is over 4 GB, splitting into install.swm parts for FAT32/ISO9660 compatibility")
            if dst.suffix == ".esd":
                wim = sources / "install.wim"
                self.run(["wimlib-imagex", "export", dst, "1", wim, "--compress=LZX"])
                dst.unlink()
                dst = wim
            self.run(["wimlib-imagex", "split", dst, sources / "install.swm", "3800"])
            dst.unlink()
        for i, d in enumerate(drivers):
            shutil.copytree(d, media / "$WinPEDriver$" / f"pack{i}", dirs_exist_ok=True)
        (media / "autounattend.xml").write_text(self.unattend(o), encoding="utf-8")

        self.log("Step 6/6  Writing ISO")
        out = Path(o["OutputIso"]).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        efi = "efi/microsoft/boot/efisys_noprompt.bin"
        if not (media / efi).exists():
            efi = "efi/microsoft/boot/efisys.bin"
        args = ["xorriso", "-as", "mkisofs", "-iso-level", "3", "-full-iso9660-filenames", "-J", "-joliet-long",
                "-V", "WIN11_FORGE", "-o", out]
        if (media / "boot" / "etfsboot.com").exists():
            args += ["-b", "boot/etfsboot.com", "-no-emul-boot", "-boot-load-size", "8", "-c", "boot.catalog", "-hide", "boot.catalog",
                     "-eltorito-alt-boot"]
        args += ["-e", efi, "-no-emul-boot", media]
        self.run(args)
        shutil.rmtree(media, ignore_errors=True)
        self.log(f"ISO ready: {out} ({out.stat().st_size / 1024 ** 3:.1f} GB)")

    def make_wtg(self, o, media, work, stage):
        dev = o["Disk"]
        info = {d["path"]: d for d in self.usb_disks()}
        if dev not in info and not o.get("AllowNonUsb"):
            raise ForgeError(f"{dev} is not a USB disk (or it is the disk Linux runs from). Refusing.")
        if dev in info and info[dev]["size"] < 32 * 1000 ** 3:
            raise ForgeError(f"{dev} is smaller than 32 GB - too small for Windows 11.")

        self.log(f"Step 5/6  Wiping and partitioning {dev}")
        for line in self.run(["lsblk", "-lnpo", "NAME,MOUNTPOINT", dev], capture=True, quiet=True).splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1].strip():
                self.run(["umount", parts[0]], ok=(0, 32))
        self.run(["wipefs", "-a", dev])
        # MBR + FAT32 first partition: boots on UEFI from both "removable" sticks and external SSDs.
        self.run(["parted", "-s", dev, "mklabel", "msdos", "mkpart", "primary", "fat32", "1MiB", "1025MiB",
                  "set", "1", "boot", "on", "mkpart", "primary", "ntfs", "1025MiB", "100%"])
        subprocess.run(["partprobe", dev], capture_output=True)
        subprocess.run(["udevadm", "settle"], capture_output=True)
        time.sleep(2)
        parts = self.partitions(dev)
        if len(parts) < 2:
            raise ForgeError("Partitions did not appear after partitioning.")
        esp, winp = parts[0], parts[1]
        self.run(["mkfs.vfat", "-F", "32", "-n", "SYSTEM", esp])
        self.run(["mkfs.ntfs", "-Q", "-L", "Windows", winp])

        self.log("Applying Windows (this is the long part on a flash drive)")
        self.run(["wimlib-imagex", "apply", stage, "1", winp])

        self.log("Step 6/6  Boot files")
        bootwim = work / "finish-boot.wim"
        if bootwim.exists():
            bootwim.unlink()
        self.run(["wimlib-imagex", "export", media / "sources" / "boot.wim", "1", bootwim, "--boot"])
        fin = ROOT / "winpe-finish"
        self.wim_update(bootwim, [
            f'add "{fin / "winpeshl.ini"}" "/Windows/System32/winpeshl.ini"',
            f'add "{fin / "forge-finish.cmd"}" "/Windows/System32/forge-finish.cmd"',
        ])
        mnt = Path(tempfile.mkdtemp(prefix="forge-esp-"))
        self.run(["mount", esp, mnt])
        try:
            shutil.copytree(media / "efi", mnt / "efi", dirs_exist_ok=True)
            if (media / "boot").exists():
                shutil.copytree(media / "boot", mnt / "boot", dirs_exist_ok=True)
            for f in ("bootmgr", "bootmgr.efi"):
                if (media / f).exists():
                    shutil.copy(media / f, mnt / f)
            (mnt / "sources").mkdir(exist_ok=True)
            shutil.copy(bootwim, mnt / "sources" / "boot.wim")
            (mnt / "forge-esp.tag").write_text("win11forge\r\n")
        finally:
            subprocess.run(["sync"])
            self.run(["umount", mnt])
            mnt.rmdir()
        bootwim.unlink()
        if not o.get("KeepImage"):
            stage.unlink()
        shutil.rmtree(media, ignore_errors=True)
        self.log("Windows To Go drive is ready (UEFI). On its first boot it spends ~30 seconds finishing")
        self.log("its boot files, restarts by itself, then goes straight to the desktop.")


def host_defaults():
    lang = (os.environ.get("LANG") or "en_US.UTF-8").split(".")[0].replace("_", "-")
    if not re.match(r"^[a-z]{2,3}-[A-Z]{2}$", lang):
        lang = "en-US"
    tz = "UTC"
    try:
        z = Path("/etc/timezone").read_text().strip() if Path("/etc/timezone").exists() else os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]
        tz = TZ_MAP.get(z, "UTC")
    except Exception:
        pass
    return {"Locale": lang, "Keyboard": LCID_KEYBOARDS.get(lang, "0409:00000409"), "TimeZone": tz}
