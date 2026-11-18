#!/usr/bin/env python3
"""Tasvir: switch cameras, take photos and record videos to local folders."""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import shiboken6
from PySide6.QtCore import QEventLoop, QObject, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QKeySequence, QShortcut
from PySide6.QtMultimedia import (
    QAudioInput,
    QCamera,
    QImageCapture,
    QMediaCaptureSession,
    QMediaDevices,
    QMediaFormat,
    QMediaRecorder,
    QVideoSink,
)
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Tasvir"
NO_FRAME_TIMEOUT_MS = 4000
POST_RECORD_REFRESH_MS = 1000
NO_AUDIO = "none"
BACKEND_MISSING = (
    "Qt Multimedia could not load its FFmpeg backend, so no camera is available. "
    "A system library is missing. Run with QT_DEBUG_PLUGINS=1 to see which one, or run install.sh again."
)
VIDEO_CODEC_PREFERENCE = [
    QMediaFormat.VideoCodec.H264,
    QMediaFormat.VideoCodec.MPEG4,
    QMediaFormat.VideoCodec.MotionJPEG,
]


def output_dir(location, root=None):
    if root is not None:
        path = Path(root)
    else:
        base = QStandardPaths.writableLocation(location) or str(Path.home())
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def video_codecs():
    supported = QMediaFormat(QMediaFormat.FileFormat.MPEG4).supportedVideoCodecs(QMediaFormat.ConversionMode.Encode)
    return [codec for codec in VIDEO_CODEC_PREFERENCE if codec in supported]


def video_format(codec):
    fmt = QMediaFormat(QMediaFormat.FileFormat.MPEG4)
    fmt.setVideoCodec(codec)
    fmt.setAudioCodec(QMediaFormat.AudioCodec.AAC)
    return fmt


def matches_camera(device, text):
    return text in device.description() or text == bytes(device.id()).decode()


def best_format(device):
    formats = device.videoFormats()
    if not formats:
        return None
    return max(
        formats,
        key=lambda f: (
            f.resolution().width() * f.resolution().height(),
            f.maxFrameRate(),
        ),
    )


