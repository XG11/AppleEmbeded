import csv
import sys
import threading
from pathlib import Path
from typing import Optional

import cv2

from .shared import SharedRecordingState, monotonic_ns


class GelSightRecorder:
    def __init__(
        self,
        state: SharedRecordingState,
        camera_index: int,
        video_path: Path,
        timestamp_path: Path,
        width: int = 0,
        height: int = 0,
        fps: float = 30.0,
        use_mjpeg_input: bool = True,
    ) -> None:
        self.state = state

        self.camera_index = camera_index
        self.video_path = Path(video_path)
        self.timestamp_path = Path(timestamp_path)

        self.requested_width = width
        self.requested_height = height
        self.requested_fps = fps
        self.use_mjpeg_input = use_mjpeg_input

        self.ready_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.frame_count = 0
        self.failed_frame_count = 0

        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name="gelsight-recorder",
            daemon=True,
        )
        self.thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def _open_camera(self):
        # AVFoundation is normally preferable on macOS.
        if sys.platform == "darwin":
            capture = cv2.VideoCapture(
                self.camera_index,
                cv2.CAP_AVFOUNDATION,
            )

            if capture.isOpened():
                return capture

            capture.release()

        return cv2.VideoCapture(self.camera_index)

    def _run(self) -> None:
        capture = None
        video_writer = None

        try:
            self.video_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            self.timestamp_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            capture = self._open_camera()

            if not capture.isOpened():
                raise RuntimeError(
                    f"Could not open camera index {self.camera_index}"
                )

            if self.use_mjpeg_input:
                capture.set(
                    cv2.CAP_PROP_FOURCC,
                    cv2.VideoWriter_fourcc(*"MJPG"),
                )

            if self.requested_width > 0:
                capture.set(
                    cv2.CAP_PROP_FRAME_WIDTH,
                    self.requested_width,
                )

            if self.requested_height > 0:
                capture.set(
                    cv2.CAP_PROP_FRAME_HEIGHT,
                    self.requested_height,
                )

            if self.requested_fps > 0:
                capture.set(
                    cv2.CAP_PROP_FPS,
                    self.requested_fps,
                )

            self.actual_width = int(
                capture.get(cv2.CAP_PROP_FRAME_WIDTH)
            )
            self.actual_height = int(
                capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
            )
            self.actual_fps = float(
                capture.get(cv2.CAP_PROP_FPS)
            )

            if self.actual_width <= 0 or self.actual_height <= 0:
                raise RuntimeError(
                    "Camera returned an invalid frame size"
                )

            if self.actual_fps <= 0:
                if self.requested_fps > 0:
                    self.actual_fps = self.requested_fps
                else:
                    self.actual_fps = 30.0

            video_writer = cv2.VideoWriter(
                str(self.video_path),
                cv2.VideoWriter_fourcc(*"MJPG"),
                self.actual_fps,
                (
                    self.actual_width,
                    self.actual_height,
                ),
            )

            if not video_writer.isOpened():
                raise RuntimeError(
                    f"Could not open video writer: {self.video_path}"
                )

            with self.timestamp_path.open(
                "w",
                newline="",
            ) as timestamp_file:

                timestamp_writer = csv.writer(timestamp_file)

                timestamp_writer.writerow(
                    [
                        "frame_index",
                        "host_time_ns",
                        "host_elapsed_s",
                    ]
                )

                print(
                    "GelSight opened: "
                    f"{self.actual_width}x{self.actual_height} "
                    f"at {self.actual_fps:.2f} FPS"
                )

                self.ready_event.set()
                self.state.start_event.wait()

                while not self.state.stop_event.is_set():
                    success, frame = capture.read()
                    host_time_ns = monotonic_ns()

                    if not success or frame is None:
                        self.failed_frame_count += 1
                        continue

                    video_writer.write(frame)

                    host_elapsed_s = (
                        host_time_ns - self.state.session_t0_ns
                    ) / 1_000_000_000.0

                    timestamp_writer.writerow(
                        [
                            self.frame_count,
                            host_time_ns,
                            f"{host_elapsed_s:.9f}",
                        ]
                    )

                    self.frame_count += 1

                timestamp_file.flush()

        except Exception as exc:
            self.ready_event.set()
            self.state.report_error(
                "GelSight",
                f"{type(exc).__name__}: {exc}",
            )

        finally:
            if video_writer is not None:
                video_writer.release()

            if capture is not None:
                capture.release()
                