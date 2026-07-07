import os
import time
import csv
import struct
from datetime import datetime
from smbus2 import SMBus, i2c_msg


def now_ns():
    return int(time.perf_counter() * 1e9)


class IMUFIFORecorder:
    ADDR = 0x6A
    BUS = 1

    WHO_AM_I = 0x0F
    CTRL1_XL = 0x10
    CTRL2_G = 0x11
    CTRL3_C = 0x12
    INT1_CTRL = 0x0D

    FIFO_CTRL1 = 0x07
    FIFO_CTRL2 = 0x08
    FIFO_CTRL3 = 0x09
    FIFO_CTRL4 = 0x0A

    FIFO_STATUS1 = 0x3A
    FIFO_STATUS2 = 0x3B
    FIFO_DATA_OUT_TAG = 0x78

    ACCEL_SCALE = 0.000244  # g/LSB for ±8g
    GYRO_SCALE = 0.07       # dps/LSB for ±2000 dps

    def __init__(self, output_csv, duration_sec=30):
        self.output_csv = output_csv
        self.duration_sec = duration_sec

    def write_reg(self, bus, reg, val):
        bus.write_byte_data(self.ADDR, reg, val)

    def read_reg(self, bus, reg):
        return bus.read_byte_data(self.ADDR, reg)

    def read_fifo_bytes(self, bus, n):
        write = i2c_msg.write(self.ADDR, [self.FIFO_DATA_OUT_TAG])
        read = i2c_msg.read(self.ADDR, n)
        bus.i2c_rdwr(write, read)
        return bytes(read)

    def fifo_level(self, bus):
        s1 = self.read_reg(bus, self.FIFO_STATUS1)
        s2 = self.read_reg(bus, self.FIFO_STATUS2)
        return s1 | ((s2 & 0x03) << 8)

    def configure_sensor(self, bus):
        who = self.read_reg(bus, self.WHO_AM_I)
        print("WHO_AM_I:", hex(who))

        if who != 0x6C:
            raise RuntimeError("Unexpected WHO_AM_I: {}".format(hex(who)))

        # Reset
        self.write_reg(bus, self.CTRL3_C, 0x01)
        time.sleep(0.1)

        # BDU=1, IF_INC=1
        self.write_reg(bus, self.CTRL3_C, 0x44)

        # Accel: ODR 1.666 kHz, ±8g
        self.write_reg(bus, self.CTRL1_XL, 0x8C)

        # Gyro: ODR 1.666 kHz, ±2000 dps
        self.write_reg(bus, self.CTRL2_G, 0x8C)

        # FIFO watermark = 255 samples
        self.write_reg(bus, self.FIFO_CTRL1, 255)
        self.write_reg(bus, self.FIFO_CTRL2, 0x00)

        # Batch accel + gyro into FIFO
        self.write_reg(bus, self.FIFO_CTRL3, 0x88)

        # FIFO continuous mode
        self.write_reg(bus, self.FIFO_CTRL4, 0x06)

        # FIFO threshold interrupt on INT1, optional
        self.write_reg(bus, self.INT1_CTRL, 0x08)

    def run(self, session_t0_ns=None):
        if session_t0_ns is None:
            session_t0_ns = now_ns()

        out_dir = os.path.dirname(self.output_csv)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        with SMBus(self.BUS) as bus:
            self.configure_sensor(bus)
            print("FIFO started")

            start_time = time.perf_counter()
            last_print = start_time
            count = 0

            with open(self.output_csv, "w", newline="", buffering=1024 * 1024) as f:
                writer = csv.writer(f)
                writer.writerow(["t_ns", "t_ms", "sensor", "x", "y", "z", "unit"])

                while time.perf_counter() - start_time < self.duration_sec:
                    time.sleep(0.005)

                    level = self.fifo_level(bus)
                    if level == 0:
                        continue

                    n_samples = min(level, 255)
                    raw = self.read_fifo_bytes(bus, n_samples * 7)

                    t_ns = now_ns()
                    t_ms = (t_ns - session_t0_ns) / 1e6

                    for i in range(0, len(raw), 7):
                        if i + 7 > len(raw):
                            break

                        tag = raw[i] >> 3
                        x, y, z = struct.unpack_from("<hhh", raw, i + 1)

                        if tag == 0x02:
                            writer.writerow([
                                t_ns, t_ms, "accel",
                                x * self.ACCEL_SCALE,
                                y * self.ACCEL_SCALE,
                                z * self.ACCEL_SCALE,
                                "g",
                            ])
                            count += 1
                        '''
                        elif tag == 0x01:
                            writer.writerow([
                                t_ns, t_ms, "gyro",
                                x * self.GYRO_SCALE,
                                y * self.GYRO_SCALE,
                                z * self.GYRO_SCALE,
                                "dps",
                            ])
                            count += 1
                        '''
                    now = time.perf_counter()
                    if now - last_print >= 1.0:
                        print("FIFO samples/sec:", count)
                        count = 0
                        last_print = now

        print("Done recording IMU.")


if __name__ == "__main__":
    DURATION_SEC = 30

    session_name = datetime.now().strftime("session_%Y%m%d_%H%M%S")
    session_dir = os.path.join(os.getcwd(), session_name)
    output_csv = os.path.join(session_dir, "imu_fifo_i2c.csv")

    recorder = IMUFIFORecorder(
        output_csv=output_csv,
        duration_sec=DURATION_SEC,
    )

    session_t0_ns = now_ns()
    recorder.run(session_t0_ns=session_t0_ns)

    print("Saved:", output_csv)