class CameraController(QObject):
    recordingFailed = Signal(str)
    noFrames = Signal(str)

    def __init__(self, output_root=None):
        super().__init__()
        self.output_root = output_root
        self.codecs = video_codecs()
        self.codec_index = 0
        self.record_path = None
        self.stopping = False
        self.frames = 0
        self.frame_timer = QTimer()
        self.frame_timer.setSingleShot(True)
        self.frame_timer.setInterval(NO_FRAME_TIMEOUT_MS)
        self.frame_timer.timeout.connect(self.check_frames)
        self.session = QMediaCaptureSession()
        self.camera = None
        self.audio_input = None
        self.image_capture = QImageCapture()
        self.image_capture.setQuality(QImageCapture.Quality.VeryHighQuality)
        self.recorder = QMediaRecorder()
        self.recorder.setQuality(QMediaRecorder.Quality.HighQuality)
        self.recorder.errorOccurred.connect(self.on_record_error)
        self.session.setImageCapture(self.image_capture)
        self.session.setRecorder(self.recorder)

    def set_camera(self, device, camera_format=None):
        if self.camera is not None:
            old = self.camera
            self.camera = None
            self.session.setCamera(None)
            old.stop()
            shiboken6.delete(old)
        self.camera = QCamera(device)
        chosen = camera_format if camera_format is not None else best_format(device)
        if chosen is not None:
            self.camera.setCameraFormat(chosen)
        self.session.setCamera(self.camera)
        self.frames = 0
        self.camera.start()
        self.frame_timer.start()
        return self.camera

    def set_video_output(self, sink):
        self.session.setVideoOutput(sink)
        sink.videoFrameChanged.connect(self.on_frame)

    def on_frame(self, _frame):
        self.frames += 1

    def check_frames(self):
        if self.frames == 0 and self.camera is not None:
            self.noFrames.emit(
                f"No video from {self.camera.cameraDevice().description()}. Another app may be using this camera."
            )

    def set_audio(self, device):
        if device is None:
            self.session.setAudioInput(None)
            self.audio_input = None
            return
        self.audio_input = QAudioInput(device)
        self.session.setAudioInput(self.audio_input)

    def take_photo(self):
        path = output_dir(QStandardPaths.StandardLocation.PicturesLocation, self.output_root) / f"IMG_{timestamp()}.jpg"
        request_id = self.image_capture.captureToFile(str(path))
        return request_id, path

    def start_recording(self):
        path = output_dir(QStandardPaths.StandardLocation.MoviesLocation, self.output_root) / f"VID_{timestamp()}.mp4"
        self.record_path = path
        self.stopping = False
        self.record_with_current_codec()
        return path

    def record_with_current_codec(self):
        if self.codec_index >= len(self.codecs):
            self.recordingFailed.emit("No working video encoder found.")
            return
        self.recorder.setMediaFormat(video_format(self.codecs[self.codec_index]))
        self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self.record_path)))
        self.recorder.record()

    def on_record_error(self, error, message):
        encoder_error = error in (QMediaRecorder.Error.FormatError, QMediaRecorder.Error.ResourceError)
        has_data = self.recorder.duration() > 0 or (self.record_path.exists() and self.record_path.stat().st_size > 0)
        if encoder_error and not has_data and not self.stopping and self.codec_index + 1 < len(self.codecs):
            self.codec_index += 1
            print(f"{message}; retrying with {self.codecs[self.codec_index].name}", file=sys.stderr)
            QTimer.singleShot(0, self.retry_recording)
            return
        print(f"recording error {error}: {message}", file=sys.stderr)
        self.recordingFailed.emit(message)

    def retry_recording(self):
        if self.stopping:
            return
        if self.recorder.recorderState() != QMediaRecorder.RecorderState.StoppedState:
            QTimer.singleShot(50, self.retry_recording)
            return
        if self.record_path.exists() and self.record_path.stat().st_size == 0:
            self.record_path.unlink()
        self.record_with_current_codec()

    def stop_recording(self):
        self.stopping = True
        self.recorder.stop()


