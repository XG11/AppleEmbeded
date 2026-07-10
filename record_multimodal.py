#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path
from typing import Optional

import cv2
import sounddevice as sd
from serial.tools import list_ports

from recorder import RecordingSession


def find_teensy_port() -> Optional[str]:
    for port in list_ports.comports():
        description = (port.description or "").lower()
        manufacturer = (port.manufacturer or "").lower()

        if (
            "teensy" in description
            or "teensy" in manufacturer
            or port.vid == 0x16C0
        ):
            return port.device

    return None


def list_serial_devices() -> None:
    print("\nSerial devices:")

    for port in list_ports.comports():
        print(
            f"  {port.device}: "
            f"{port.description}, "
            f"VID={port.vid}, PID={port.pid}"
        )


def list_audio_devices() -> None:
    print("\nAudio devices:")
    print(sd.query_devices())


def open_camera(index: int):
    if sys.platform == "darwin":
        capture = cv2.VideoCapture(
            index,
            cv2.CAP_AVFOUNDATION,
        )

        if capture.isOpened():
            return capture

        capture.release()

    return cv2.VideoCapture(index)


def list_cameras(max_index: int = 10) -> None:
    print("\nCameras:")

    found = False

    for index in range(max_index):
        capture = open_camera(index)

        if not capture.isOpened():
            capture.release()
            continue

        width = int(
            capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        )
        height = int(
            capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        )
        fps = float(
            capture.get(cv2.CAP_PROP_FPS)
        )

        print(
            f"  Camera {index}: "
            f"{width}x{height} at {fps:.2f} FPS"
        )

        found = True
        capture.release()

    if not found:
        print("  No cameras found through OpenCV.")


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Record Teensy IMU, GelSight camera, "
            "and USB microphone."
        )
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Recording duration in seconds.",
    )

    parser.add_argument(
        "--serial-port",
        type=str,
        default=None,
        help=(
            "Teensy serial port. "
            "Automatically detected when omitted."
        ),
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=921600,
    )

    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="OpenCV camera index for GelSight.",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=0,
        help="Requested camera width.",
    )

    parser.add_argument(
        "--height",
        type=int,
        default=0,
        help="Requested camera height.",
    )

    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Requested camera FPS.",
    )

    parser.add_argument(
        "--audio-device",
        type=int,
        default=None,
        help="sounddevice microphone index.",
    )

    parser.add_argument(
        "--audio-rate",
        type=int,
        default=48000,
    )

    parser.add_argument(
        "--audio-channels",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--audio-block-size",
        type=int,
        default=1024,
    )

    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("recordings"),
    )

    parser.add_argument(
        "--list-devices",
        action="store_true",
    )

    parser.add_argument(
        "--camera-probe-count",
        type=int,
        default=10,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    if args.list_devices:
        list_serial_devices()
        list_audio_devices()
        list_cameras(args.camera_probe_count)
        return

    serial_port = args.serial_port

    if serial_port is None:
        serial_port = find_teensy_port()

    if serial_port is None:
        raise RuntimeError(
            "Could not automatically find the Teensy. "
            "Use --serial-port."
        )

    session = RecordingSession(
        duration_s=args.duration,
        imu_port=serial_port,
        imu_baud=args.baud,
        camera_index=args.camera,
        camera_width=args.width,
        camera_height=args.height,
        camera_fps=args.fps,
        audio_device=args.audio_device,
        audio_sample_rate=args.audio_rate,
        audio_channels=args.audio_channels,
        audio_block_size=args.audio_block_size,
        output_root=args.output_root,
    )

    session.run()


if __name__ == "__main__":
    main()