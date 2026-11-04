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
import re
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


# palette for the Tk GUI
BG, CARD, LINE = "#F3F5F9", "#FFFFFF", "#DDE2EA"
TEXT, MUTED, ACCENT, ACCENT_DARK = "#1F2937", "#6B7280", "#2563EB", "#1D4ED8"
OK, BAD, LOG_BG, LOG_FG = "#15803D", "#B91C1C", "#0F172A", "#CBD5E1"
STEP_RE = re.compile(r"^Step (\d+)/(\d+)\s+(.*)")


def gui():
    import time
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter import font as tkfont

    root = tk.Tk()
    root.title("Win11Forge")
    root.geometry("1180x800")
    root.minsize(980, 620)
    root.configure(background=BG)

    # ---------------------------------------------------------------- look
    base = tkfont.nametofont("TkDefaultFont")
    base.configure(size=10)
    for name in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=base.cget("family"), size=10)
        except tk.TclError:
            pass
    fam = base.cget("family")
    f_title = (fam, 15, "bold")
    f_head = (fam, 11, "bold")
    f_small = (fam, 9)
    f_status = (fam, 11, "bold")
    mono = "DejaVu Sans Mono" if "DejaVu Sans Mono" in tkfont.families() else "Monospace"

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG, foreground=TEXT, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    focuscolor=ACCENT, troughcolor="#E5E9F0")
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD)
    style.configure("Head.TLabel", background=CARD, font=f_head)
    style.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=f_small)
    style.configure("Status.TLabel", background=CARD, font=f_status)
    style.configure("Sub.TLabel", background=CARD, foreground=MUTED)
    style.configure("TCheckbutton", background=CARD, padding=(0, 3), indicatorforeground=ACCENT, indicatorbackground="#FFFFFF")
    style.map("TCheckbutton", background=[("active", CARD)], foreground=[("disabled", "#A0A7B4")])
    style.configure("TRadiobutton", background=CARD, padding=(0, 4), font=(fam, 10, "bold"))
    style.map("TRadiobutton", background=[("active", CARD)])
    style.configure("TEntry", padding=(6, 4), fieldbackground="#FFFFFF")
    style.map("TEntry", fieldbackground=[("disabled", "#F1F3F6")], bordercolor=[("focus", ACCENT)],
              lightcolor=[("focus", ACCENT)])
    style.configure("TCombobox", padding=(6, 4), arrowsize=14)
    style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF"), ("disabled", "#F1F3F6")],
              bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
    style.configure("TButton", padding=(12, 6), background="#EEF1F5")
    style.map("TButton", background=[("active", "#E2E7EE"), ("disabled", "#F1F3F6")])
    style.configure("Accent.TButton", padding=(26, 8), background=ACCENT, foreground="white",
                    bordercolor=ACCENT_DARK, font=(fam, 10, "bold"))
    style.map("Accent.TButton", background=[("disabled", "#93B4F5"), ("pressed", ACCENT_DARK), ("active", ACCENT_DARK)],
              foreground=[("disabled", "#EEF3FE")])
    style.configure("Horizontal.TProgressbar", background=ACCENT, bordercolor=LINE, thickness=10,
                    lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Vertical.TScrollbar", arrowsize=12, background="#E5E9F0", troughcolor=BG, bordercolor=BG)
    root.option_add("*TCombobox*Listbox.font", base)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    logq = queue.Queue()
    prog = {"pct": None}
    eng = Engine(log=logq.put, progress=lambda p: prog.__setitem__("pct", p))
    d = host_defaults()
    v = {k: tk.StringVar(value=val) for k, val in {
        "iso": "", "drivers": "", "updates": "", "user": "User", "pass": "", "pc": "",
        "locale": d["Locale"], "kbd": d["Keyboard"], "tz": d["TimeZone"],
        "out": str(Path.home() / "Win11-Forge.iso"), "work": str(Path.home() / "win11forge-work"),
        "mode": "ISO", "usb": ""}.items()}
    b = {k: tk.BooleanVar(value=val) for k, val in {
        "ads": True, "autologon": True, "bypass": True, "esd": False, "wipe": False, "portable": False}.items()}
    state = {"editions": [], "disks": [], "busy": None, "t0": 0.0, "step": 0, "steps": 6}

    # ---------------------------------------------------------------- header
    header = tk.Frame(root, background=LOG_BG, height=58)
    header.pack(fill="x")
    header.pack_propagate(False)
    tk.Label(header, text="Win11Forge", font=f_title, background=LOG_BG, foreground="white").pack(side="left", padx=(20, 10))
    tk.Label(header, text="A lighter Windows 11 that goes straight to the desktop", font=(fam, 10),
             background=LOG_BG, foreground="#94A3B8").pack(side="left", pady=(4, 0))

    body = ttk.Frame(root, padding=(14, 14, 14, 14))
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, weight=0, minsize=540)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)

    # ---------------------------------------------------------------- left: scrollable settings
    left_wrap = ttk.Frame(body)
    left_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
    sc = tk.Canvas(left_wrap, width=540, highlightthickness=0, background=BG, borderwidth=0)
    vsb = ttk.Scrollbar(left_wrap, orient="vertical", command=sc.yview)
    sc.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    sc.pack(side="left", fill="both", expand=True)
    left = ttk.Frame(sc)
    left_id = sc.create_window((0, 0), window=left, anchor="nw")
    left.bind("<Configure>", lambda e: sc.configure(scrollregion=sc.bbox("all")))
    sc.bind("<Configure>", lambda e: sc.itemconfigure(left_id, width=e.width))

    def wheel_target(w):
        """Scroll the innermost scrollable area under the pointer."""
        while w is not None:
            if getattr(w, "_scroll", None):
                return w._scroll
            w = w.master
        return None

    def on_wheel(e):
        try:
            w = root.winfo_containing(e.x_root, e.y_root)
        except (KeyError, tk.TclError):
            return
        if w is None or isinstance(w, (ttk.Combobox, tk.Listbox)):
            return
        t = wheel_target(w)
        if t is None:
            return
        step = -1 if (e.num == 4 or e.delta > 0) else 1
        t.yview_scroll(step * 3, "units")
    root.bind_all("<Button-4>", on_wheel)
    root.bind_all("<Button-5>", on_wheel)
    root.bind_all("<MouseWheel>", on_wheel)
    left_wrap._scroll = sc

    def card(title, hint=None):
        outer = tk.Frame(left, background=LINE, padx=1, pady=1)  # 1px border
        outer.pack(fill="x", pady=(0, 12))
        g = ttk.Frame(outer, style="Card.TFrame", padding=(16, 12, 16, 14))
        g.pack(fill="both", expand=True)
        ttk.Label(g, text=title, style="Head.TLabel").pack(anchor="w")
        if hint:
            ttk.Label(g, text=hint, style="Hint.TLabel", wraplength=470, justify="left").pack(anchor="w", pady=(2, 0))
        ttk.Frame(g, style="Card.TFrame", height=8).pack(fill="x")
        return g

    def row(parent, label, var, browse=None, show=None, browse_text="Browse"):
        f = ttk.Frame(parent, style="Card.TFrame")
        f.pack(fill="x", pady=3)
        ttk.Label(f, text=label, width=14, style="Card.TLabel").pack(side="left")
        e = ttk.Entry(f, textvariable=var, show=show or "")
        e.pack(side="left", fill="x", expand=True)
        widgets = [e]
        if browse:
            bt = ttk.Button(f, text=browse_text, command=browse, width=8)
            bt.pack(side="left", padx=(6, 0))
            widgets.append(bt)
        return widgets

    # 1. source
    g = card("1  Windows 11 source", "Get the official ISO from microsoft.com/software-download/windows11")

    def pick_iso():
        p = filedialog.askopenfilename(filetypes=[("Windows ISO", "*.iso"), ("All files", "*")])
        if p:
            v["iso"].set(p)
            load_editions()

    row(g, "ISO file", v["iso"], pick_iso)
    f = ttk.Frame(g, style="Card.TFrame")
    f.pack(fill="x", pady=3)
    ttk.Label(f, text="Edition", width=14, style="Card.TLabel").pack(side="left")
    cmb_ed = ttk.Combobox(f, state="readonly", width=10)  # fixed width: long names must not resize the layout
    cmb_ed.pack(side="left", fill="x", expand=True)
    cmb_ed.set("Choose an ISO first")

    def load_editions():
        logq.put("Reading editions (copies the install image out of the ISO, about a minute)...")
        cmb_ed.set("Reading editions...")
        set_busy("Reading the ISO...")

        def job():
            try:
                eds = eng.editions(v["iso"].get(), scratch=v["work"].get())
                root.after(0, lambda: set_editions(eds))
            except Exception as ex:  # noqa: BLE001
                logq.put(f"Could not read the ISO: {ex}")
                root.after(0, lambda: (cmb_ed.set("Could not read this ISO"), set_idle("Could not read the ISO", BAD)))
        threading.Thread(target=job, daemon=True).start()

    def set_editions(eds):
        state["editions"] = eds
        cmb_ed["values"] = [f"{e['Name']}   (build {e['Build']}, {e['Language']})" for e in eds]
        pro = next((i for i, e in enumerate(eds) if e["EditionId"] == "Professional"), 0)
        if eds:
            cmb_ed.current(pro)
        else:
            cmb_ed.set("No editions found")
        logq.put(f"Found {len(eds)} editions.")
        set_idle(f"Ready. {len(eds)} editions found.")

    # 2. debloat
    g = card("2  Debloat", "Camera and Sound Recorder are kept so the webcam and microphone work out of the box.")
    ttk.Checkbutton(g, text="Remove ads, tips, Widgets, Copilot, web search and telemetry",
                    variable=b["ads"]).pack(anchor="w")
    hdr = ttk.Frame(g, style="Card.TFrame")
    hdr.pack(fill="x", pady=(8, 4))
    ttk.Label(hdr, text="Apps to remove", style="Card.TLabel").pack(side="left")
    apps = []

    def set_all(val):
        for _, var in apps:
            var.set(val)
    for txt, val in (("None", False), ("All", True)):
        tk.Label(hdr, text=txt, foreground=ACCENT, background=CARD, cursor="hand2", font=f_small).pack(side="right", padx=(8, 0))
        hdr.winfo_children()[-1].bind("<Button-1>", lambda e, val=val: set_all(val))
    lf = tk.Frame(g, background=LINE, padx=1, pady=1)
    lf.pack(fill="x")
    lin = tk.Frame(lf, background=CARD)
    lin.pack(fill="both", expand=True)
    ac = tk.Canvas(lin, height=170, highlightthickness=0, background=CARD, borderwidth=0)
    asb = ttk.Scrollbar(lin, orient="vertical", command=ac.yview)
    inner = ttk.Frame(ac, style="Card.TFrame", padding=(8, 4))
    inner.bind("<Configure>", lambda e: ac.configure(scrollregion=ac.bbox("all")))
    inner_id = ac.create_window((0, 0), window=inner, anchor="nw")
    ac.bind("<Configure>", lambda e: ac.itemconfigure(inner_id, width=e.width))
    ac.configure(yscrollcommand=asb.set)
    asb.pack(side="right", fill="y")
    ac.pack(side="left", fill="both", expand=True)
    lf._scroll = ac
    inner.columnconfigure(0, weight=1, uniform="a")
    inner.columnconfigure(1, weight=1, uniform="a")
    for i, a in enumerate(load_json("apps.json")["apps"]):
        var = tk.BooleanVar(value=a["remove"])
        ttk.Checkbutton(inner, text=a["name"], variable=var).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 8))
        apps.append((a["match"], var))

    # 3. drivers
    g = card("3  Drivers", "Windows already has drivers for most touchpads, touchscreens, webcams and audio, and gets "
                           "the rest from Windows Update. Add extracted Intel RST/VMD, Wi-Fi and OEM driver packs "
                           "(.inf) here. Pick more than one folder to add them all.")
    row(g, "Driver folders", v["drivers"],
        lambda: v["drivers"].set(";".join(x for x in [v["drivers"].get(), filedialog.askdirectory()] if x)), browse_text="Add")

    # 4. first boot
    g = card("4  First boot", "No setup screens: the account below is created and Windows opens on the desktop.")
    row(g, "User name", v["user"])
    row(g, "Password", v["pass"], show="•")
    row(g, "Computer name", v["pc"])
    row(g, "Language", v["locale"])
    row(g, "Keyboard", v["kbd"])
    f = ttk.Frame(g, style="Card.TFrame")
    f.pack(fill="x", pady=3)
    ttk.Label(f, text="Time zone", width=14, style="Card.TLabel").pack(side="left")
    ttk.Combobox(f, textvariable=v["tz"], values=WINDOWS_TZ_CHOICES, width=10).pack(side="left", fill="x", expand=True)
    ttk.Frame(g, style="Card.TFrame", height=4).pack(fill="x")
    ttk.Checkbutton(g, text="Sign in automatically every boot", variable=b["autologon"]).pack(anchor="w")
    ttk.Checkbutton(g, text="Allow older laptops (skip TPM / Secure Boot / CPU / RAM checks)", variable=b["bypass"]).pack(anchor="w")

    # 5. output
    g = card("5  Output")
    ttk.Radiobutton(g, text="Bootable ISO  (install on any laptop)", variable=v["mode"], value="ISO").pack(anchor="w")
    iso_f = ttk.Frame(g, style="Card.TFrame", padding=(24, 0, 0, 6))
    iso_f.pack(fill="x")
    iso_w = row(iso_f, "Save ISO as", v["out"],
                lambda: v["out"].set(filedialog.asksaveasfilename(defaultextension=".iso") or v["out"].get()))

    def wipe_warn():
        if b["wipe"].get() and not messagebox.askyesno("Win11Forge", "The installer will erase disk 0 of any laptop that boots it, without asking. Keep this on?"):
            b["wipe"].set(False)
    iso_w.append(ttk.Checkbutton(iso_f, text="Maximum compression (ESD, slower)", variable=b["esd"]))
    iso_w[-1].pack(anchor="w")
    iso_w.append(ttk.Checkbutton(iso_f, text="Fully hands-off: wipe the laptop's disk 0 without asking",
                                 variable=b["wipe"], command=wipe_warn))
    iso_w[-1].pack(anchor="w")

    ttk.Radiobutton(g, text="Windows To Go  (USB flash drive or external SSD, needs sudo)", variable=v["mode"], value="WTG").pack(anchor="w")
    wtg_f = ttk.Frame(g, style="Card.TFrame", padding=(24, 0, 0, 6))
    wtg_f.pack(fill="x")
    f = ttk.Frame(wtg_f, style="Card.TFrame")
    f.pack(fill="x", pady=3)
    ttk.Label(f, text="USB drive", width=14, style="Card.TLabel").pack(side="left")
    cmb_usb = ttk.Combobox(f, textvariable=v["usb"], state="readonly", width=10)
    cmb_usb.pack(side="left", fill="x", expand=True)

    def refresh_usb():
        state["disks"] = eng.usb_disks()
        cmb_usb["values"] = [x["label"] for x in state["disks"]]
        if state["disks"]:
            cmb_usb.current(0)
        else:
            cmb_usb.set("")
            logq.put("No USB disks found. Plug in the drive and press Refresh.")
    bt_usb = ttk.Button(f, text="Refresh", command=refresh_usb, width=8)
    bt_usb.pack(side="left", padx=(6, 0))
    cb_port = ttk.Checkbutton(wtg_f, text="Strict Windows To Go mode (PortableOperatingSystem flag)", variable=b["portable"])
    cb_port.pack(anchor="w")
    wtg_w = [cmb_usb, bt_usb, cb_port]

    tk.Frame(g, background=LINE, height=1).pack(fill="x", pady=(8, 10))
    row(g, "Work folder", v["work"], lambda: v["work"].set(filedialog.askdirectory() or v["work"].get()))
    ttk.Label(g, text="Needs about 25 GB free.", style="Hint.TLabel").pack(anchor="w", padx=(0, 0))

    def mode_changed(*_):
        # enable/disable instead of hiding, so nothing jumps around
        iso_on = v["mode"].get() == "ISO"
        for w in iso_w:
            w.state(["!disabled"] if iso_on else ["disabled"])
        for w in wtg_w:
            if w is cmb_usb:
                w.state(["readonly", "!disabled"] if not iso_on else ["disabled"])
            else:
                w.state(["!disabled"] if not iso_on else ["disabled"])
    v["mode"].trace_add("write", mode_changed)
    mode_changed()

    # ---------------------------------------------------------------- right: status + log
    rwrap = tk.Frame(body, background=LINE, padx=1, pady=1)
    rwrap.grid(row=0, column=1, sticky="nsew")
    right = ttk.Frame(rwrap, style="Card.TFrame", padding=(16, 14, 16, 14))
    right.pack(fill="both", expand=True)
    right.columnconfigure(0, weight=1)
    right.rowconfigure(3, weight=1)

    top = ttk.Frame(right, style="Card.TFrame")
    top.grid(row=0, column=0, sticky="ew")
    status = ttk.Label(top, text="Ready", style="Status.TLabel")
    status.pack(side="left")
    elapsed = ttk.Label(top, text="", style="Sub.TLabel")
    elapsed.pack(side="right")
    detail = ttk.Label(right, text="Choose an ISO, check the options on the left, then press Build.", style="Sub.TLabel", width=1)
    detail.grid(row=1, column=0, sticky="ew", pady=(2, 8))
    bar = ttk.Progressbar(right, mode="determinate", maximum=1000)
    bar.grid(row=2, column=0, sticky="ew", pady=(0, 12))

    lw = tk.Frame(right, background=LOG_BG)
    lw.grid(row=3, column=0, sticky="nsew")
    log = tk.Text(lw, font=(mono, 9), background=LOG_BG, foreground=LOG_FG, insertbackground=LOG_FG,
                  relief="flat", borderwidth=0, highlightthickness=0, padx=10, pady=8, wrap="word",
                  state="disabled", cursor="arrow", selectbackground="#334155")
    lsb = ttk.Scrollbar(lw, orient="vertical", command=log.yview)
    log.configure(yscrollcommand=lsb.set)
    lsb.pack(side="right", fill="y")
    log.pack(side="left", fill="both", expand=True)
    lw._scroll = log
    log.tag_configure("cmd", foreground="#64748B")
    log.tag_configure("step", foreground="#FFFFFF", font=(mono, 9, "bold"), spacing1=6)
    log.tag_configure("err", foreground="#FCA5A5")
    log.tag_configure("ok", foreground="#86EFAC")

    btns = ttk.Frame(right, style="Card.TFrame")
    btns.grid(row=4, column=0, sticky="ew", pady=(12, 0))

    def set_busy(text):
        state["busy"] = "indet"
        status.configure(text=text, foreground=TEXT)
        bar.configure(mode="indeterminate")
        bar.start(15)

    def set_idle(text, color=TEXT):
        state["busy"] = None
        bar.stop()
        bar.configure(mode="determinate")
        status.configure(text=text, foreground=color)

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
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        state.update(busy="build", t0=time.time(), step=0, steps=6)
        prog["pct"] = None
        bar.configure(mode="determinate", value=0)
        status.configure(text="Starting...", foreground=TEXT)
        detail.configure(text="Checking tools and free space")

        def job():
            try:
                eng.build(o)
                logq.put(("__done__", True))
            except Exception as ex:  # noqa: BLE001
                logq.put(f"ERROR: {ex}")
                logq.put(("__done__", False))
        threading.Thread(target=job, daemon=True).start()

    ttk.Button(btns, text="Save answer file only", command=save_unattend).pack(side="left")
    btn_build = ttk.Button(btns, text="Build", style="Accent.TButton", command=build)
    btn_build.pack(side="right")

    def fmt_time(s):
        s = int(s)
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    def tag_for(line):
        if line.startswith("ERROR"):
            return "err"
        if STEP_RE.match(line):
            return "step"
        if line.startswith("> "):
            return "cmd"
        if line.startswith(("ISO ready", "Done in", "Windows To Go drive is ready")):
            return "ok"
        return ()

    def finish(ok):
        dt = fmt_time(time.time() - state["t0"])
        state["busy"] = None
        if ok:
            bar.configure(value=1000)
            status.configure(text="Finished", foreground=OK)
            detail.configure(text=f"Completed in {dt}. The log shows where the output is.")
        else:
            status.configure(text="Stopped with an error", foreground=BAD)
            detail.configure(text="See the red lines in the log.")
        btn_build.state(["!disabled"])
        messagebox.showinfo("Win11Forge", "Finished. See the log for where the output is." if ok else "The build stopped with an error. See the log.")

    def pump():
        # Drain everything queued, then touch the widget once: no per-line redraws.
        lines, done = [], None
        try:
            while True:
                m = logq.get_nowait()
                if isinstance(m, tuple):
                    done = m[1]
                    break
                lines.append(str(m))
        except queue.Empty:
            pass
        if lines:
            at_bottom = log.yview()[1] >= 0.999
            log.configure(state="normal")
            for line in lines:
                log.insert("end", line + "\n", tag_for(line))
                sm = STEP_RE.match(line)
                if sm and state["busy"] == "build":
                    state["step"], state["steps"] = int(sm.group(1)), int(sm.group(2))
                    prog["pct"] = None
                    status.configure(text=f"Step {sm.group(1)} of {sm.group(2)}:  {sm.group(3)}")
                elif state["busy"] == "build" and line.strip() and not line.startswith(">"):
                    detail.configure(text=line.strip()[:110])
            n = int(log.index("end-1c").split(".")[0])
            if n > 5000:  # keep the widget fast on very long builds
                log.delete("1.0", f"{n - 4000}.0")
            log.configure(state="disabled")
            if at_bottom:
                log.see("end")
        if state["busy"] == "build":
            st = state["step"]
            frac = (prog["pct"] or 0) / 100.0 if st else 0.0
            target = int(1000 * (max(st - 1, 0) + frac) / state["steps"])
            if target > int(bar.cget("value")):
                bar.configure(value=target)
            elapsed.configure(text=fmt_time(time.time() - state["t0"]))
        if done is not None:
            finish(done)
        root.after(120, pump)

    missing = Engine.missing_tools("WTG")
    if missing:
        logq.put("Missing tools: " + ", ".join(missing))
        for line in INSTALL_HINT.splitlines():
            logq.put("  " + line)
        status.configure(text="Some tools are missing", foreground=BAD)
        detail.configure(text="Install them with the command shown in the log, then restart Win11Forge.")
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