class MainWindow(QMainWindow):
    def __init__(self, output_root=None, camera_filter=""):
        super().__init__()
        self.camera_filter = camera_filter
        self.setWindowTitle("Tasvir")
        self.resize(1100, 760)
        self.devices = QMediaDevices(self)
        self.controller = CameraController(output_root)
        self.record_started = 0.0
        self.record_path = None
        self.pending_refresh = False
        self.camera_choice = None
        self.mic_choice = None

        self.video = QVideoWidget()
        self.video.setMinimumSize(640, 360)
        self.controller.set_video_output(self.video.videoSink())

        self.camera_box = QComboBox()
        self.format_box = QComboBox()
        self.mic_box = QComboBox()
        self.photo_button = QPushButton("Take Photo (Space)")
        self.record_button = QPushButton("Start Recording (R)")
        self.folder_button = QPushButton("Open Folder")
        self.status = QLabel()
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        controls = QHBoxLayout()
        for label, widget in (("Camera", self.camera_box), ("Resolution", self.format_box), ("Mic", self.mic_box)):
            controls.addWidget(QLabel(label))
            controls.addWidget(widget, 1)
        for button in (self.photo_button, self.record_button, self.folder_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        buttons = QHBoxLayout()
        buttons.addWidget(self.photo_button)
        buttons.addWidget(self.record_button)
        buttons.addWidget(self.folder_button)

        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self.video, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.record_timer = QTimer(self)
        self.record_timer.setInterval(500)
        self.record_timer.timeout.connect(self.update_record_time)

        self.camera_box.currentIndexChanged.connect(self.on_camera_chosen)
        self.format_box.currentIndexChanged.connect(self.on_format_selected)
        self.mic_box.currentIndexChanged.connect(self.on_mic_chosen)
        self.photo_button.clicked.connect(self.take_photo)
        self.record_button.clicked.connect(self.toggle_recording)
        self.folder_button.clicked.connect(self.open_folder)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.take_photo)
        QShortcut(QKeySequence(Qt.Key.Key_R), self, self.toggle_recording)

        capture = self.controller.image_capture
        capture.imageSaved.connect(lambda _id, path: self.show_status(f"Photo saved: {path}"))
        capture.errorOccurred.connect(lambda _id, _err, msg: self.show_status(f"Photo error: {msg}"))
        capture.readyForCaptureChanged.connect(self.update_buttons)
        recorder = self.controller.recorder
        recorder.recorderStateChanged.connect(self.on_recorder_state)
        self.controller.noFrames.connect(self.show_status)
        self.controller.recordingFailed.connect(lambda msg: self.show_status(f"Recording error: {msg}"))

        self.devices.videoInputsChanged.connect(self.refresh_cameras)
        self.devices.audioInputsChanged.connect(self.refresh_mics)
        self.refresh_mics()
        self.refresh_cameras()

    def show_status(self, text):
        self.status.setText(text)

    def recording(self):
        return self.controller.recorder.recorderState() != QMediaRecorder.RecorderState.StoppedState

    def refresh_cameras(self):
        if self.recording():
            self.pending_refresh = True
            return
        cameras = QMediaDevices.videoInputs()
        ids = [bytes(c.id()) for c in cameras]
        preferred = [i for i, c in enumerate(cameras) if self.camera_filter and matches_camera(c, self.camera_filter)]
        default_id = bytes(QMediaDevices.defaultVideoInput().id())
        if self.camera_choice in ids:
            selected = ids.index(self.camera_choice)
        elif self.camera_choice is None and preferred:
            selected = preferred[0]
        elif default_id in ids:
            selected = ids.index(default_id)
        else:
            selected = 0
        self.camera_box.blockSignals(True)
        self.camera_box.clear()
        for device in cameras:
            self.camera_box.addItem(device.description(), device)
        if cameras:
            self.camera_box.setCurrentIndex(selected)
        self.camera_box.blockSignals(False)
        if not cameras:
            self.show_status("No camera found. Plug in a camera.")
            self.update_buttons()
            return
        self.on_camera_selected(selected)

    def refresh_mics(self):
        if self.recording():
            self.pending_refresh = True
            return
        mics = QMediaDevices.audioInputs()
        ids = [bytes(m.id()) for m in mics]
        default_id = bytes(QMediaDevices.defaultAudioInput().id())
        if self.mic_choice == NO_AUDIO:
            selected = 0
        elif self.mic_choice in ids:
            selected = ids.index(self.mic_choice) + 1
        elif default_id in ids:
            selected = ids.index(default_id) + 1
        else:
            selected = 0
        self.mic_box.blockSignals(True)
        self.mic_box.clear()
        self.mic_box.addItem("No audio", None)
        for device in mics:
            self.mic_box.addItem(device.description(), device)
        self.mic_box.setCurrentIndex(selected)
        self.mic_box.blockSignals(False)
        self.on_mic_selected(selected)

    def on_camera_chosen(self, index):
        device = self.camera_box.itemData(index)
        if device is not None:
            self.camera_choice = bytes(device.id())
        self.on_camera_selected(index)

    def on_mic_chosen(self, index):
        device = self.mic_box.itemData(index)
        self.mic_choice = bytes(device.id()) if device is not None else NO_AUDIO
        self.on_mic_selected(index)

    def on_camera_selected(self, index):
        device = self.camera_box.itemData(index)
        if device is None:
            return
        camera = self.controller.set_camera(device)
        camera.errorOccurred.connect(lambda _err, msg: self.show_status(f"Camera error: {msg}"))
        self.format_box.blockSignals(True)
        self.format_box.clear()
        formats = sorted(
            device.videoFormats(),
            key=lambda f: (f.resolution().width() * f.resolution().height(), f.maxFrameRate()),
            reverse=True,
        )
        seen = set()
        active = camera.cameraFormat()
        for fmt in formats:
            key = (fmt.resolution().width(), fmt.resolution().height(), int(fmt.maxFrameRate()))
            if key in seen:
                continue
            seen.add(key)
            self.format_box.addItem(f"{key[0]}x{key[1]} @ {key[2]} fps", fmt)
            if fmt == active:
                self.format_box.setCurrentIndex(self.format_box.count() - 1)
        self.format_box.blockSignals(False)
        self.show_status(f"Camera: {device.description()}")
        self.update_buttons()

    def on_format_selected(self, index):
        fmt = self.format_box.itemData(index)
        device = self.camera_box.currentData()
        if fmt is None or device is None:
            return
        camera = self.controller.set_camera(device, fmt)
        camera.errorOccurred.connect(lambda _err, msg: self.show_status(f"Camera error: {msg}"))
        self.update_buttons()

    def on_mic_selected(self, index):
        self.controller.set_audio(self.mic_box.itemData(index))

    def take_photo(self):
        if not self.controller.image_capture.isReadyForCapture():
            self.show_status("Camera is not ready for a photo.")
            return
        self.controller.take_photo()

    def toggle_recording(self):
        recorder = self.controller.recorder
        if recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState:
            if self.controller.camera is None or not self.controller.camera.isActive():
                self.show_status("Camera is not active.")
                return
            self.record_path = self.controller.start_recording()
        else:
            self.controller.stop_recording()

    def on_recorder_state(self, state):
        recording = state == QMediaRecorder.RecorderState.RecordingState
        if recording:
            self.record_started = time.monotonic()
            self.record_timer.start()
            self.update_record_time()
        else:
            self.record_timer.stop()
            self.record_button.setText("Start Recording (R)")
            location = self.controller.recorder.actualLocation().toLocalFile() or str(self.record_path)
            if Path(location).exists() and Path(location).stat().st_size > 0:
                self.show_status(f"Video saved: {location}")
            if self.pending_refresh:
                QTimer.singleShot(POST_RECORD_REFRESH_MS, self.run_pending_refresh)
        self.update_buttons()

    def run_pending_refresh(self):
        if self.recording() or not self.pending_refresh:
            return
        self.pending_refresh = False
        self.refresh_mics()
        self.refresh_cameras()

    def update_record_time(self):
        elapsed = int(time.monotonic() - self.record_started)
        self.record_button.setText(f"Stop Recording {elapsed // 60:02d}:{elapsed % 60:02d} (R)")
        self.show_status(f"Recording: {self.record_path}")

    def update_buttons(self, *_):
        recording = self.controller.recorder.recorderState() != QMediaRecorder.RecorderState.StoppedState
        has_camera = self.camera_box.count() > 0
        self.camera_box.setEnabled(has_camera and not recording)
        self.format_box.setEnabled(has_camera and not recording)
        self.mic_box.setEnabled(not recording)
        self.photo_button.setEnabled(has_camera and self.controller.image_capture.isReadyForCapture())
        self.record_button.setEnabled(has_camera)

    def open_folder(self):
        folder = output_dir(QStandardPaths.StandardLocation.PicturesLocation, self.controller.output_root)
        if self.controller.recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState and self.record_path:
            folder = self.record_path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def closeEvent(self, event):
        if self.controller.recorder.recorderState() != QMediaRecorder.RecorderState.StoppedState:
            loop = QEventLoop()
            self.controller.recorder.recorderStateChanged.connect(loop.quit)
            QTimer.singleShot(5000, loop.quit)
            self.controller.stop_recording()
            loop.exec()
        super().closeEvent(event)


