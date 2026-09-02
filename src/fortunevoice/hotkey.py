"""Global hold-to-talk hotkey.

macOS used the KeyboardShortcuts package, which hands you key-down and key-up
for a registered chord. Windows has no such API:

* `RegisterHotKey` delivers WM_HOTKEY on press only — no key-up, so
  hold-to-talk is impossible with it.
* A `WH_KEYBOARD_LL` hook sees both edges and can *swallow* the chord so the
  trigger key never reaches the focused app. That is what we use.

Two things a low-level hook gets wrong if you're careless, both handled here:

1. **The callback must return fast.** Windows silently unhooks a low-level
   hook whose callback exceeds `LowLevelHooksTimeout` (300 ms by default), and
   the app then looks alive while the hotkey does nothing. The callback only
   timestamps and enqueues; if it ever does run long (a GIL stall behind a
   heavy decode), we notice and reinstall the hook.
2. **Our own synthesized keystrokes come back through the hook.** Typing a
   dictation containing the trigger key would retrigger dictation. Events
   flagged `LLKHF_INJECTED` are passed through untouched.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

from .log import get as get_logger
from . import winapi
from .winapi import user32

logger = get_logger("hotkey")

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_TIMER = 0x0113
LLKHF_INJECTED = 0x00000010
HC_ACTION = 0

VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C

# Virtual-key codes for the names accepted in FVHotkey.
KEY_NAMES: dict[str, int] = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "capslock": 0x14,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "rctrl": VK_RCONTROL,
    "lctrl": VK_LCONTROL,
    "ralt": VK_RMENU,
    "lalt": VK_LMENU,
    "rshift": VK_RSHIFT,
    "lshift": VK_LSHIFT,
    "`": 0xC0,
    "-": 0xBD,
    "=": 0xBB,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "\\": 0xDC,
    "[": 0xDB,
    "]": 0xDD,
}
for _i in range(1, 25):
    KEY_NAMES[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEY_NAMES[_c] = ord(_c.upper())
for _d in "0123456789":
    KEY_NAMES[_d] = ord(_d)

# Modifier name → the pair of virtual keys that satisfy it.
MODIFIER_KEYS: dict[str, tuple[int, ...]] = {
    "ctrl": (VK_LCONTROL, VK_RCONTROL),
    "control": (VK_LCONTROL, VK_RCONTROL),
    "alt": (VK_LMENU, VK_RMENU),
    "shift": (VK_LSHIFT, VK_RSHIFT),
    "win": (VK_LWIN, VK_RWIN),
}

# Virtual keys that are modifiers. A hotkey may still be built on one — holding
# Ctrl to talk is a fine push-to-talk — but the listener must never swallow it.
_MODIFIER_VKS = {
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU,
    VK_LWIN, VK_RWIN, 0x10, 0x11, 0x12,  # the combined SHIFT/CONTROL/MENU
    0x14, 0x90, 0x91,                    # caps lock, num lock, scroll lock
}


class HotkeySpec:
    def __init__(self, modifiers: list[str], key: int, label: str,
                 keys: tuple[int, ...] | None = None,
                 modifier_trigger: bool = False) -> None:
        self.modifiers = modifiers
        self.key = key
        # A trigger can have more than one virtual key: a low-level hook
        # reports Alt as VK_LMENU/VK_RMENU, never the combined VK_MENU, so
        # "ctrl+alt" has to match either side.
        self.keys = keys or (key,)
        self.label = label
        # True when the trigger is itself a modifier ("ctrl+alt", "ctrl").
        # Those are never swallowed — see HotkeyListener._handle.
        self.modifier_trigger = modifier_trigger

    def modifiers_held(self, arriving: int | None = None) -> bool:
        """Are the chord's modifiers down, apart from the one just pressed?

        `arriving` is the virtual key the hook is reporting right now. Its own
        group has to be skipped: a low-level hook runs *before* Windows commits
        the keystroke, so GetAsyncKeyState still reports that key as up. That
        never mattered while triggers were ordinary keys — the trigger is known
        to be down because the hook said so — but a modifier trigger is both at
        once, and checking it here made Ctrl+Alt never fire.
        """
        for name in self.modifiers:
            keys = MODIFIER_KEYS[name]
            if arriving is not None and arriving in keys:
                continue
            if not any(_key_is_down(vk) for vk in keys):
                return False
        return True


def parse(spec: str) -> HotkeySpec:
    """Parse "ctrl+alt+space" into modifiers plus a trigger key.

    Raises ValueError on an unknown name so a typo in config.json surfaces at
    startup with a readable message instead of a hotkey that silently never
    fires.
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    *modifier_names, key_name = parts
    for name in modifier_names:
        if name not in MODIFIER_KEYS:
            raise ValueError(f"unknown modifier {name!r} in hotkey {spec!r}")

    label = "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in parts)

    # "ctrl+alt", or a lone "ctrl": the last part is itself a modifier, and
    # holding it is the trigger. Common for push-to-talk, because a modifier
    # is comfortable to hold down while speaking and types nothing on its own.
    if key_name in MODIFIER_KEYS:
        # Every key of the chord is a trigger, and every one of them is also
        # required to be held. That way Ctrl+Alt fires whichever finger lands
        # second, and lifting either one ends the dictation.
        names = [*modifier_names, key_name]
        keys = tuple(vk for name in names for vk in MODIFIER_KEYS[name])
        return HotkeySpec(names, MODIFIER_KEYS[key_name][0], label, keys=keys,
                          modifier_trigger=True)

    if key_name not in KEY_NAMES:
        raise ValueError(f"unknown key {key_name!r} in hotkey {spec!r}")
    vk = KEY_NAMES[key_name]
    # Same rule for a side-specific name: "rctrl" is still a Ctrl, and eating
    # it would break Ctrl+C typed with the right hand.
    return HotkeySpec(modifier_names, vk, label,
                      modifier_trigger=vk in _MODIFIER_VKS)


