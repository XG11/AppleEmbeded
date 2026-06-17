import time
import csv
import struct
from smbus2 import SMBus, i2c_msg
from gpiozero import Button


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

    ACCEL_SCALE = 0.000976 / 2  # g/LSB for your current setup

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

    def run(self, session_t0_ns):

        fifo_int = Button(17, pull_up = False)

        with SMBus(self.BUS) as bus:
            who = self.read_reg(bus, self.WHO_AM_I)
            print("WHO_AM_I:", hex(who))

            self.write_reg(bus, self.CTRL3_C, 0x01)
            time.sleep(0.1)

            self.write_reg(bus, self.CTRL3_C, 0x44)

            self.write_reg(bus, self.CTRL1_XL, 0xAC)
            self.write_reg(bus, self.CTRL2_G, 0x00)

            self.write_reg(bus, self.FIFO_CTRL1, 128)
            self.write_reg(bus, self.FIFO_CTRL2, 0x00)

            self.write_reg(bus, self.FIFO_CTRL3, 0x0A)
            self.write_reg(bus, self.FIFO_CTRL4, 0x06)

            self.write_reg(bus, self.INT1_CTRL, 0X08)

            print("FIFO started")

            start_time = time.perf_counter()
            last_print = start_time
            count = 0

            with open(self.output_csv, "w", newline="", buffering=1024 * 1024) as f:
                writer = csv.writer(f)
                writer.writerow(["t_ns", "t_ms", "sensor", "x_g", "y_g", "z_g"])

                while time.perf_counter() - start_time < self.duration_sec:

                    fifo_int.wait_for_press(timeout=0.1)
                    #print("INT!")

                    level = self.fifo_level(bus)

                    if level == 0:
                        continue

                    n_samples = min(level, 128)
                    raw = self.read_fifo_bytes(bus, n_samples * 7)

                    t_ns = time.perf_counter_ns()
                    t_ms = (t_ns - session_t0_ns) / 1e6

                    for i in range(0, len(raw), 7):
                        tag = raw[i] >> 3
                        x, y, z = struct.unpack_from("<hhh", raw, i + 1)

                        if tag == 0x02:
                            writer.writerow([
                                t_ns,
                                t_ms,
                                "accel",
                                x * self.ACCEL_SCALE,
                                y * self.ACCEL_SCALE,
                                z * self.ACCEL_SCALE,
                            ])
                            count += 1

                    now = time.perf_counter()
                    if now - last_print >= 1.0:
                        print("FIFO samples/sec:", count)
                        count = 0
                        last_print = now

            print("Done recording IMU.")