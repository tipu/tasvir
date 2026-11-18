# Linux Camera

A small desktop camera app for Linux. It shows a live preview, switches between cameras, takes photos and records videos with sound. Files save locally:

- Photos: `~/Pictures/LinuxCamera/IMG_<timestamp>.jpg`
- Videos: `~/Videos/LinuxCamera/VID_<timestamp>.mp4`

The folders follow your XDG user directories, so a localized `Pictures` or `Videos` folder works too.

## Why it works across distributions

The app is one Python file on top of the PySide6 (Qt 6) wheels from PyPI. Those wheels bundle Qt and an FFmpeg build, so the app does not depend on the distribution's GStreamer or Qt packages. It reads cameras through V4L2 and microphones through PulseAudio or PipeWire. It runs on X11 and on Wayland.

Requirements: x86_64 with glibc 2.34 or newer (Ubuntu 22.04+, Debian 12+, Fedora 35+, RHEL 9+, Arch), or aarch64 with glibc 2.39 or newer. Python 3.10 or newer. These limits come from the PySide6 6.12 wheels.

Video uses H.264 when a working hardware encoder exists. When H.264 fails to start, the app retries with MPEG-4 Part 2, which the bundled FFmpeg always encodes in software.

## Install

```bash
./install.sh
```

The script needs `python3` (3.10 or newer) with the `venv` module. It stops with a clear message when Python, glibc or a system library that the camera backend links against is missing. It creates a virtual environment in `~/.local/share/linux-camera`, adds the `linux-camera` command to `~/.local/bin` and adds a menu entry. Remove it with `./install.sh --uninstall`.

On X11 Qt also needs `libxcb-cursor0` (Fedora and Arch: `xcb-util-cursor`). The installer warns when a display backend misses a library.

## Use

| Control | Action |
| --- | --- |
| Camera list | Switch camera. Hot-plugged cameras appear on their own. |
| Resolution list | Pick a camera mode. The app starts with the largest one. |
| Mic list | Pick a microphone, or `No audio`. |
| Take Photo or `Space` | Save a JPEG. |
| Start Recording or `R` | Start or stop a video. Camera and mic switching lock while recording. |
| Open Folder | Open the save folder in the file manager. |

Options:

```bash
linux-camera --camera "Cam A"          # start with the first camera whose name contains "Cam A"
linux-camera --output ~/Desktop/shots  # save photos and videos in one other folder
```

When another program (for example OBS) holds a camera, the preview stays black and the status line says that the camera gives no video.

## Test

Development setup:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Headless self-test. It takes a photo and a 3 second video from each camera, checks the files and exits non-zero on any failure:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python linux_camera.py --selftest --output /tmp/lc-test
```

GUI smoke test. It opens the real window, switches through the cameras, clicks Take Photo and presses `R` twice for each one:

```bash
.venv/bin/python tests/smoke_gui.py --cameras "Cam A,Cam B" --output /tmp/lc-gui
```
