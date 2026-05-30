from __future__ import annotations

from dataclasses import dataclass, asdict
from io import BytesIO
import json
import os
from pathlib import Path
import threading

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError:
    tk = None
    filedialog = None
    messagebox = None
    ttk = None

try:
    from PIL import Image
except ModuleNotFoundError:
    Image = None

try:
    import win32clipboard
except ModuleNotFoundError:
    win32clipboard = None

try:
    from pynput.keyboard import Controller, Key, KeyCode, Listener
except Exception:
    Controller = None
    Key = None
    KeyCode = None
    Listener = None


APP_DIR = Path(__file__).resolve().parent
DEFAULT_MACRO_DIR = APP_DIR / "macros"
APP_NAME = "MaduinosMacroKey"


def default_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / APP_NAME


CONFIG_PATH = default_config_dir() / "bindings.json"


SPECIAL_KEYS = {
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "cmd": "cmd",
    "command": "cmd",
    "control": "ctrl",
    "ctrl": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "delete": "delete",
    "down": "down",
    "enter": "enter",
    "esc": "esc",
    "escape": "esc",
    "left": "left",
    "menu": "alt",
    "return": "enter",
    "right": "right",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "space": "space",
    "tab": "tab",
    "up": "up",
    "win": "cmd",
    "windows": "cmd",
}


def key_objects_by_token() -> dict:
    if Key is None:
        return {}

    def keys(*names: str) -> set:
        return {getattr(Key, name) for name in names if hasattr(Key, name)}

    return {
        "alt": keys("alt", "alt_l", "alt_r"),
        "cmd": keys("cmd", "cmd_l", "cmd_r"),
        "ctrl": keys("ctrl", "ctrl_l", "ctrl_r"),
        "delete": keys("delete"),
        "down": keys("down"),
        "enter": keys("enter"),
        "esc": keys("esc"),
        "left": keys("left"),
        "right": keys("right"),
        "shift": keys("shift", "shift_l", "shift_r"),
        "space": keys("space"),
        "tab": keys("tab"),
        "up": keys("up"),
    }


def key_object_to_token() -> dict:
    result = {}
    for token, keys in key_objects_by_token().items():
        for key in keys:
            result[key] = token
    return result


def normalize_token(token: str) -> str:
    value = token.strip().lower()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    value = value.replace(" ", "space")
    return SPECIAL_KEYS.get(value, value)


def parse_hotkey(hotkey: str) -> frozenset[str]:
    tokens = [normalize_token(part) for part in hotkey.split("+")]
    tokens = [token for token in tokens if token]
    if not tokens:
        raise ValueError("hotkey is empty")
    return frozenset(tokens)


def key_to_token(key) -> str | None:
    mapped = key_object_to_token().get(key)
    if mapped:
        return mapped
    if KeyCode is not None and isinstance(key, KeyCode) and key.char:
        return normalize_token(key.char)
    return None


def display_path(path: str) -> str:
    if not path:
        return ""
    resolved = resolve_path(path)
    try:
        return str(resolved.relative_to(APP_DIR))
    except ValueError:
        return str(resolved)


def resolve_path(path: str) -> Path:
    value = Path(path).expanduser()
    if value.is_absolute():
        return value
    return APP_DIR / value


@dataclass
class MacroBinding:
    name: str
    hotkey: str
    image: str
    enabled: bool = True
    paste: bool = True
    press_enter: bool = True

    def tokens(self) -> frozenset[str]:
        return parse_hotkey(self.hotkey)


def default_bindings() -> list[MacroBinding]:
    return [
        MacroBinding(
            name=f"Macro {index}",
            hotkey=f"tab+{index}",
            image=f"macros/macro_{index}.png",
        )
        for index in range(1, 9)
    ]


def load_bindings() -> list[MacroBinding]:
    if not CONFIG_PATH.exists():
        return default_bindings()
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        raw_items = json.load(file)
    bindings = []
    for item in raw_items:
        bindings.append(
            MacroBinding(
                name=str(item.get("name", "Macro")),
                hotkey=str(item.get("hotkey", "")),
                image=str(item.get("image", "")),
                enabled=bool(item.get("enabled", True)),
                paste=bool(item.get("paste", True)),
                press_enter=bool(item.get("press_enter", True)),
            )
        )
    return bindings or default_bindings()


