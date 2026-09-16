"""Small real Windows host/client GUI for the KerMP sidecar."""
from __future__ import annotations

import asyncio
import json
import queue
import socket
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from .identity import IdentityStore
from .net import KerMPHost, KerMPClient
from .runtime import HostRuntime, ClientRuntime


def app_data_path() -> Path:
    root = Path.home() / "AppData" / "Local" / "KerMP"
    root.mkdir(parents=True, exist_ok=True)
    return root / "gui.json"


def load_settings() -> dict:
    try:
        return json.loads(app_data_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"display_name": "Player", "host_ip": "127.0.0.1", "lan_port": 17653, "bridge_port": 17654}


def save_settings(values: dict) -> None:
    app_data_path().write_text(json.dumps(values, indent=2), encoding="utf-8")


def detected_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


@dataclass
class StatusModel:
    mode: str = ""
    running: bool = False
    lan_state: str = "Disconnected"
    bridge_state: str = "Waiting"
    player_id: str = ""
    display_name: str = ""
    session_id: str = ""
    connected_players: str = ""
    host_address: str = ""
    active_sim_id: str = "unknown"
    travel_phase: str = "idle"
    travel_epoch: int = 0
    build_owner: str = "none"
    view_updates_sent: int = 0
    view_updates_received: int = 0
    last_error: str = ""
    compatibility: str = "Checking"
    save_progress: str = "0%"
    readiness: str = "Not ready"
    clock: str = "Speed 1"
    native: str = "Unavailable"


class RuntimeController:
    def __init__(self, publish):
        self.publish = publish
        self.events = queue.Queue()
        self.thread = None
        self.loop = None
        self.runtime = None
        self.stop_event = None

    def start(self, mode, name, host_ip, lan_port, bridge_port):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._thread_main, args=(mode, name, host_ip, lan_port, bridge_port), daemon=True)
        self.thread.start()

    def _thread_main(self, mode, name, host_ip, lan_port, bridge_port):
        asyncio.run(self._run(mode, name, host_ip, lan_port, bridge_port))

    async def _run(self, mode, name, host_ip, lan_port, bridge_port):
        self.loop = asyncio.get_running_loop()
        ident = IdentityStore().load_or_create(name)
        self.stop_event = asyncio.Event()
        try:
            if mode == "host":
                net = KerMPHost(ident.player_id, ident.display_name, "0.0.0.0", lan_port)
                self.runtime = HostRuntime(net, bridge_port)
                await self.runtime.start()
                self.publish("started", {"mode": "HOSTING", "player_id": ident.player_id,
                    "address": "%s:%s" % (detected_lan_ip(), lan_port)})
                await self.stop_event.wait()
                await self.runtime.stop()
            else:
                net = KerMPClient(ident.player_id, ident.display_name, host_ip, lan_port)
                self.runtime = ClientRuntime(net, bridge_port)
                welcome = await self.runtime.start()
                self.publish("started", {"mode": "CONNECTED", "player_id": ident.player_id,
                    "session_id": welcome.payload.get("session_id", ""),
                    "players": ", ".join(welcome.payload.get("players", []))})
                listen_task = asyncio.create_task(net.listen())
                await self.stop_event.wait()
                listen_task.cancel()
                await self.runtime.stop()
        except Exception as exc:
            self.publish("error", str(exc))
        finally:
            self.runtime = None
            self.loop = None
            self.publish("stopped", {})

    def stop(self):
        if self.loop and self.stop_event:
            self.loop.call_soon_threadsafe(self.stop_event.set)


class KerMPGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KerMP — Sims multiplayer")
        self.geometry("760x560")
        self.minsize(680, 480)
        self.settings = load_settings()
        self.model = StatusModel(display_name=str(self.settings.get("display_name", "Player")))
        self.controller = RuntimeController(self._publish)
        self._build_ui()
        self.after(150, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        ttk.Label(root, text="KerMP", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(root, text="Host a shared Sims session or join one on your LAN.").pack(anchor="w", pady=(0, 12))
        form = ttk.LabelFrame(root, text="Session", padding=12)
        form.pack(fill="x")
        self.mode = tk.StringVar(value="host")
        for text, value in (("Host game", "host"), ("Join game", "client")):
            ttk.Radiobutton(form, text=text, variable=self.mode, value=value, command=self._mode_changed).pack(side="left", padx=(0, 16))
        self.name = tk.StringVar(value=self.settings.get("display_name", "Player"))
        self.host_ip = tk.StringVar(value=self.settings.get("host_ip", "127.0.0.1"))
        self.lan_port = tk.StringVar(value=str(self.settings.get("lan_port", 17653)))
        self.bridge_port = tk.StringVar(value=str(self.settings.get("bridge_port", 17654)))
        grid = ttk.Frame(form); grid.pack(fill="x", pady=(12, 0))
        self._field(grid, "Display name", self.name, 0, 0)
        self._field(grid, "Host IP", self.host_ip, 0, 2)
        self._field(grid, "LAN port", self.lan_port, 1, 0)
        self._field(grid, "Bridge port", self.bridge_port, 1, 2)
        buttons = ttk.Frame(root); buttons.pack(fill="x", pady=12)
        self.action = ttk.Button(buttons, text="HOST GAME", command=self._start); self.action.pack(side="left")
        ttk.Button(buttons, text="Stop / Disconnect", command=self.controller.stop).pack(side="left", padx=8)
        ttk.Button(buttons, text="Copy address", command=self._copy_address).pack(side="left")
        ttk.Button(buttons, text="Build / Install Sims Mod", command=self._build_mod).pack(side="right")
        status = ttk.LabelFrame(root, text="Live status", padding=12); status.pack(fill="x")
        self.status_text = tk.StringVar(); ttk.Label(status, textvariable=self.status_text, justify="left").pack(anchor="w")
        ttk.Label(root, text="Sims Mod: Waiting    Sidecar: Waiting    LAN: Disconnected", foreground="#666").pack(anchor="w", pady=(10, 0))
        log_frame = ttk.LabelFrame(root, text="Log", padding=8); log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log = tk.Text(log_frame, height=8, state="disabled", wrap="word"); self.log.pack(fill="both", expand=True)
        ttk.Button(log_frame, text="Clear", command=lambda: self.log.delete("1.0", "end")).pack(anchor="e", pady=(5, 0))
        self._mode_changed()

    def _field(self, parent, label, variable, row, col):
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=(0, 7), pady=3)
        ttk.Entry(parent, textvariable=variable, width=20).grid(row=row, column=col + 1, sticky="ew", padx=(0, 18), pady=3)
        parent.columnconfigure(col + 1, weight=1)

    def _mode_changed(self):
        self.action.configure(text="HOST GAME" if self.mode.get() == "host" else "JOIN GAME")

    def _start(self):
        try:
            lan_port, bridge_port = int(self.lan_port.get()), int(self.bridge_port.get())
            if not 1 <= lan_port <= 65535 or not 1 <= bridge_port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid ports", "Ports must be between 1 and 65535."); return
        self.settings.update(display_name=self.name.get().strip() or "Player", host_ip=self.host_ip.get().strip(), lan_port=lan_port, bridge_port=bridge_port)
        save_settings(self.settings)
        mode = self.mode.get(); self.model = StatusModel(mode=mode, display_name=self.name.get())
        self.controller.start(mode, self.name.get(), self.host_ip.get(), lan_port, bridge_port)
        self._write_log("Starting %s..." % ("host" if mode == "host" else "client"))

    def _publish(self, kind, data): self.controller.events.put((kind, data))

    def _poll(self):
        try:
            while True:
                kind, data = self.controller.events.get_nowait()
                if kind == "started":
                    self.model.running = True; self.model.lan_state = data.get("mode", "Connected"); self.model.player_id = data.get("player_id", "")
                    self.model.host_address = data.get("address", ""); self.model.session_id = data.get("session_id", ""); self.model.connected_players = data.get("players", "")
                    self._write_log("%s — player %s" % (data.get("mode"), self.model.player_id))
                elif kind == "error": self.model.last_error = data; self._write_log("ERROR: %s" % data)
                elif kind == "stopped": self.model.running = False; self.model.lan_state = "Disconnected"; self._write_log("Runtime stopped")
        except queue.Empty: pass
        self.status_text.set("Mode: %s\nLAN: %s    Bridge: %s\nPlayer: %s\nSession: %s\nPlayers: %s\nAddress: %s\nCompatibility: %s    Save: %s    Ready: %s\nClock: %s    Native Build/Buy: %s\nBuild lock: %s\nLast error: %s" % (self.model.mode or "idle", self.model.lan_state, self.model.bridge_state, self.model.player_id or "—", self.model.session_id or "—", self.model.connected_players or "—", self.model.host_address or "—", self.model.compatibility, self.model.save_progress, self.model.readiness, self.model.clock, self.model.native, self.model.build_owner, self.model.last_error or "none"))
        self.after(150, self._poll)

    def _write_log(self, text):
        self.log.configure(state="normal"); self.log.insert("end", text + "\n"); self.log.see("end"); self.log.configure(state="disabled")
    def _copy_address(self):
        address = self.model.host_address or "%s:%s" % (detected_lan_ip(), self.lan_port.get())
        self.clipboard_clear(); self.clipboard_append(address); self._write_log("Copied %s" % address)
    def _build_mod(self):
        import subprocess, sys
        subprocess.Popen([sys.executable, "-m", "sims_mod_src.build"], cwd=str(Path(__file__).parents[1]))
        self._write_log("Started Sims mod build; see console/output for Python 3.7 result.")
    def _close(self):
        self.controller.stop(); self.after(250, self.destroy)


def main():
    KerMPGui().mainloop()


if __name__ == "__main__": main()