def wait_for(predicate, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        if time.monotonic() > deadline:
            return False
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    return True


def selftest_camera(controller, device, seconds):
    errors = []
    camera = controller.set_camera(device)
    camera.errorOccurred.connect(lambda _err, msg: errors.append(f"camera: {msg}"))
    no_frames = []
    controller.noFrames.connect(no_frames.append)
    ready = wait_for(lambda: (controller.frames > 0 and controller.image_capture.isReadyForCapture()) or no_frames or errors, 10000)
    controller.noFrames.disconnect()
    if not ready or no_frames or errors:
        return False, f"camera did not deliver frames {no_frames} {errors}"

    saved = []
    controller.image_capture.imageSaved.connect(lambda _id, path: saved.append(path))
    controller.image_capture.errorOccurred.connect(lambda _id, _err, msg: errors.append(f"photo: {msg}"))
    _, photo_path = controller.take_photo()
    ok = wait_for(lambda: saved or errors, 10000)
    controller.image_capture.imageSaved.disconnect()
    controller.image_capture.errorOccurred.disconnect()
    image = QImage(str(photo_path))
    if not ok or errors or image.isNull():
        return False, f"photo failed {photo_path} {errors}"

    controller.recordingFailed.connect(lambda msg: errors.append(f"record: {msg}"))
    video_path = controller.start_recording()
    if not wait_for(lambda: controller.recorder.recorderState() == QMediaRecorder.RecorderState.RecordingState or errors, 10000):
        return False, f"recording did not start {errors}"
    wait_for(lambda: bool(errors), seconds * 1000)
    controller.stop_recording()
    stopped = wait_for(lambda: controller.recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState, 10000)
    controller.recordingFailed.disconnect()
    actual = Path(controller.recorder.actualLocation().toLocalFile() or video_path)
    duration_ms = controller.recorder.duration()
    if not stopped or errors or not actual.exists() or actual.stat().st_size == 0:
        return False, f"video failed {actual} {errors}"
    return True, f"photo {photo_path} ({image.width()}x{image.height()}), video {actual} ({actual.stat().st_size} bytes, {duration_ms} ms)"


def run_selftest(camera_filter, seconds, output_root):
    controller = CameraController(output_root)
    sink = QVideoSink()
    controller.set_video_output(sink)
    if QMediaDevices.audioInputs():
        controller.set_audio(QMediaDevices.defaultAudioInput())
    cameras = QMediaDevices.videoInputs()
    if camera_filter:
        cameras = [c for c in cameras if matches_camera(c, camera_filter)]
    if not cameras:
        print("FAIL: no camera matched")
        return 1
    failures = 0
    for device in cameras:
        ok, detail = selftest_camera(controller, device, seconds)
        print(f"{'PASS' if ok else 'FAIL'}: {device.description()}: {detail}", flush=True)
        failures += 0 if ok else 1
    if controller.camera is not None:
        controller.camera.stop()
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description="Switch cameras, take photos and record videos.")
    parser.add_argument("--selftest", action="store_true", help="capture a photo and a video from each camera, then exit")
    parser.add_argument("--camera", default="", help="camera name substring or device id to start with (selftest: cameras to test)")
    parser.add_argument("--output", help="save photos and videos to this folder instead of ~/Pictures/Tasvir and ~/Videos/Tasvir")
    parser.add_argument("--seconds", type=int, default=3, help="selftest only: video length")
    args, qt_args = parser.parse_known_args()
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("tasvir")
    probe = CameraController()
    if not probe.recorder.isAvailable():
        print(BACKEND_MISSING, file=sys.stderr)
        if not args.selftest:
            QMessageBox.critical(None, "Tasvir", BACKEND_MISSING)
        return 1
    if args.selftest:
        return run_selftest(args.camera, args.seconds, args.output)
    window = MainWindow(args.output, args.camera)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
