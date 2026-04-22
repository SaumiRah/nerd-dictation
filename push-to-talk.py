#!/usr/bin/env python3
"""Push-to-talk for nerd-dictation.
Hold the Copilot button (Meta+Shift+F23) to dictate; release to finish.

When keyd is running it remaps the chord to KEY_F24, stripping Super/Shift
from reaching the compositor. This script detects keyd automatically and
reads from whichever virtual keyboard is available.
"""

import os
import subprocess
import sys
from pathlib import Path

import evdev
from evdev import ecodes

NERD_DICTATION    = str(Path(__file__).parent / "nerd-dictation")
VOSK_MODEL_DIR    = str(Path(__file__).parent / "model")
PHYSICAL_KB_NAME  = "AT Translated Set 2 keyboard"
KEYD_KB_NAME      = "keyd virtual keyboard"
BUILTIN_MIC       = "alsa_input.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp_6__source"
INPUT_TOOL        = "YDOTOOL"
PYTHON            = sys.executable

# ydotoold on this machine uses /tmp/.ydotool_socket regardless of config
YDOTOOL_SOCKET = "/tmp/.ydotool_socket"


def find_keyboard() -> tuple[evdev.InputDevice, bool]:
    """Return (device, keyd_active). Prefers keyd virtual keyboard."""
    keyd_dev = physical_dev = None
    for path in evdev.list_devices():
        d = evdev.InputDevice(path)
        if d.name == KEYD_KB_NAME:
            keyd_dev = d
        elif d.name == PHYSICAL_KB_NAME:
            physical_dev = d
    if keyd_dev:
        return keyd_dev, True
    if physical_dev:
        return physical_dev, False
    sys.exit(f"Keyboard not found (tried {KEYD_KB_NAME!r} and {PHYSICAL_KB_NAME!r})")


def get_mic() -> str:
    """Return Bluetooth mic if a BT audio device is connected, otherwise built-in mic.

    Checking source state (RUNNING/SUSPENDED) is not sufficient: the bluez_input
    source persists in pactl even when the device is fully disconnected. Instead,
    look at the card's api.bluez5.connection property and only use the BT source
    when the card is genuinely connected.
    """
    try:
        cards = subprocess.check_output(["pactl", "list", "cards"], text=True)
        sources = subprocess.check_output(["pactl", "list", "sources", "short"], text=True)

        connected_macs: set[str] = set()
        mac = ""
        for line in cards.splitlines():
            s = line.strip()
            if s.startswith("api.bluez5.address"):
                mac = s.split('"')[1].replace(":", "_")
            elif s.startswith("api.bluez5.connection") and '"connected"' in s:
                connected_macs.add(mac)

        for line in sources.splitlines():
            parts = line.split()
            if len(parts) >= 2 and "bluez_input" in parts[1]:
                for m in connected_macs:
                    if m in parts[1]:
                        return parts[1]
    except Exception:
        pass
    return BUILTIN_MIC


def run(cmd: list[str]) -> None:
    subprocess.Popen([PYTHON] + cmd)


def main() -> None:
    os.environ["YDOTOOL_SOCKET"] = YDOTOOL_SOCKET

    device, keyd_active = find_keyboard()
    # With keyd running, Meta+Shift+F23 is remapped to F21, stripping the
    # modifiers from reaching the compositor.
    watch_key = ecodes.KEY_F24 if keyd_active else ecodes.KEY_F23
    chord = {watch_key}

    print(f"Push-to-talk active on {device.path} ({device.name})")
    print(f"{'keyd active — watching KEY_F21' if keyd_active else 'keyd not active — watching KEY_F23 on physical keyboard'}")

    held: set[int] = set()
    recording = False

    for event in device.read_loop():
        if event.type != ecodes.EV_KEY:
            continue

        if event.value == 1:
            held.add(event.code)
            if event.code == watch_key and not recording:
                recording = True
                mic = get_mic()
                print(f"Recording via: {mic}")
                run([NERD_DICTATION, "begin",
                     f"--vosk-model-dir={VOSK_MODEL_DIR}",
                     f"--simulate-input-tool={INPUT_TOOL}",
                     f"--pulse-device-name={mic}",
                     "--defer-output"])

        elif event.value == 0:
            held.discard(event.code)
            if recording and not (held & chord):
                recording = False
                run([NERD_DICTATION, "end"])


if __name__ == "__main__":
    main()
