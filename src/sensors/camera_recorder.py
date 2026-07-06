import csv
import time
#import cv2
#from digit_interface import Digit

import queue
import threading

import subprocess
import os

import signal




'''class DigitCameraRecorder:
    def __init__(self, output_video, output_timestamps, duration_sec=30,
                 serial_number="D20966", fps=30):
        self.output_video = output_video
        self.output_timestamps = output_timestamps
        self.duration_sec = duration_sec
        self.serial_number = serial_number
        self.fps = fps

    def run(self, session_t0_ns):
        sensor = Digit(self.serial_number)
        sensor.connect()
        sensor.set_fps(self.fps)

        print("DIGIT connected")

        first_frame = cv2.flip(sensor.get_frame(), 1)
        height, width = first_frame.shape[:2]

        writer = cv2.VideoWriter(
            self.output_video,
            cv2.VideoWriter_fourcc(*"mp4v"),
            self.fps,
            (width, height),
        )

        with open(self.output_timestamps, "w", newline="") as f:
            ts_writer = csv.writer(f)
            ts_writer.writerow(["frame_idx", "t_ns", "t_ms"])

            start = time.perf_counter()
            next_frame_time = start
            frame_idx = 0

            while time.perf_counter() - start < self.duration_sec:
                if time.perf_counter() < next_frame_time:
                    time.sleep(0.0005)
                    continue

                t_ns = time.perf_counter_ns()
                t_ms = (t_ns - session_t0_ns) / 1e6

                frame = cv2.flip(sensor.get_frame(), 1)
                writer.write(frame)
                ts_writer.writerow([frame_idx, t_ns, t_ms])

                frame_idx += 1
                next_frame_time += 1.0 / self.fps

        writer.release()
        sensor.disconnect()
        print("DIGIT stopped")

'''

'''
class GelSightMiniRecorder:
    def __init__(
        self,
        output_video,
        output_csv,
        duration_sec=30,
        device="/dev/video0",
        width=3280,
        height=2464,
        fps=25,
        queue_size=120,
    ):
        self.output_video = output_video
        self.output_csv = output_csv
        self.duration_sec = duration_sec
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.queue_size = queue_size

    def run(self, session_t0_ns=None):
        if session_t0_ns is None:
            session_t0_ns = time.perf_counter_ns()

        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)

        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 4)

        if not cap.isOpened():
            raise RuntimeError(f"Could not open GelSight Mini camera: {self.device}")

        ret, frame = cap.read()
        if not ret:
            cap.release()
            raise RuntimeError("Could not read first GelSight frame")

        actual_h, actual_w = frame.shape[:2]

        print("GelSight started")
        print("Requested:", self.width, "x", self.height, "@", self.fps, "FPS")
        print("Actual frame size:", actual_w, "x", actual_h)
        print("Camera-reported FPS:", cap.get(cv2.CAP_PROP_FPS))

        out = cv2.VideoWriter(
            self.output_video,
            cv2.VideoWriter_fourcc(*"MJPG"),
            self.fps,
            (actual_w, actual_h),
        )
'''


class GelSightRawMJPEGRecorder:
    def __init__(
        self,
        output_video,
        output_csv,
        duration_sec=30,
        device="/dev/video0",
        width=3280,
        height=2464,
        fps=25,
    ):
        self.output_video = output_video
        self.output_csv = output_csv
        self.duration_sec = duration_sec
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps

    def run(self, session_t0_ns):
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel", "warning",

            "-f", "v4l2",
            "-input_format", "mjpeg",
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.fps),
            "-use_wallclock_as_timestamps", "1",
            "-i", self.device,

            "-t", str(self.duration_sec),
            "-c:v", "copy",
            "-f", "avi",
            self.output_video,
        ]

        print("Starting raw MJPEG recording")
        print("Command:", " ".join(ffmpeg_cmd))

        start_ns = time.perf_counter_ns()
        start_ms = (start_ns - session_t0_ns) / 1e6

        proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        _, stderr = proc.communicate()

        end_ns = time.perf_counter_ns()
        end_ms = (end_ns - session_t0_ns) / 1e6

        with open(self.output_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["event", "t_ns", "t_ms", "note"])
            writer.writerow([
                "camera_recording_start",
                start_ns,
                start_ms,
                "ffmpeg launched",
            ])
            writer.writerow([
                "camera_recording_end",
                end_ns,
                end_ms,
                "ffmpeg exited",
            ])

        if proc.returncode != 0:
            print(stderr)
            raise RuntimeError("ffmpeg raw MJPEG recording failed")

        elapsed = (end_ns - start_ns) / 1e9

        print("Raw MJPEG recording stopped")
        print("Elapsed:", elapsed)
        print("Expected FPS:", self.fps)
        print("Expected frames:", int(self.duration_sec * self.fps))


if __name__ == "__main__":
    session_t0_ns = time.perf_counter_ns()

    recorder = GelSightRawMJPEGRecorder(
        output_video="gelsight_raw_mjpg.avi",
        output_csv="gelsight_camera_events.csv",
        duration_sec=30,
        device="/dev/video0",
        width=3280,
        height=2464,
        fps=25,
    )

    recorder.run(session_t0_ns)



class GelSightCPURAMsaverRecorder:
    def __init__(
        self,
        output_video,
        output_csv,
        duration_sec,
        device="/dev/video0",
        width=3280,
        height=2464,
        fps=25,
    ):
        self.output_video = output_video
        self.output_csv = output_csv
        self.duration_sec = duration_sec
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps

        self._running = False
        self._timestamp_thread = None
        self._ffmpeg_proc = None

    def _timestamp_worker(self, session_t0_ns):
        period = 1.0 / self.fps

        with open(self.output_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["frame_idx", "t_ns", "t_ms", "estimated"]
            )

            frame_idx = 0
            next_time = time.perf_counter()

            while self._running:
                now_ns = int(time.perf_counter() * 1e9)
                t_ns = now_ns - session_t0_ns

                writer.writerow(
                    [
                        frame_idx,
                        t_ns,
                        t_ns / 1e6,
                        True,
                    ]
                )

                frame_idx += 1

                next_time += period
                sleep_time = next_time - time.perf_counter()

                if sleep_time > 0:
                    time.sleep(sleep_time)

    def run(self, session_t0_ns):
        os.makedirs(os.path.dirname(self.output_video), exist_ok=True)

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "v4l2",
            "-input_format",
            "mjpeg",
            "-video_size",
            f"{self.width}x{self.height}",
            "-framerate",
            str(self.fps),
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            self.device,
            "-t",
            str(self.duration_sec),
            "-c:v",
            "copy",
            self.output_video,
            "-y",
        ]

        print("Starting GelSight MJPEG recording")
        print(" ".join(ffmpeg_cmd))

        self._running = True

        self._timestamp_thread = threading.Thread(
            target=self._timestamp_worker,
            args=(session_t0_ns,),
            daemon=True,
        )
        self._timestamp_thread.start()

        start = time.perf_counter()

        self._ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self._ffmpeg_proc.wait()

        elapsed = time.perf_counter() - start

        self._running = False
        self._timestamp_thread.join()

        print("GelSight stopped")
        print(f"Elapsed: {elapsed:.2f} s")
        print(f"Expected FPS: {self.fps}")
        print(f"Expected frames: {int(elapsed * self.fps)}")

    def stop(self):
        self._running = False

        if (
            self._ffmpeg_proc is not None
            and self._ffmpeg_proc.poll() is None
        ):
            self._ffmpeg_proc.send_signal(signal.SIGINT)

        if self._timestamp_thread is not None:
            self._timestamp_thread.join(timeout=2)
