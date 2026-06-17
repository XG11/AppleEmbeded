import csv
import time
import cv2
from digit_interface import Digit


class DigitCameraRecorder:
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


class GelSightMiniRecorder:
    def __init__(
        self,
        output_video,
        output_csv,
        duration_sec=30,
        device="/dev/video0",
        width= 640, #3280,
        height= 480, #2464,
        fps=30,
        save_preview_size=False,
    ):
        self.output_video = output_video
        self.output_csv = output_csv
        self.duration_sec = duration_sec
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.save_preview_size = save_preview_size

    def run(self, session_t0_ns):
        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)

        if not cap.isOpened():
            raise RuntimeError(f"Could not open GelSight Mini camera: {self.device}")

        ret, frame = cap.read()
        if not ret:
            cap.release()
            raise RuntimeError("Could not read first GelSight frame")

        actual_h, actual_w = frame.shape[:2]
        print("GelSight started")
        print("Actual frame size:", actual_w, "x", actual_h)

        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        out = cv2.VideoWriter(
            self.output_video,
            fourcc,
            self.fps,
            (actual_w, actual_h),
        )

        if not out.isOpened():
            cap.release()
            raise RuntimeError("Could not open video writer")

        start_time = time.perf_counter()
        frame_idx = 0

        with open(self.output_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["frame_idx", "t_ns", "t_ms"])

            # write first frame
            t_ns = time.perf_counter_ns()
            writer.writerow([frame_idx, t_ns, (t_ns - session_t0_ns) / 1e6])
            out.write(frame)
            frame_idx += 1

            while time.perf_counter() - start_time < self.duration_sec:
                ret, frame = cap.read()
                if not ret:
                    continue

                t_ns = time.perf_counter_ns()
                t_ms = (t_ns - session_t0_ns) / 1e6

                out.write(frame)
                writer.writerow([frame_idx, t_ns, t_ms])
                frame_idx += 1

        cap.release()
        out.release()

        print("GelSight stopped")
        print("GelSight frames:", frame_idx)
        print("GelSight FPS:", frame_idx / self.duration_sec)