_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.GetMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
)

# vkCode -> the name `parse()` understands. Built by inverting KEY_NAMES, with
# the left/right specific entries losing to the plain ones so a captured Ctrl
# reads as "ctrl" rather than "lctrl".
_VK_TO_NAME: dict[int, str] = {}
for _name, _vk in KEY_NAMES.items():
    if _vk not in _VK_TO_NAME or len(_name) < len(_VK_TO_NAME[_vk]):
        _VK_TO_NAME[_vk] = _name


def _key_is_down(vk: int) -> bool:
    """Is this key physically held right now?

    A named function rather than the raw call, so a test can replace THIS and
    leave the ctypes declaration alone: monkeypatching an attribute on the
    shared `user32` object and restoring it drops the argtypes and restype
    that were declared at import, and the next caller gets a value of the
    wrong width. See test_the_monitor_handle_is_not_truncated.
    """
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class ChordCapture:
    """Grab the next chord the user presses, anywhere.

    A shortcut recorder built on window focus cannot be trusted: the thing
    being recorded is a GLOBAL hotkey, and the keys have to be read the same
    way the hook will read them later — regardless of which window happens to
    hold focus, and regardless of Windows refusing foreground to a background
    thread.

    Swallows what it captures, so the chord being recorded never leaks into
    whatever is underneath.
    """

    # Nothing may leave this hook installed forever. It SWALLOWS keys, so a
    # path that forgets to stop it eats the user's typing system-wide and
    # leaves the app's own hotkey paused — the whole keyboard half-dead with
    # no way to tell why. Closing the Settings window mid-recording did
    # exactly that. Recording a chord takes a second; a minute is a generous
    # ceiling that still guarantees the hook cannot outlive the session.
    MAX_LISTENING_SECONDS = 60.0

    def __init__(self, on_chord: Callable[[str], None],
                 on_cancel: Callable[[], None] | None = None) -> None:
        self._on_chord = on_chord
        self._on_cancel = on_cancel
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._done = False
        # Modifier state is tracked from the hook's own events rather than
        # read back with GetAsyncKeyState: capture SWALLOWS what it sees, so
        # the modifiers never reach the OS and GetAsyncKeyState would report
        # them as up. Swallowing matters — the chord being recorded must not
        # leak into whatever window is underneath.
        self._held: set[int] = set()
        self._proc = _HOOKPROC(self._callback)
        self._stop = threading.Event()
        self._deadline: threading.Timer | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._done = False
        self._held.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="chord-capture",
                                        daemon=True)
        self._thread.start()
        self._deadline = threading.Timer(self.MAX_LISTENING_SECONDS, self._expire)
        self._deadline.daemon = True
        self._deadline.start()

    def _expire(self) -> None:
        """Nobody stopped us. Give the keyboard back and say the recording was
        abandoned, rather than eating keys for the rest of the session."""
        if self._done:
            return
        logger.warning("chord capture timed out after %.0f s — releasing the keyboard",
                       self.MAX_LISTENING_SECONDS)
        self._done = True
        if self._on_cancel:
            try:
                self._on_cancel()
            except Exception:  # noqa: BLE001 - the hook must still come down
                logger.exception("chord capture cancel callback failed")
        self.stop()

    def stop(self) -> None:
        if self._deadline is not None:
            self._deadline.cancel()
            self._deadline = None
        self._stop.set()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    # ── hook thread ──────────────────────────────────────────────────────

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        # Module handle None, like the main listener: a low-level hook is
        # global and does not need one.
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            logger.error("could not install the capture hook")
            return
        message = wintypes.MSG()
        while not self._stop.is_set():
            got = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if got in (0, -1):
                break
        user32.UnhookWindowsHookEx(self._hook)
        self._hook = None
        self._thread_id = 0

    def _callback(self, code, wparam, lparam):
        if code == HC_ACTION and not self._done:
            try:
                if self._handle(wparam, lparam):
                    return 1
            except Exception:  # noqa: BLE001 - never break the input queue
                logger.exception("chord capture failed")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    # Canonical order, matching what `parse()` prints back, so the chip reads
    # "ctrl+alt+space" whatever order the fingers landed in.
    _MODIFIER_ORDER = (
        ("ctrl", (VK_LCONTROL, VK_RCONTROL, 0x11)),
        ("alt", (VK_LMENU, VK_RMENU, 0x12)),
        ("shift", (VK_LSHIFT, VK_RSHIFT, 0x10)),
        ("win", (VK_LWIN, VK_RWIN)),
    )

    def _modifiers_down(self, held: set[int] | None = None) -> list[str]:
        held = self._held if held is None else held
        return [name for name, keys in self._MODIFIER_ORDER
                if any(k in held for k in keys)]

    def _modifier_chord(self, held: set[int]) -> str:
        """The chord for a modifiers-only press, e.g. "ctrl+alt".

        Built from what was held at the moment of release. A single modifier
        is a valid hotkey on its own; the listener will not swallow it, so
        Ctrl keeps working as Ctrl.
        """
        names = self._modifiers_down(held)
        return "+".join(names) if names else ""

    def _handle(self, wparam: int, lparam: int) -> bool:
        event = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        # Injected events are NOT filtered here, unlike in the listener. The
        # listener must ignore them so the app never triggers itself with its
        # own typed dictation; capture runs at a moment the app types nothing,
        # and refusing injected keys would lock out anyone using a keyboard
        # remapper or a remote desktop, where every key arrives injected.
        vk = event.vkCode
        if wparam not in (WM_KEYDOWN, WM_SYSKEYDOWN):
            held_before = set(self._held)
            self._held.discard(vk)
            # Modifiers released without any other key having been pressed:
            # the user meant the modifiers themselves ("hold Ctrl+Alt to
            # talk"). Decided on RELEASE, because until a key goes up there is
            # no way to tell "Ctrl as the chord" from "Ctrl on the way to
            # Ctrl+C".
            if vk in held_before and not self._done and vk in _MODIFIER_VKS:
                # Computed from the set BEFORE this key left it: releasing Alt
                # first out of Ctrl+Alt must still record "ctrl+alt", not
                # whatever happens to remain down.
                chord = self._modifier_chord(held_before)
                if chord:
                    self._done = True
                    self._on_chord(chord)
            # Swallow the matching key-ups of a chord we already took, so the
            # target app never sees half a keystroke.
            return True

        if vk == 0x1B:  # Escape cancels, and is never a shortcut by itself
            self._done = True
            if self._on_cancel:
                self._on_cancel()
            return True
        if vk in _MODIFIER_VKS:
            self._held.add(vk)
            return True  # held, not the trigger — swallow and keep waiting

        name = _VK_TO_NAME.get(vk)
        if name is None:
            return True  # unmapped key: swallow, wait for one we can name

        modifiers = self._modifiers_down()

        self._done = True
        self._on_chord("+".join([*modifiers, name]))
        return True


