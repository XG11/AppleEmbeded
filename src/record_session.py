import os
import time
import json
import threading
from datetime import datetime

from sensors.imu_fifo_i2c import IMUFIFORecorder
from sensors.camera_recorder import GelSightMiniRecorder, GelSightRawMJPEGRecorder, GelSightCPURAMsaverRecorder


DURATION_SEC = 30


def main():
    session_name = datetime.now().strftime("session_%Y%m%d_%H%M%S")
    session_dir = os.path.join(os.getcwd(), session_name)
    os.makedirs(session_dir, exist_ok=True)

    session_t0_ns = time.perf_counter_ns()

    imu = IMUFIFORecorder(
        output_csv=os.path.join(session_dir, "imu_fifo.csv"),
        duration_sec=DURATION_SEC,
    )

    camera = GelSightCPURAMsaverRecorder(
        output_video=os.path.join(session_dir, "gelsight_raw_mjpeg.avi"),
        output_csv=os.path.join(session_dir, "gelsight_timestamps_estimated.csv"),
        duration_sec=DURATION_SEC,
        device="/dev/video0",
        width=3280,
        height=2464,
        fps=25,
    )

    metadata = {
        "session_name": session_name,
        "duration_sec": DURATION_SEC,
        "session_t0_ns": session_t0_ns,
        "camera": {
            "sensor": "GelSight Mini",
            "device": "/dev/video0",
            "width": 3280,
            "height": 2464,
            "fps": 25,
            "format": "MJPEG",
            "recording_method": "ffmpeg -c copy",
            "video_file": "gelsight_raw_mjpeg.avi",
            "timestamp_file": "gelsight_timestamps_estimated.csv",
            "timestamp_warning": "Frame timestamps are estimated from recording start and FPS, not hardware frame arrival times.",
        },
        "imu": {
            "sensor": "LSM6DSO32",
            "mode": "I2C FIFO accel only",
            "output_file": "imu_fifo.csv",
        },
    }

    with open(os.path.join(session_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    imu_thread = threading.Thread(target=imu.run, args=(session_t0_ns,))
    cam_thread = threading.Thread(target=camera.run, args=(session_t0_ns,))

    print("Recording to:", session_dir)

    imu_thread.start()
    cam_thread.start()

    imu_thread.join()
    cam_thread.join()

    print("Done.")
    print("Saved:", session_dir)


if __name__ == "__main__":
    main()