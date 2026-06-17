import time
import csv
import board
import busio

from adafruit_lsm6ds.lsm6dso32 import LSM6DSO32
from adafruit_lsm6ds import Rate, AccelRange, GyroRange

i2c = busio.I2C(board.SCL, board.SDA)
imu = LSM6DSO32(i2c)

imu.accelerometer_data_rate = Rate.RATE_1_66K_HZ
imu.gyro_data_rate = Rate.RATE_1_66K_HZ

imu.accelerometer_range = AccelRange.RANGE_32G
imu.gyro_range = GyroRange.RANGE_2000_DPS

filename = "imu_highspeed_i2c.csv"

count = 0
t0 = time.perf_counter()

with open(filename, "w", newline="", buffering=1024 * 1024) as f:
    writer = csv.writer(f)
    writer.writerow(["t_ns", "ax", "ay", "az", "gx", "gy", "gz"])

    while True:
        t_ns = time.perf_counter_ns()
        ax, ay, az = imu.acceleration
        gx, gy, gz = imu.gyro

        writer.writerow([t_ns, ax, ay, az, gx, gy, gz])
        count += 1

        now = time.perf_counter()
        if now - t0 >= 1.0:
            print("logged rate:", count, "Hz")
            count = 0
            t0 = now