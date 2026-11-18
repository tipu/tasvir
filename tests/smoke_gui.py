#!/usr/bin/env python3
"""Drive the real window: switch cameras, take a photo and record a video with each one."""

import argparse
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from linux_camera import MainWindow


MIN_VIDEO_BYTES = 50000


def wait_for(predicate, timeout_s):
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            return False
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    return True


def files(folder, pattern):
    return set(Path(folder).glob(pattern))


def exercise_camera(window, index, output):
    name = window.camera_box.itemText(index)
    window.camera_box.setCurrentIndex(index)
    controller = window.controller
    if not wait_for(lambda: controller.frames > 0 and window.photo_button.isEnabled(), 10):
        return False, f"{name}: no frames: {window.status.text()}"

    photos_before = files(output, "IMG_*.jpg")
    QTest.mouseClick(window.photo_button, Qt.MouseButton.LeftButton)
    if not wait_for(lambda: window.status.text().startswith("Photo saved"), 10):
        return False, f"{name}: photo not saved: {window.status.text()}"
    new_photos = files(output, "IMG_*.jpg") - photos_before
    if len(new_photos) != 1 or QImage(str(next(iter(new_photos)))).isNull():
        return False, f"{name}: photo file invalid: {new_photos}"

    videos_before = files(output, "VID_*.mp4")
    QTest.keyClick(window, Qt.Key.Key_R)
    if not wait_for(lambda: window.record_button.text().startswith("Stop Recording"), 10):
        return False, f"{name}: recording did not start: {window.status.text()}"
    if window.camera_box.isEnabled():
        return False, f"{name}: camera switch not locked during recording"
    camera_before = controller.camera
    audio_before = controller.audio_input
    window.refresh_cameras()
    window.refresh_mics()
    if controller.camera is not camera_before or controller.audio_input is not audio_before:
        return False, f"{name}: device refresh replaced the camera or mic during recording"
    wait_for(lambda: False, 3)
    QTest.keyClick(window, Qt.Key.Key_R)
    if not wait_for(lambda: window.status.text().startswith("Video saved"), 15):
        return False, f"{name}: video not saved: {window.status.text()}"
    wait_for(lambda: False, 1)
    new_videos = [p for p in files(output, "VID_*.mp4") - videos_before if p.stat().st_size > MIN_VIDEO_BYTES]
    if len(new_videos) != 1:
        return False, f"{name}: video file invalid: {new_videos}"
    if not wait_for(lambda: not window.pending_refresh and controller.camera is not camera_before and controller.frames > 0, 10):
        return False, f"{name}: device refresh did not recreate the camera after recording"
    return True, f"{name}: {next(iter(new_photos)).name}, {new_videos[0].name} ({new_videos[0].stat().st_size} bytes)"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cameras", default="", help="comma separated camera name substrings; default all")
    parser.add_argument("--output", default="")
    parser.add_argument("--screenshot", default="")
    args, qt_args = parser.parse_known_args()
    output = args.output or tempfile.mkdtemp(prefix="linux-camera-")
    app = QApplication([sys.argv[0], *qt_args])
    window = MainWindow(output)
    window.show()
    window.activateWindow()
    if not QTest.qWaitForWindowActive(window, 5000):
        print("FAIL: window did not become active")
        return 1
    wanted = [w for w in args.cameras.split(",") if w]
    indexes = [
        i for i in range(window.camera_box.count())
        if not wanted or any(w in window.camera_box.itemText(i) for w in wanted)
    ]
    if not indexes:
        print("FAIL: no camera matched")
        return 1
    failures = 0
    for index in indexes:
        ok, detail = exercise_camera(window, index, output)
        print(f"{'PASS' if ok else 'FAIL'}: {detail}", flush=True)
        failures += 0 if ok else 1
    if args.screenshot:
        window.grab().save(args.screenshot)
    window.close()
    app.processEvents()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
