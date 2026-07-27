#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path
from typing import List

import cv2
import sounddevice as sd
from serial.tools import list_ports

from recorder import RecordingSession


# ---------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------

def find_teensy_ports() -> List[str]:
    """
    Find serial ports that appear to belong to Teensy boards.
    """
    ports: List[str] = []

    for port in list_ports.comports():
        description = (port.description or "").lower()
        manufacturer = (port.manufacturer or "").lower()

        if (
            "teensy" in description
            or "teensy" in manufacturer
            or port.vid == 0x16C0
        ):
            ports.append(port.device)

    return ports


def list_serial_devices() -> None:
    print("\nSerial devices:")

    serial_ports = list(list_ports.comports())

    if not serial_ports:
        print("  No serial devices found.")
        return

    for port in serial_ports:
        print(
            f"  {port.device}: "
            f"{port.description}, "
            f"manufacturer={port.manufacturer}, "
            f"VID={port.vid}, PID={port.pid}"
        )


def list_audio_devices() -> None:
    print("\nAudio devices:")
    print(sd.query_devices())


def open_camera(index: int):
    """
    Open an OpenCV camera.

    On macOS, try AVFoundation explicitly first.
    """
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


# ---------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Record a synchronized multimodal session containing "
            "LSM6DSO32 IMU data, HX711 load-cell data, piezo data, "
            "GelSight video, and USB microphone audio."
        )
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Recording duration in seconds. Default: 30.",
    )

    # -----------------------------------------------------------------
    # Combined IMU + HX711 Teensy
    # -----------------------------------------------------------------

    parser.add_argument(
        "--imu-port",
        type=str,
        default=None,
        help=(
            "Serial port for the Teensy that records the "
            "LSM6DSO32 IMU and HX711 load cell."
        ),
    )

    parser.add_argument(
        "--imu-baud",
        type=int,
        default=2_000_000,
        help=(
            "Baud rate for the combined IMU/HX711 Teensy. "
            "Default: 2000000."
        ),
    )

    # -----------------------------------------------------------------
    # Piezo Teensy
    # -----------------------------------------------------------------

    parser.add_argument(
        "--piezo-port",
        type=str,
        default=None,
        help="Serial port for the piezo Teensy.",
    )

    parser.add_argument(
        "--piezo-baud",
        type=int,
        default=2_000_000,
        help=(
            "Baud rate for the piezo Teensy. "
            "Default: 2000000."
        ),
    )

    # -----------------------------------------------------------------
    # GelSight
    # -----------------------------------------------------------------

    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="OpenCV camera index for the GelSight camera.",
    )

    parser.add_argument(
        "--width",
        type=int,
        default=0,
        help=(
            "Requested camera width. "
            "Use 0 to leave the camera default unchanged."
        ),
    )

    parser.add_argument(
        "--height",
        type=int,
        default=0,
        help=(
            "Requested camera height. "
            "Use 0 to leave the camera default unchanged."
        ),
    )

    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Requested GelSight frame rate.",
    )

    # -----------------------------------------------------------------
    # RØDE microphone
    # -----------------------------------------------------------------

    parser.add_argument(
        "--audio-device",
        type=int,
        default=None,
        help="sounddevice input-device index for the USB microphone.",
    )

    parser.add_argument(
        "--audio-rate",
        type=int,
        default=48_000,
        help="Audio sample rate in samples per second.",
    )

    parser.add_argument(
        "--audio-channels",
        type=int,
        default=1,
        help="Number of recorded microphone channels.",
    )

    parser.add_argument(
        "--audio-block-size",
        type=int,
        default=1024,
        help="Audio callback block size.",
    )

    # -----------------------------------------------------------------
    # Output and device listing
    # -----------------------------------------------------------------

    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("recordings"),
        help="Root directory for recording sessions.",
    )

    parser.add_argument(
        "--list-devices",
        action="store_true",
        help=(
            "List serial ports, audio devices, and cameras, "
            "then exit."
        ),
    )

    parser.add_argument(
        "--camera-probe-count",
        type=int,
        default=10,
        help=(
            "Number of OpenCV camera indices to test when "
            "using --list-devices."
        ),
    )

    return parser.parse_args()


