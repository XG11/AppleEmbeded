import time
import board
import busio
from adafruit_lsm6ds.lsm6dso32 import LSM6DSO32

i2c = busio.I2C(board.SCL, board.SDA)
imu = LSM6DSO32(i2c)

while True:
    print("Accel:", imu.acceleration)
    print("Gyro :", imu.gyro)
    time.sleep(0.1)