#!/usr/bin/env python3
"""
Win11Forge for Linux.

  GUI:        sudo ./win11forge.sh              (sudo only needed for Windows To Go)
  Headless:   ./win11forge.sh --config build.json
  Editions:   ./win11forge.sh --list-editions Win11.iso
"""
import argparse
import json
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forge_engine import Engine, ForgeError, INSTALL_HINT, WINDOWS_TZ_CHOICES, host_defaults, load_json  # noqa: E402


def cli():
    ap = argparse.ArgumentParser(description="Win11Forge: debloated Windows 11 ISO or Windows To Go, straight to the desktop.")
    ap.add_argument("--config", help="JSON file with build options (same keys as the Windows version)")
    ap.add_argument("--list-editions", metavar="ISO")
    ap.add_argument("--unattend", metavar="OUT.xml", help="only write the answer file, using --config options")
    a = ap.parse_args()
    eng = Engine()
    if a.list_editions:
        for e in eng.editions(a.list_editions):
            print(f"{e['Index']:>2}  {e['Name']}  [{e['EditionId']}]  build {e['Build']}  {e['Language']}")
        return 0
    if a.config:
        o = json.loads(Path(a.config).read_text(encoding="utf-8"))
        if a.unattend:
            o.setdefault("Mode", "ISO")
            Path(a.unattend).write_text(eng.unattend(o), encoding="utf-8")
            print("wrote", a.unattend)
            return 0
        eng.build(o)
        return 0
    return None