# ---------------------------------------------------------------------
# Port selection
# ---------------------------------------------------------------------

def resolve_teensy_ports(
    imu_port: str | None,
    piezo_port: str | None,
) -> tuple[str, str]:
    """
    Resolve the two Teensy serial ports.

    Explicit command-line ports are preferred. Automatic assignment is
    only performed when both ports are omitted and exactly two Teensy
    devices are detected.

    Because USB enumeration order is not guaranteed, explicit port
    arguments are recommended.
    """
    if imu_port is not None and piezo_port is not None:
        if imu_port == piezo_port:
            raise ValueError(
                "The IMU/HX711 and piezo recorders cannot use "
                "the same serial port."
            )

        return imu_port, piezo_port

    teensy_ports = find_teensy_ports()

    if imu_port is None and piezo_port is None:
        if len(teensy_ports) != 2:
            raise RuntimeError(
                f"Expected exactly 2 Teensy devices, "
                f"but found {len(teensy_ports)}.\n"
                f"Detected Teensy ports: {teensy_ports}\n"
                "Specify the ports explicitly using:\n"
                "  --imu-port PORT --piezo-port PORT"
            )

        print(
            "\nWarning: assigning Teensy ports according to USB "
            "enumeration order."
        )
        print(f"  IMU + HX711: {teensy_ports[0]}")
        print(f"  Piezo:       {teensy_ports[1]}")
        print(
            "Use explicit --imu-port and --piezo-port arguments "
            "if these assignments are incorrect."
        )

        return teensy_ports[0], teensy_ports[1]

    # Resolve one missing port while preserving the explicitly provided
    # one.
    used_port = (
        imu_port
        if imu_port is not None
        else piezo_port
    )

    candidates = [
        port
        for port in teensy_ports
        if port != used_port
    ]

    if len(candidates) != 1:
        missing_name = (
            "--imu-port"
            if imu_port is None
            else "--piezo-port"
        )

        raise RuntimeError(
            f"Could not uniquely determine {missing_name}.\n"
            f"Detected Teensy ports: {teensy_ports}\n"
            "Specify both ports explicitly."
        )

    if imu_port is None:
        imu_port = candidates[0]
    else:
        piezo_port = candidates[0]

    return imu_port, piezo_port


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> None:
    args = parse_arguments()

    if args.list_devices:
        list_serial_devices()
        list_audio_devices()
        list_cameras(args.camera_probe_count)
        return

    if args.duration <= 0:
        raise ValueError("--duration must be greater than zero.")

    if args.imu_baud <= 0:
        raise ValueError("--imu-baud must be greater than zero.")

    if args.piezo_baud <= 0:
        raise ValueError("--piezo-baud must be greater than zero.")

    imu_port, piezo_port = resolve_teensy_ports(
        imu_port=args.imu_port,
        piezo_port=args.piezo_port,
    )

    print("\nSelected devices")
    print(f"  IMU + HX711 Teensy: {imu_port}")
    print(f"  Piezo Teensy:       {piezo_port}")
    print(f"  GelSight camera:    {args.camera}")
    print(f"  Audio device:       {args.audio_device}")

    session = RecordingSession(
        duration_s=args.duration,

        # Combined LSM6DSO32 + HX711 stream.
        imu_port=imu_port,
        imu_baud=args.imu_baud,

        # Separate piezo Teensy.
        piezo_port=piezo_port,
        piezo_baud=args.piezo_baud,

        # GelSight.
        camera_index=args.camera,
        camera_width=args.width,
        camera_height=args.height,
        camera_fps=args.fps,

        # RØDE USB microphone.
        rode_audio_device=args.audio_device,
        rode_sample_rate=args.audio_rate,
        rode_channels=args.audio_channels,
        rode_block_size=args.audio_block_size,

        output_root=args.output_root,
    )

    session.run()


if __name__ == "__main__":
    main()