class HotkeyListener:
    """Runs the hook on its own thread with its own message pump.

    A low-level keyboard hook only delivers to a thread that pumps messages,
    and the app's own thread is busy driving dictation — so the hook gets a
    dedicated one that does nothing else.
    """

    # A callback slower than this means we are at risk of being unhooked by
    # Windows; reinstall before that happens.
    SLOW_CALLBACK_SECONDS = 0.2

    def __init__(
        self,
        spec: HotkeySpec,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_arm: Callable[[], None] | None = None,
        on_disarm: Callable[[], None] | None = None,
    ) -> None:
        self._spec = spec
        self._on_press = on_press
        self._on_release = on_release
        # Fired the moment the chord goes down, before the hold threshold has
        # elapsed, so the microphone can be opened early — the wait then costs
        # the user nothing instead of eating the start of their sentence. The
        # disarm says the hold never completed and whatever was captured
        # should be thrown away.
        self._on_arm = on_arm
        self._on_disarm = on_disarm
        # Told when the hook could not be installed.
        self.on_broken: Callable[[str], None] | None = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._held = False
        self._reinstall = False
        # When the hook last saw ANY key. Compared against what Windows
        # says the system saw, to notice a hook removed under us.
        self._last_seen = time.monotonic()
        # When a liveness probe was sent, 0 when none is outstanding.
        self._probe_at = 0.0
        self._proc = _HOOKPROC(self._callback)  # keep a reference alive
        self._stop = threading.Event()
        # A modifier trigger fires from a timer thread, not from the hook —
        # see _begin_press.
        self._press_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pressed = False

    @property
    def spec(self) -> HotkeySpec:
        return self._spec

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._press_lock:
            self._cancel_timer()
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)

    # ── hook thread ──────────────────────────────────────────────────────

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        if not self._install():
            self._report_broken()
            return
        # A 1 s timer wakes GetMessage so the loop can act on _reinstall; the
        # hook itself has nothing to do with timers.
        user32.SetTimer(None, 0, 1000, None)
        message = wintypes.MSG()
        while not self._stop.is_set():
            result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if result in (0, -1):
                break
            if message.message == WM_TIMER:
                self._resync_held()
                self._check_still_hooked()
            if message.message == WM_TIMER and self._reinstall:
                self._reinstall = False
                logger.warning("reinstalling the keyboard hook after a slow callback")
                self._uninstall()
                if not self._install():
                    self._report_broken()
                    break
        self._uninstall()

    def _resync_held(self) -> None:
        """Recover from a key-up the hook never saw.

        A UAC prompt, Win+L and Ctrl+Alt+Del all switch to a different
        desktop, and the release lands on a desktop this hook is not on. The
        latch then says the key is still down, which has two consequences: the
        next press is read as auto-repeat and swallowed, so the first attempt
        after unlocking does nothing; and a recording that was running when
        the desktop switched never ends, so it runs to the 300 s cap and types
        five minutes of room noise.

        Checked once a second against the physical keyboard, which is the one
        thing here that cannot be out of date.
        """
        if not self._held:
            return
        if any(_key_is_down(vk) for vk in self._spec.keys):
            return
        logger.info("the hotkey release was never delivered — resyncing")
        self._held = False
        self._end_press()

    def _report_broken(self) -> None:
        """Say the hotkey is dead. Windows refusing the hook, or refusing to
        reinstall it after a slow callback, left the app looking alive while
        the key did nothing — with the only trace in a log file."""
        if self.on_broken is None:
            return
        try:
            self.on_broken(self._spec.label)
        except Exception:  # noqa: BLE001 - reporting must not raise here
            logger.exception("could not report the broken hotkey")

    def _install(self) -> bool:
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            logger.error(
                "SetWindowsHookEx failed (error %d) — the hotkey will not work",
                ctypes.get_last_error(),
            )
            return False
        logger.info("hotkey listening for %s", self._spec.label)
        return True

    def _uninstall(self) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    # How far the hook may lag the system before it is presumed deaf. Long
    # enough that a stall cannot trip it, short enough that the user is not
    # left pressing a dead shortcut for a minute.
    DEAF_AFTER_SECONDS = 20.0

    def _check_still_hooked(self) -> None:
        """Ask the hook whether it is still listening, and reinstall if not.

        Windows removes a low-level hook whose callback runs too long, and it
        says nothing: no error, no final callback, and no API to ask whether a
        hook is still installed. Twice on this machine the app was running,
        the log said "hotkey listening", and the shortcut did nothing until it
        was restarted by hand.

        The first attempt at this compared `GetLastInputInfo` against what the
        hook had seen — and GetLastInputInfo counts the MOUSE. Moving the
        pointer without typing looked exactly like a dead hook, so it was
        reinstalled every twenty seconds for as long as the machine was in
        use. Guessing replaced by asking: press a key nobody uses and see
        whether our own hook reports it.
        """
        now = time.monotonic()
        if now - self._last_seen < self.DEAF_AFTER_SECONDS:
            self._probe_at = 0.0
            return

        if self._probe_at:
            if self._last_seen >= self._probe_at:
                self._probe_at = 0.0      # it answered; the hook is alive
                return
            logger.warning("the hook did not see its own probe — reinstalling")
            self._probe_at = 0.0
            self._last_seen = now
            self._reinstall = True
            return

        # Quiet for a while. That is normal on an idle machine, so this is a
        # question rather than a verdict; the answer arrives on the next tick.
        self._probe_at = now
        try:
            winapi.tap_probe_key()
        except Exception:  # noqa: BLE001 - a probe that cannot be sent proves nothing
            logger.debug("could not send the hook probe", exc_info=True)
            self._probe_at = 0.0

    def _callback(self, code: int, wparam: int, lparam: int) -> int:
        if code != HC_ACTION:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        started = time.monotonic()
        swallow = False
        self._last_seen = time.monotonic()
        try:
            swallow = self._handle(wparam, lparam)
        except Exception:  # noqa: BLE001 - never let an exception kill the hook
            logger.exception("hotkey callback failed")
        elapsed = time.monotonic() - started
        if elapsed > self.SLOW_CALLBACK_SECONDS:
            # Windows drops hooks whose callback is too slow. Schedule a
            # reinstall rather than discovering later that the hotkey is dead.
            self._reinstall = True
            logger.warning("hotkey callback took %.0f ms", elapsed * 1000)
        if swallow:
            return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _handle(self, wparam: int, lparam: int) -> bool:
        """True to swallow the event. Runs inside the hook — do nothing slow."""
        event = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if event.flags & LLKHF_INJECTED:
            return False  # our own typed dictation, not a user keypress
        if event.vkCode not in self._spec.keys:
            return False

        down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        up = wparam in (WM_KEYUP, WM_SYSKEYUP)
        if not (down or up):
            return False

        # A modifier trigger is never swallowed. Eating Ctrl would break
        # Ctrl+C everywhere; the dictation starts *in addition to* the key
        # doing its normal job, which is how hold-a-modifier push-to-talk is
        # expected to behave.
        eat = not self._spec.modifier_trigger

        if down:
            if self._held:
                return eat  # auto-repeat: swallow, but don't re-fire
            # Plain Space must still be plain Space: only claim the key when
            # the chord's modifiers are actually held. (A chord-less trigger
            # like "f9" has no modifiers, so this passes trivially.)
            if not self._spec.modifiers_held(event.vkCode):
                return False
            self._held = True
            self._begin_press()
            return eat

        if not self._held:
            return False
        self._held = False
        self._end_press()
        return eat

    # ── press timing ─────────────────────────────────────────────────────
    #
    # A normal trigger fires the moment it goes down. A modifier trigger has
    # to wait: Ctrl is also the first half of Ctrl+C, and Ctrl+Alt is what a
    # Russian layout's AltGr sends. Requiring the keys to stay down for
    # HOLD_SECONDS separates "reaching for a shortcut" from "holding a key to
    # talk" — a tap does nothing at all, and the press never starts.

    HOLD_SECONDS = 0.3

    def prerolls(self) -> bool:
        """Should the microphone be opened while the hold is still being
        measured?

        Only for a chord of two or more modifiers. A lone "ctrl" would arm on
        every Ctrl+C, Ctrl+V and Ctrl+S the user types — the recording would
        be discarded each time, but the Windows "microphone in use" indicator
        would blink all day, which is its own kind of alarming.
        """
        return self._spec.modifier_trigger and len(self._spec.modifiers) >= 2

    def _begin_press(self) -> None:
        if not self._spec.modifier_trigger:
            self._on_press()
            return
        with self._press_lock:
            self._cancel_timer()
            self._pressed = False
            self._timer = threading.Timer(self.HOLD_SECONDS, self._hold_elapsed)
            self._timer.daemon = True
            self._timer.start()
        # After the timer is armed, not before: opening a microphone is slow
        # enough that doing it first would push the hold measurement out by
        # however long the device takes.
        if self._on_arm and self.prerolls():
            self._on_arm()

    # Press and release are raised from two different threads — the press from
    # the hold timer, the release from the hook — and BOTH are raised while
    # holding _press_lock. That is the only thing that makes their order
    # certain. Each used to drop the lock first, so a key-up landing exactly as
    # the timer fired could enqueue the release ahead of the press: the app saw
    # a release with nothing recording (ignored), then a press with no key held
    # — a recording that ran until the 300 s cap.
    #
    # Safe to hold across these because the callbacks only put an event on a
    # queue. Nothing here may ever call back into this class.

    def _hold_elapsed(self) -> None:
        with self._press_lock:
            # The key-up may have won the race; it clears _held first, so this
            # check is what keeps a tap from starting a dictation.
            if not self._held or self._pressed:
                return
            self._pressed = True
            self._on_press()

    def _end_press(self) -> None:
        if not self._spec.modifier_trigger:
            self._on_release()
            return
        with self._press_lock:
            self._cancel_timer()
            tap = not self._pressed
            self._pressed = False
            if not tap:
                self._on_release()
                return
        # A tap: nothing was ever started, but the microphone may have been
        # opened for the pre-roll. It has to be closed and its audio dropped,
        # or a Ctrl+Alt reached for by mistake leaves a recording running until
        # the next one. Outside the lock — this one reaches into the app.
        if self._on_disarm and self.prerolls():
            self._on_disarm()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