def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("Win11Forge - lighter Windows 11, straight to the desktop")
    root.geometry("1100x760")
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    logq = queue.Queue()
    eng = Engine(log=logq.put)
    d = host_defaults()
    v = {k: tk.StringVar(value=val) for k, val in {
        "iso": "", "drivers": "", "updates": "", "user": "User", "pass": "", "pc": "",
        "locale": d["Locale"], "kbd": d["Keyboard"], "tz": d["TimeZone"],
        "out": str(Path.home() / "Win11-Forge.iso"), "work": str(Path.home() / "win11forge-work"),
        "mode": "ISO", "usb": ""}.items()}
    b = {k: tk.BooleanVar(value=val) for k, val in {
        "ads": True, "autologon": True, "bypass": True, "esd": False, "wipe": False, "portable": False}.items()}
    state = {"editions": [], "disks": []}

    paned = ttk.PanedWindow(root, orient="horizontal")
    paned.pack(fill="both", expand=True, padx=10, pady=10)
    left = ttk.Frame(paned)
    right = ttk.Frame(paned)
    paned.add(left, weight=0)
    paned.add(right, weight=1)

    def row(parent, label, var, browse=None, show=None):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        ttk.Label(f, text=label, width=14).pack(side="left")
        e = ttk.Entry(f, textvariable=var, show=show or "")
        e.pack(side="left", fill="x", expand=True)
        if browse:
            ttk.Button(f, text="Browse...", command=browse).pack(side="left", padx=(6, 0))
        return e

    def box(title):
        g = ttk.LabelFrame(left, text=title, padding=8)
        g.pack(fill="x", pady=(0, 8))
        return g

    # 1. source
    g = box("1. Windows 11 source")

    def pick_iso():
        p = filedialog.askopenfilename(filetypes=[("Windows ISO", "*.iso"), ("All files", "*")])
        if p:
            v["iso"].set(p)
            load_editions()

    row(g, "ISO", v["iso"], pick_iso)
    f = ttk.Frame(g)
    f.pack(fill="x", pady=2)
    ttk.Label(f, text="Edition", width=14).pack(side="left")
    cmb_ed = ttk.Combobox(f, state="readonly")
    cmb_ed.pack(side="left", fill="x", expand=True)
    ttk.Label(g, foreground="#5B6472", text="Get the ISO from microsoft.com/software-download/windows11").pack(anchor="w")

    def load_editions():
        logq.put("Reading editions (copies the install image out of the ISO, ~1 minute)...")

        def job():
            try:
                eds = eng.editions(v["iso"].get(), scratch=v["work"].get())
                root.after(0, lambda: set_editions(eds))
            except Exception as ex:  # noqa: BLE001
                logq.put(f"Could not read the ISO: {ex}")
        threading.Thread(target=job, daemon=True).start()

    def set_editions(eds):
        state["editions"] = eds
        cmb_ed["values"] = [f"{e['Index']}. {e['Name']}  -  {e['EditionId']}  build {e['Build']}  [{e['Language']}]" for e in eds]
        pro = next((i for i, e in enumerate(eds) if e["EditionId"] == "Professional"), 0)
        if eds:
            cmb_ed.current(pro)
        logq.put(f"Found {len(eds)} editions.")

    # 2. debloat
    g = box("2. Debloat")
    ttk.Checkbutton(g, text="Remove ads, tips, suggested apps, Widgets, Copilot, web search and telemetry", variable=b["ads"]).pack(anchor="w")
    ttk.Label(g, text="Apps to remove (Camera and Sound Recorder kept for webcam and mic):").pack(anchor="w", pady=(4, 2))
    lf = ttk.Frame(g)
    lf.pack(fill="x")
    canvas = tk.Canvas(lf, height=150, highlightthickness=0, background="white")
    sb = ttk.Scrollbar(lf, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, background="white")
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="x", expand=True)
    sb.pack(side="right", fill="y")
    apps = []
    for a in load_json("apps.json")["apps"]:
        var = tk.BooleanVar(value=a["remove"])
        tk.Checkbutton(inner, text=a["name"], variable=var, background="white", anchor="w").pack(fill="x", anchor="w")
        apps.append((a["match"], var))

    # 3. drivers
    g = box("3. Drivers (for wide laptop support)")
    row(g, "Driver folder", v["drivers"], lambda: v["drivers"].set(";".join(x for x in [v["drivers"].get(), filedialog.askdirectory()] if x)))
    ttk.Label(g, foreground="#5B6472", wraplength=480, justify="left",
              text="Windows already has drivers for most touchpads, touchscreens, webcams and audio, and gets the rest "
                   "from Windows Update. Add extracted Intel RST/VMD, Wi-Fi and OEM driver packs (.inf) here.").pack(anchor="w")

    # 4. first boot
    g = box("4. First boot (no setup screens)")
    row(g, "User name", v["user"])
    row(g, "Password", v["pass"], show="*")
    row(g, "Computer name", v["pc"])
    row(g, "Language", v["locale"])
    row(g, "Keyboard", v["kbd"])
    f = ttk.Frame(g)
    f.pack(fill="x", pady=2)
    ttk.Label(f, text="Time zone", width=14).pack(side="left")
    ttk.Combobox(f, textvariable=v["tz"], values=WINDOWS_TZ_CHOICES).pack(side="left", fill="x", expand=True)
    ttk.Checkbutton(g, text="Sign in automatically every boot", variable=b["autologon"]).pack(anchor="w")
    ttk.Checkbutton(g, text="Allow older laptops (skip TPM / Secure Boot / CPU / RAM checks)", variable=b["bypass"]).pack(anchor="w")

    # 5. output
    g = box("5. Output")
    ttk.Radiobutton(g, text="Bootable ISO (install on any laptop)", variable=v["mode"], value="ISO").pack(anchor="w")
    iso_f = ttk.Frame(g, padding=(20, 0, 0, 4))
    iso_f.pack(fill="x")
    row(iso_f, "Save ISO as", v["out"], lambda: v["out"].set(filedialog.asksaveasfilename(defaultextension=".iso") or v["out"].get()))
    ttk.Checkbutton(iso_f, text="Maximum compression (ESD)", variable=b["esd"]).pack(anchor="w")

    def wipe_warn():
        if b["wipe"].get() and not messagebox.askyesno("Win11Forge", "The installer will erase disk 0 of any laptop that boots it, without asking. Keep this on?"):
            b["wipe"].set(False)
    ttk.Checkbutton(iso_f, text="Fully hands-off: wipe the laptop's disk 0 without asking", variable=b["wipe"], command=wipe_warn).pack(anchor="w")
    ttk.Radiobutton(g, text="Windows To Go (USB flash drive or external SSD, needs sudo)", variable=v["mode"], value="WTG").pack(anchor="w")
    wtg_f = ttk.Frame(g, padding=(20, 0, 0, 0))
    wtg_f.pack(fill="x")
    f = ttk.Frame(wtg_f)
    f.pack(fill="x")
    ttk.Label(f, text="USB drive", width=14).pack(side="left")
    cmb_usb = ttk.Combobox(f, textvariable=v["usb"], state="readonly")
    cmb_usb.pack(side="left", fill="x", expand=True)

    def refresh_usb():
        state["disks"] = eng.usb_disks()
        cmb_usb["values"] = [x["label"] for x in state["disks"]]
        if state["disks"]:
            cmb_usb.current(0)
        else:
            logq.put("No USB disks found. Plug in the drive and press Refresh.")
    ttk.Button(f, text="Refresh", command=refresh_usb).pack(side="left", padx=(6, 0))
    ttk.Checkbutton(wtg_f, text="Strict Windows To Go mode (PortableOperatingSystem flag)", variable=b["portable"]).pack(anchor="w")
    row(g, "Work folder", v["work"])

    # log
    ttk.Label(right, text="Log").pack(anchor="w")
    log = scrolledtext.ScrolledText(right, font=("Monospace", 9), background="#0F172A", foreground="#E2E8F0", insertbackground="white")
    log.pack(fill="both", expand=True)
    bar = ttk.Progressbar(right, mode="indeterminate")
    bar.pack(fill="x", pady=6)
    btns = ttk.Frame(right)
    btns.pack(anchor="e")

    def options():
        o = {
            "IsoPath": v["iso"].get(), "Mode": v["mode"].get(), "WorkDir": v["work"].get(),
            "RemoveApps": [m for m, var in apps if var.get()], "RemoveAds": b["ads"].get(), "UseEsd": b["esd"].get(),
            "DriverDirs": [x.strip() for x in v["drivers"].get().split(";") if x.strip()],
            "UserName": v["user"].get().strip(), "Password": v["pass"].get(), "ComputerName": v["pc"].get().strip(),
            "Locale": v["locale"].get().strip(), "Keyboard": v["kbd"].get().strip(), "TimeZone": v["tz"].get().strip(),
            "AutoLogon": b["autologon"].get(), "BypassHardware": b["bypass"].get(), "OutputIso": v["out"].get(),
            "WipeDisk0": b["wipe"].get(), "PortableMode": b["portable"].get(),
        }
        if cmb_ed.current() >= 0 and state["editions"]:
            e = state["editions"][cmb_ed.current()]
            o["ImageIndex"], o["EditionId"] = e["Index"], e["EditionId"]
        if cmb_usb.current() >= 0 and state["disks"]:
            o["Disk"] = state["disks"][cmb_usb.current()]["path"]
        return o

    def save_unattend():
        p = filedialog.asksaveasfilename(initialfile="autounattend.xml", defaultextension=".xml")
        if p:
            Path(p).write_text(eng.unattend(options()), encoding="utf-8")
            logq.put(f"Saved {p}. Put it in the root of any Windows 11 USB installer.")

    def build():
        o = options()
        if not o["IsoPath"]:
            return messagebox.showinfo("Win11Forge", "Pick a Windows 11 ISO first.")
        if "ImageIndex" not in o:
            return messagebox.showinfo("Win11Forge", "Pick an edition (it loads after you choose the ISO).")
        if o["Mode"] == "WTG":
            if "Disk" not in o:
                return messagebox.showinfo("Win11Forge", "Pick the USB drive.")
            if not messagebox.askyesno("Win11Forge", f"Everything on\n\n    {cmb_usb.get()}\n\nwill be erased. Continue?", icon="warning"):
                return
        btn_build.state(["disabled"])
        bar.start(12)
        log.delete("1.0", "end")

        def job():
            try:
                eng.build(o)
                logq.put(("__done__", True))
            except Exception as ex:  # noqa: BLE001
                logq.put(f"ERROR: {ex}")
                logq.put(("__done__", False))
        threading.Thread(target=job, daemon=True).start()

    ttk.Button(btns, text="Save answer file only", command=save_unattend).pack(side="left", padx=4)
    btn_build = ttk.Button(btns, text="   Build   ", command=build)
    btn_build.pack(side="left")

    def pump():
        try:
            while True:
                m = logq.get_nowait()
                if isinstance(m, tuple):
                    bar.stop()
                    btn_build.state(["!disabled"])
                    messagebox.showinfo("Win11Forge", "Finished. See the log for where the output is." if m[1] else "The build stopped with an error. See the log.")
                    continue
                log.insert("end", str(m) + "\n")
                log.see("end")
        except queue.Empty:
            pass
        root.after(250, pump)

    missing = Engine.missing_tools("WTG")
    if missing:
        logq.put("Missing tools: " + ", ".join(missing))
        for line in INSTALL_HINT.splitlines():
            logq.put("  " + line)
    pump()
    root.mainloop()


if __name__ == "__main__":
    try:
        r = cli()
        if r is None:
            gui()
    except ForgeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
