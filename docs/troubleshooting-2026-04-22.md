# Troubleshooting session — 2026-04-22

## Symptom

After a system restart, pressing the Copilot button activates the microphone
but no text is typed. Before the restart everything worked.

---

## Root causes found (and fixed)

### 1. ydotool client/server version mismatch

**What happened.**
Two versions of ydotool exist on this machine:

| Path | Source | Notes |
|---|---|---|
| `/usr/bin/ydotool` | apt (`0.1.8-3build1`) | old, but compatible with running daemon |
| `/usr/local/bin/ydotoold` | locally compiled (newer) | the running daemon |
| `/usr/local/bin/ydotool` | locally compiled (newer) | **incompatible with daemon above** |

`/usr/local/bin` appears first in PATH, so `nerd-dictation` resolved `ydotool`
to the newer client. That client uses a different wire protocol than the
older daemon → every `ydotool type` call returned exit code 2 with
"Protocol wrong type for socket".

**Fix applied.**
Hard-coded the full path in `nerd-dictation` line ~173:

```python
# Before
cmd = "ydotool"
# After
cmd = "/usr/bin/ydotool"
```

**Verification.** After this change, service logs show:
```
ydotool: notice: Using ydotoold backend
Key delay was set to 5 milliseconds.
```

---

### 2. ydotoold socket path mismatch

**What happened.**
`ydotoold` (both builds) creates its Unix socket at `/tmp/.ydotool_socket`
(hard-coded default). The `ydotool` client defaults to
`$XDG_RUNTIME_DIR/.ydotool_socket` = `/run/user/1000/.ydotool_socket`.
After a restart, the socket at `/run/user/1000/` is stale (no daemon
listening) so every connection attempt got "Connection refused".

We also attempted to fix this via a systemd drop-in:
```ini
# ~/.config/systemd/user/ydotoold.service.d/socket-path.conf
[Service]
ExecStart=
ExecStart=/usr/bin/ydotoold --socket-path %t/.ydotool_socket
```
The `--socket-path` flag is documented in `ydotoold --help` but is silently
ignored by the installed binary — `ss -xlp` confirmed the daemon kept
listening on `/tmp/.ydotool_socket` regardless. The drop-in was removed.

**Fix applied.**
`push-to-talk.py` now sets `YDOTOOL_SOCKET` in `os.environ` at startup so
all child processes (nerd-dictation → ydotool) inherit the correct path:

```python
YDOTOOL_SOCKET = "/tmp/.ydotool_socket"

def main() -> None:
    os.environ["YDOTOOL_SOCKET"] = YDOTOOL_SOCKET
    ...
```

---

### 3. keyd grabbing the touchpad

**What happened.**
Enabling `keyd` to remap the Copilot key stopped the touchpad from working.
The existing `/etc/keyd/default.conf` used:

```ini
[ids]
*        # grabs ALL input devices, including the Synaptics touchpad
```

**Fix applied.**
Restricted to the keyboard's vendor:product ID only:

```ini
[ids]
0001:0001   # AT Translated Set 2 keyboard only
```

The touchpad (`06cb:cef5`) is no longer grabbed by keyd.

---

### 4. keyd remapping Copilot to F21 toggled the touchpad (new — fixed 2026-04-22)

**What happened.**
After switching the keyd remap target to `f21`, pressing the Copilot key
began toggling the touchpad on and off (with a GNOME indicator appearing
on-screen).

Root cause: in the xkb evdev keymap, `KEY_F21` (Linux evdev code 191, X11
keycode 199) is assigned the keysym `XF86TouchpadToggle`. keyd strips the
Meta+Shift modifiers before emitting the key, so GNOME's media-keys handler
sees a bare `XF86TouchpadToggle` and fires.

```
# verified with:
xmodmap -pke | grep "keycode 199"
# keycode 199 = XF86TouchpadToggle NoSymbol XF86TouchpadToggle
```

Similarly, F22 = `XF86TouchpadOn` and F23 = `XF86TouchpadOff`. X11 keycode
202 (KEY_F24) is unassigned and safe to use.

**Fix applied.**

`/etc/keyd/default.conf`:
```ini
# Before
f23 = f21
# After
f23 = f24
```

`push-to-talk.py`:
```python
# Before
watch_key = ecodes.KEY_F21 if keyd_active else ecodes.KEY_F23
# After
watch_key = ecodes.KEY_F24 if keyd_active else ecodes.KEY_F23
```