def save_bindings(bindings: list[MacroBinding]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump([asdict(binding) for binding in bindings], file, indent=2)


class MacroRunner:
    def __init__(self, status_callback):
        self.status_callback = status_callback
        self.controller = None
        self.listener = None
        self.bindings: list[MacroBinding] = []
        self.active_tokens: set[str] = set()
        self.fired_indexes: set[int] = set()
        self.lock = threading.RLock()

    def start(self, bindings: list[MacroBinding]) -> None:
        self.require_runtime()
        with self.lock:
            self.bindings = bindings
            self.active_tokens.clear()
            self.fired_indexes.clear()
            if self.listener is not None:
                return
            self.listener = Listener(on_press=self.on_press, on_release=self.on_release)
            self.listener.start()
        self.status_callback("Listener started")

    def stop(self) -> None:
        with self.lock:
            listener = self.listener
            self.listener = None
            self.active_tokens.clear()
            self.fired_indexes.clear()
        if listener is not None:
            listener.stop()
        self.status_callback("Listener stopped")

    def require_runtime(self) -> None:
        missing = []
        if Listener is None or Controller is None:
            missing.append("pynput")
        if Image is None:
            missing.append("Pillow")
        if win32clipboard is None:
            missing.append("pywin32")
        if missing:
            packages = ", ".join(missing)
            raise RuntimeError(f"Install required package(s): {packages}")
        if self.controller is None:
            self.controller = Controller()

    def on_press(self, key) -> None:
        token = key_to_token(key)
        if token is None:
            return
        with self.lock:
            self.active_tokens.add(token)
            active = frozenset(self.active_tokens)
            for index, binding in enumerate(self.bindings):
                if not binding.enabled or index in self.fired_indexes:
                    continue
                try:
                    trigger = binding.tokens()
                except ValueError:
                    continue
                if trigger.issubset(active):
                    self.fired_indexes.add(index)
                    threading.Thread(
                        target=self.run_binding,
                        args=(binding,),
                        daemon=True,
                    ).start()

    def on_release(self, key) -> None:
        token = key_to_token(key)
        if token is None:
            return
        with self.lock:
            self.active_tokens.discard(token)
            active = frozenset(self.active_tokens)
            for index, binding in enumerate(self.bindings):
                try:
                    trigger = binding.tokens()
                except ValueError:
                    trigger = frozenset()
                if not trigger.issubset(active):
                    self.fired_indexes.discard(index)

    def run_binding(self, binding: MacroBinding) -> None:
        try:
            self.copy_image_to_clipboard(resolve_path(binding.image))
            if binding.paste:
                self.paste_from_clipboard(binding.press_enter)
            self.status_callback(f"Ran {binding.name}")
        except Exception as exc:  # noqa: BLE001 - report runtime failures in UI.
            self.status_callback(f"{binding.name} failed: {exc}")

    def copy_image_to_clipboard(self, image_path: Path) -> None:
        if not image_path.exists():
            raise FileNotFoundError(image_path)
        image = Image.open(image_path)
        output = BytesIO()
        image.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:]
        output.close()

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()

    def paste_from_clipboard(self, press_enter: bool) -> None:
        if self.controller is None:
            self.controller = Controller()
        self.controller.press(Key.ctrl)
        self.controller.press("v")
        self.controller.release("v")
        self.controller.release(Key.ctrl)
        if press_enter:
            self.controller.press(Key.enter)
            self.controller.release(Key.enter)


class BindingRow:
    def __init__(self, parent, index: int, binding: MacroBinding, browse_callback, run_callback):
        self.enabled = tk.BooleanVar(value=binding.enabled)
        self.name = tk.StringVar(value=binding.name)
        self.hotkey = tk.StringVar(value=binding.hotkey)
        self.image = tk.StringVar(value=display_path(binding.image))
        self.paste = tk.BooleanVar(value=binding.paste)
        self.press_enter = tk.BooleanVar(value=binding.press_enter)

        ttk.Checkbutton(parent, variable=self.enabled).grid(row=index, column=0, padx=4, pady=3)
        ttk.Entry(parent, textvariable=self.name, width=14).grid(row=index, column=1, padx=4, pady=3, sticky="ew")
        ttk.Entry(parent, textvariable=self.hotkey, width=14).grid(row=index, column=2, padx=4, pady=3, sticky="ew")
        ttk.Entry(parent, textvariable=self.image, width=32).grid(row=index, column=3, padx=4, pady=3, sticky="ew")
        ttk.Button(parent, text="Browse", command=lambda: browse_callback(self)).grid(row=index, column=4, padx=4, pady=3)
        ttk.Checkbutton(parent, variable=self.paste).grid(row=index, column=5, padx=4, pady=3)
        ttk.Checkbutton(parent, variable=self.press_enter).grid(row=index, column=6, padx=4, pady=3)
        ttk.Button(parent, text="Run", command=lambda: run_callback(self)).grid(row=index, column=7, padx=4, pady=3)

    def to_binding(self) -> MacroBinding:
        return MacroBinding(
            name=self.name.get().strip() or "Macro",
            hotkey=self.hotkey.get().strip(),
            image=self.image.get().strip(),
            enabled=self.enabled.get(),
            paste=self.paste.get(),
            press_enter=self.press_enter.get(),
        )


class MacroKeyApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Maduinos MacroKey")
        self.root.minsize(920, 360)
        self.status = tk.StringVar(value=f"Config: {CONFIG_PATH}")
        self.runner = MacroRunner(self.set_status_threadsafe)
        self.rows: list[BindingRow] = []
        self.build_ui(load_bindings())

    def build_ui(self, bindings: list[MacroBinding]) -> None:
        controls = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        controls.pack(fill="x")

        ttk.Button(controls, text="Start", command=self.start_listener).pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="Stop", command=self.stop_listener).pack(side="left", padx=6)
        ttk.Button(controls, text="Save", command=self.save).pack(side="left", padx=6)
        ttk.Button(controls, text="Add Row", command=self.add_row).pack(side="left", padx=6)
        ttk.Button(controls, text="Reset Defaults", command=self.reset_defaults).pack(side="left", padx=6)

        table = ttk.Frame(self.root, padding=(10, 4, 10, 4))
        table.pack(fill="both", expand=True)
        self.table = table

        headers = ["On", "Name", "Hotkey", "Image", "", "Paste", "Enter", ""]
        for column, title in enumerate(headers):
            ttk.Label(table, text=title).grid(row=0, column=column, padx=4, pady=(0, 4), sticky="w")
        table.columnconfigure(1, weight=1)
        table.columnconfigure(2, weight=1)
        table.columnconfigure(3, weight=3)

        for binding in bindings:
            self.add_binding_row(binding)

        status_bar = ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=(8, 4))
        status_bar.pack(fill="x", side="bottom")

    def add_binding_row(self, binding: MacroBinding) -> None:
        row = BindingRow(
            self.table,
            len(self.rows) + 1,
            binding,
            browse_callback=self.browse_image,
            run_callback=self.run_once,
        )
        self.rows.append(row)

    def add_row(self) -> None:
        self.add_binding_row(MacroBinding("Macro", "", "", enabled=False))

    def reset_defaults(self) -> None:
        self.runner.stop()
        for widget in self.table.grid_slaves():
            if int(widget.grid_info()["row"]) > 0:
                widget.destroy()
        self.rows.clear()
        for binding in default_bindings():
            self.add_binding_row(binding)
        self.set_status("Defaults loaded")

    def browse_image(self, row: BindingRow) -> None:
        selected = filedialog.askopenfilename(
            title="Select macro image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
            initialdir=DEFAULT_MACRO_DIR,
        )
        if selected:
            row.image.set(selected)

    def current_bindings(self) -> list[MacroBinding]:
        bindings = [row.to_binding() for row in self.rows]
        used_hotkeys = {}
        for binding in bindings:
            if binding.enabled:
                tokens = binding.tokens()
                if tokens in used_hotkeys:
                    other = used_hotkeys[tokens]
                    raise ValueError(f"Duplicate hotkey: {binding.hotkey} is used by {other} and {binding.name}")
                used_hotkeys[tokens] = binding.name
            if binding.image:
                resolve_path(binding.image)
        return bindings

    def save(self) -> None:
        try:
            bindings = self.current_bindings()
            save_bindings(bindings)
            self.set_status(f"Saved {CONFIG_PATH}")
        except Exception as exc:  # noqa: BLE001 - show validation failures to the user.
            messagebox.showerror("Save failed", str(exc))

    def start_listener(self) -> None:
        try:
            bindings = self.current_bindings()
            self.runner.start(bindings)
        except Exception as exc:  # noqa: BLE001 - show runtime setup failures to the user.
            messagebox.showerror("Start failed", str(exc))

    def stop_listener(self) -> None:
        self.runner.stop()

    def run_once(self, row: BindingRow) -> None:
        try:
            binding = row.to_binding()
            self.runner.require_runtime()
            threading.Thread(target=self.runner.run_binding, args=(binding,), daemon=True).start()
        except Exception as exc:  # noqa: BLE001 - show runtime setup failures to the user.
            messagebox.showerror("Run failed", str(exc))

    def set_status(self, message: str) -> None:
        self.status.set(message)

    def set_status_threadsafe(self, message: str) -> None:
        self.root.after(0, self.set_status, message)

    def on_close(self) -> None:
        self.runner.stop()
        self.root.destroy()


def main() -> None:
    if tk is None:
        raise RuntimeError("tkinter is required to run the MacroKey GUI app.")
    root = tk.Tk()
    app = MacroKeyApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
