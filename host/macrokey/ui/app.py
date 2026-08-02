"""Tkinter editor.

This is the only module allowed to import tkinter. Everything it does is a call
into :class:`~macrokey.app.MacroKeyApp`, so replacing this view with PySide6 or
running with no view at all costs nothing elsewhere.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from ..app import MacroKeyApp
from ..config import GESTURES, KEY_COUNT, LAYER_COUNT, Action, keycodes
from ..device import DeviceError, candidates

# Action kinds offered in the slot editor, with the field each one needs.
EDITABLE_KINDS: dict[str, tuple[str, str]] = {
    "none": ("", "Nothing"),
    "key": ("hotkey", "Shortcut, e.g. ctrl+shift+p"),
    "consumer": ("usage", "Media key"),
    "mouse_button": ("button", "Mouse button"),
    "layer_momentary": ("layer", "Layer while held"),
    "layer_toggle": ("layer", "Layer to toggle"),
    "host": ("token", "Host action token"),
}


def _value_of(action: Action) -> str:
    """The single editable field for this action kind, as text."""
    field, _ = EDITABLE_KINDS.get(action.kind, ("", ""))
    return str(getattr(action, field)) if field else ""


class SlotDialog(tk.Toplevel):
    """Edits one (layer, key, gesture) slot."""

    def __init__(self, parent: tk.Misc, app: MacroKeyApp, layer: int, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.layer, self.key, self.gesture = layer, key, gesture
        self.result: Action | None = None

        self.title(f"Layer {layer} - Key {key + 1} - {gesture}")
        self.transient(parent)
        self.resizable(False, False)

        current = app.profile.action(layer, key, gesture)
        self.kind = tk.StringVar(value=current.kind if current.kind in EDITABLE_KINDS else "none")
        self.value = tk.StringVar(value=_value_of(current))

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="Action").grid(row=0, column=0, sticky="w", pady=4)
        picker = ttk.Combobox(
            body, textvariable=self.kind, values=list(EDITABLE_KINDS), state="readonly", width=22
        )
        picker.grid(row=0, column=1, sticky="ew", pady=4)
        picker.bind("<<ComboboxSelected>>", lambda _event: self._refresh_hint())

        ttk.Label(body, text="Value").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.value, width=24).grid(row=1, column=1, sticky="ew", pady=4)

        self.hint = ttk.Label(body, text="", foreground="#666")
        self.hint.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Record...", command=self._record).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        ttk.Button(buttons, text="OK", command=self._accept).pack(side="left", padx=4)

        self._refresh_hint()
        self.grab_set()

    def _refresh_hint(self) -> None:
        _, description = EDITABLE_KINDS[self.kind.get()]
        extra = ""
        if self.kind.get() == "consumer":
            extra = "  (" + ", ".join(sorted(keycodes.CONSUMER_USAGES)) + ")"
        self.hint.configure(text=description + extra)

    def _accept(self) -> None:
        field, _ = EDITABLE_KINDS[self.kind.get()]
        raw = self.value.get().strip()
        try:
            if not field:
                self.result = Action()
            elif field in ("layer", "token"):
                self.result = Action(kind=self.kind.get(), **{field: int(raw or 0)})
            else:
                self.result = Action(kind=self.kind.get(), **{field: raw})
            self.result.encode()  # fails now rather than at push time
        except Exception as exc:  # noqa: BLE001 - report any validation problem
            messagebox.showerror("Invalid action", str(exc), parent=self)
            return
        self.destroy()

    def _record(self) -> None:
        RecordDialog(self, self.app, self.layer, self.key, self.gesture)
        self.destroy()


class RecordDialog(tk.Toplevel):
    """Captures input, shows what it understood, then binds it on confirmation."""

    def __init__(self, parent: tk.Misc, app: MacroKeyApp, layer: int, key: int, gesture: str):
        super().__init__(parent)
        self.app = app
        self.layer, self.key, self.gesture = layer, key, gesture
        self.steps: list[dict] = []

        self.title("Record a macro")
        self.transient(parent)
        self.geometry("420x320")

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text="Press Start, do the thing, then press Esc to stop.\n"
            "The LEDs turn red while recording.",
            justify="left",
        ).pack(anchor="w")

        self.listbox = tk.Listbox(body, height=10)
        self.listbox.pack(fill="both", expand=True, pady=8)

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="Start", command=self._start)
        self.start_button.pack(side="left")
        self.save_button = ttk.Button(buttons, text="Bind", command=self._save, state="disabled")
        self.save_button.pack(side="right")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right", padx=6)

        self.grab_set()

    def _start(self) -> None:
        try:
            self.app.start_recording()
        except Exception as exc:  # noqa: BLE001 - pynput failures are environmental
            messagebox.showerror("Cannot record", str(exc), parent=self)
            return
        self.start_button.configure(state="disabled")
        self.listbox.delete(0, "end")
        self.listbox.insert("end", "recording...")
        threading.Thread(target=self._wait_for_stop, daemon=True).start()

    def _wait_for_stop(self) -> None:
        while self.app.recorder.recording:
            threading.Event().wait(0.1)
        steps = self.app.stop_recording()
        self.after(0, self._show, steps)

    def _show(self, steps: list[dict]) -> None:
        self.steps = steps
        self.listbox.delete(0, "end")
        for line in self.app.recorder.summary(steps):
            self.listbox.insert("end", line)
        if not steps:
            self.listbox.insert("end", "(nothing captured)")
        self.start_button.configure(state="normal")
        self.save_button.configure(state="normal" if steps else "disabled")

    def _save(self) -> None:
        where = self.app.assign_recording(self.steps, self.layer, self.key, self.gesture)
        self.app.save()
        messagebox.showinfo("Bound", f"Stored as {where}", parent=self)
        self.destroy()


class MainWindow:
    def __init__(self, root: tk.Tk, port: str = "") -> None:
        self.root = root
        self.app = MacroKeyApp(status=self._status_threadsafe)
        self.port = tk.StringVar(value=port or self.app.settings.port)
        self.status_text = tk.StringVar(value="Not connected")
        self.buttons: dict[tuple[int, int, str], ttk.Button] = {}

        root.title("Maduinos macroKey")
        root.minsize(760, 520)

        self._build_toolbar()
        self._build_layers()
        self._build_status()
        self._refresh_all()

    # ------------------------------------------------------------------ build --

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        bar.pack(fill="x")

        ttk.Label(bar, text="Port").pack(side="left")
        ports = [item.device for item in candidates()]
        self.port_box = ttk.Combobox(bar, textvariable=self.port, values=ports, width=18)
        self.port_box.pack(side="left", padx=(4, 10))

        ttk.Button(bar, text="Connect", command=self._connect).pack(side="left", padx=3)
        ttk.Button(bar, text="Disconnect", command=self._disconnect).pack(side="left", padx=3)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Save", command=self._save).pack(side="left", padx=3)
        ttk.Button(bar, text="Write to device", command=self._push).pack(side="left", padx=3)
        ttk.Button(bar, text="Read from device", command=self._pull).pack(side="left", padx=3)

        self.brightness = tk.IntVar(value=self.app.profile.brightness)
        ttk.Label(bar, text="Brightness").pack(side="left", padx=(14, 4))
        ttk.Scale(
            bar,
            from_=0,
            to=255,
            variable=self.brightness,
            command=lambda _value: self._brightness_changed(),
            length=110,
        ).pack(side="left")

    def _build_layers(self) -> None:
        self.notebook = ttk.Notebook(self.root, padding=(10, 4))
        self.notebook.pack(fill="both", expand=True)

        for layer in range(LAYER_COUNT):
            frame = ttk.Frame(self.notebook, padding=8)
            name = self.app.profile.layers[layer].name or f"Layer {layer}"
            self.notebook.add(frame, text=name)

            for column, title in enumerate(("Key", *(gesture.title() for gesture in GESTURES))):
                ttk.Label(frame, text=title, font=("TkDefaultFont", 9, "bold")).grid(
                    row=0, column=column, padx=4, pady=(0, 6), sticky="w"
                )
                if column:
                    frame.columnconfigure(column, weight=1)

            for key in range(KEY_COUNT):
                ttk.Label(frame, text=f"{key + 1}").grid(row=key + 1, column=0, padx=4, pady=2)
                for index, gesture in enumerate(GESTURES):
                    button = ttk.Button(
                        frame,
                        text="-",
                        width=26,
                        command=lambda layer=layer, key=key, gesture=gesture: self._edit(
                            layer, key, gesture
                        ),
                    )
                    button.grid(row=key + 1, column=index + 1, padx=4, pady=2, sticky="ew")
                    self.buttons[(layer, key, gesture)] = button

    def _build_status(self) -> None:
        ttk.Label(
            self.root,
            textvariable=self.status_text,
            relief="sunken",
            anchor="w",
            padding=(8, 4),
        ).pack(fill="x", side="bottom")

    # --------------------------------------------------------------- actions --

    def _refresh_all(self) -> None:
        for (layer, key, gesture), button in self.buttons.items():
            action = self.app.profile.action(layer, key, gesture)
            label = action.describe()
            if action.kind == "host":
                spec = self.app.profile.host_actions.get(action.token)
                if spec is not None:
                    label = f"host: {spec.describe()}"
            button.configure(text=label)

    def _edit(self, layer: int, key: int, gesture: str) -> None:
        dialog = SlotDialog(self.root, self.app, layer, key, gesture)
        self.root.wait_window(dialog)
        if dialog.result is not None:
            self.app.profile.set_action(layer, key, gesture, dialog.result)
        self._refresh_all()

    def _brightness_changed(self) -> None:
        self.app.profile.brightness = int(self.brightness.get())
        if self.app.device.connected:
            try:
                self.app.device.set_brightness(self.app.profile.brightness)
            except DeviceError as exc:
                self._status(str(exc))

    def _connect(self) -> None:
        def worker() -> None:
            try:
                self.app.connect(self.port.get())
                self.app.settings.port = self.app.device.port
                self.app.settings.save()
            except DeviceError as exc:
                self.root.after(0, messagebox.showerror, "Connect failed", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _disconnect(self) -> None:
        self.app.disconnect()
        self._status("Disconnected")

    def _save(self) -> None:
        self.app.save()

    def _push(self) -> None:
        def worker() -> None:
            try:
                self.app.save()
                self.app.push_profile()
            except (DeviceError, ValueError) as exc:
                self.root.after(0, messagebox.showerror, "Write failed", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _pull(self) -> None:
        def worker() -> None:
            try:
                profile = self.app.pull_profile()
            except (DeviceError, ValueError) as exc:
                self.root.after(0, messagebox.showerror, "Read failed", str(exc))
                return
            self.root.after(0, self._adopt, profile)

        threading.Thread(target=worker, daemon=True).start()

    def _adopt(self, profile) -> None:
        # Host actions live only on the host, so a device read must not wipe
        # them: the device stores tokens, not the work behind them.
        profile.host_actions = self.app.profile.host_actions
        if not messagebox.askyesno(
            "Replace local profile?",
            "The device profile will replace the one in this window. Continue?",
        ):
            return
        self.app.profile = profile
        self.app.actions.set_profile(profile)
        self.brightness.set(profile.brightness)
        self._refresh_all()

    # ---------------------------------------------------------------- status --

    def _status(self, message: str) -> None:
        self.status_text.set(message)

    def _status_threadsafe(self, message: str) -> None:
        self.root.after(0, self._status, message)

    def close(self) -> None:
        self.app.close()
        self.root.destroy()


def run_gui(port: str = "") -> int:
    root = tk.Tk()
    window = MainWindow(root, port=port)
    root.protocol("WM_DELETE_WINDOW", window.close)
    root.mainloop()
    return 0