---

### 5. BUILTIN_MIC pointed at headphone jack, not digital mic (new — fixed 2026-04-22)

**What happened.**
`nerd-dictation begin` ran successfully but consistently logged
`No text found in the audio`. Test recording confirmed the captured signal
was essentially silent (RMS ≈ 3, max 113 out of 32768).

Root cause: `BUILTIN_MIC` in `push-to-talk.py` was set to
`...sofhdadsp__source`, which PipeWire describes as
**"Headphones Stereo Microphone"** — the 3.5 mm jack input. Nothing was
plugged in, so the capture was silence. The actual built-in microphone
array is `...sofhdadsp_6__source` ("Digital Microphone", 4-channel DMIC).

```bash
# confirmed with:
pactl list sources | grep -E "Name:|Description:"
# ...sofhdadsp__source   → Headphones Stereo Microphone  (RMS ≈ 3 — silent)
# ...sofhdadsp_6__source → Digital Microphone            (RMS ≈ 283 — live)
```

**Fix applied.**

`push-to-talk.py`:
```python
# Before
BUILTIN_MIC = "alsa_input.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp__source"
# After
BUILTIN_MIC = "alsa_input.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__hw_sofhdadsp_6__source"
```

---

## Changes made to files

| File | Change |
|---|---|
| `nerd-dictation` | `cmd = "/usr/bin/ydotool"` (was `"ydotool"`) |
| `push-to-talk.py` | `YDOTOOL_SOCKET`; adaptive keyd detection; `BUILTIN_MIC` → `sofhdadsp_6__source`; watches `KEY_F24` (was `KEY_F21`); `get_mic()` checks card connection state |
| `/etc/keyd/default.conf` | `[ids]` changed from `*` to `0001:0001`; remap target changed from `f21` to `f24` |
| `~/.config/systemd/user/ydotoold.service.d/` | Created then removed (broken approach) |

---

## Current state (as of 2026-04-22 session end)

- ydotool connects successfully to the daemon.
- Copilot key no longer toggles the touchpad.
- Dictation works end-to-end with the built-in digital microphone.
- **Still unresolved**: AirPods do not work reliably for dictation (see below).

---

## Known bug: AirPods unreliable for dictation

### Attempt 1 — drop the RUNNING check (did not fix it)

**Hypothesis.**
`get_mic()` checked `RUNNING` state; PipeWire suspends idle sources after ~10 s,
so after the first dictation the AirPods source went `SUSPENDED` and the check
failed, silently falling back to the built-in mic.

**Fix attempted.**
Removed `and "RUNNING" in line` from the `pactl list sources short` loop.

**Why it failed.**
The `bluez_input` source entry **persists in `pactl list sources short` even
when the AirPods are fully disconnected** (`api.bluez5.connection = disconnected`).
Removing the state check caused `get_mic()` to always return the AirPods source
regardless of whether they were actually on and connected, so dictation silently
recorded from a dead source.

```bash
# Confirmed: disconnected AirPods still appear as SUSPENDED source
pactl list sources short | grep bluez
# 1845  bluez_input.68_CA_C4_CB_B7_3D.0  PipeWire  s16le 1ch 16000Hz  SUSPENDED
```

### Attempt 2 — check card connection state (current code, untested)

**Fix applied.**
`get_mic()` now parses `pactl list cards` to find BT cards where
`api.bluez5.connection = "connected"`, extracts their MAC addresses, then
matches against the `bluez_input` source name. Only genuinely connected devices
are selected.

```python
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
```

**Status.** Applied but not yet verified with AirPods connected. If this still
fails, the next things to check are:

- **HFP profile not activating**: AirPods Pro on this machine only show HSP/HFP
  profiles (no A2DP). When nerd-dictation opens the `bluez_input` source,
  PipeWire/WirePlumber must switch the card to an HFP profile. If the profile
  switch fails or takes too long, recording starts before the mic is ready.
  Check `pactl list cards` while dictating to see what profile is active.

- **mSBC codec negotiation delay**: Active profile is `headset-head-unit-msbc`.
  mSBC requires Bluetooth SCO connection negotiation which can take several
  hundred milliseconds. nerd-dictation may start consuming the stream before
  audio flows, dropping the first words.

- **WirePlumber auto-switch policy**: Check
  `/usr/share/wireplumber/bluetooth.lua.d/` for profile auto-switch rules.
  A rule that switches away from HFP when no output is active could break
  things mid-session.
