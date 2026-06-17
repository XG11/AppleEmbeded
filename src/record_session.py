import os
import time
import json
import threading
from datetime import datetime

from sensors.imu_fifo_i2c import IMUFIFORecorder
from sensors.camera_recorder import GelSightMiniRecorder


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

    camera = GelSightMiniRecorder(
        output_video=os.path.join(session_dir, "gelsight.avi"),
        output_csv=os.path.join(session_dir, "gelsight_timestamps.csv"),
        duration_sec=DURATION_SEC,
        device="/dev/video0",
        width= 640, #3280,
        height= 480, #2464,
        fps=30,
    )

    metadata = {
        "session_name": session_name,
        "duration_sec": DURATION_SEC,
        "session_t0_ns": session_t0_ns,
        "imu": {
            "sensor": "LSM6DSO32",
            "mode": "I2C FIFO accel only",
            "CTRL1_XL": "0xAC",
            "CTRL2_G": "0x00",
            "FIFO_CTRL3": "0x0A",
            "accel_scale_g_per_lsb": 0.000976 / 2,
        },
        "camera": {
            "sensor": "DIGIT",
            "serial_number": "D20966",
            "fps_requested": 30,
            "interface": "digit-interface",
            "video_file": "digit.mp4",
            "timestamp_file": "digit_timestamps.csv"
        },
    }

    with open(os.path.join(session_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    imu_thread = threading.Thread(target=imu.run, args=(session_t0_ns,))
    cam_thread = threading.Thread(target=camera.run, args=(session_t0_ns,))

    print("Recording to:", session_dir)

    imu_thread.start()
    #cam_thread.start()

    imu_thread.join()
    #cam_thread.join()

    print("Done.")
    print("Saved:", session_dir)


if __name__ == "__main__":
    main()