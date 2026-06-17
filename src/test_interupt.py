import time
from smbus2 import SMBus
from gpiozero import Button

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

INT1_GPIO = 17

def write_reg(bus, reg, val):
    bus.write_byte_data(ADDR, reg, val)

def read_reg(bus, reg):
    return bus.read_byte_data(ADDR, reg)

def fifo_level(bus):
    s1 = read_reg(bus, FIFO_STATUS1)
    s2 = read_reg(bus, FIFO_STATUS2)
    return s1 | ((s2 & 0x03) << 8)

gpio = Button(INT1_GPIO, pull_up=False)

with SMBus(BUS) as bus:
    print("WHO_AM_I:", hex(read_reg(bus, WHO_AM_I)))

    write_reg(bus, CTRL3_C, 0x01)
    time.sleep(0.1)

    # BDU + auto-increment
    write_reg(bus, CTRL3_C, 0x44)

    # accel 6.66 kHz, gyro off
    write_reg(bus, CTRL1_XL, 0xAC)
    write_reg(bus, CTRL2_G, 0x00)

    # watermark = 128
    write_reg(bus, FIFO_CTRL1, 128)
    write_reg(bus, FIFO_CTRL2, 0x00)

    # accel batch rate 6.66 kHz
    write_reg(bus, FIFO_CTRL3, 0x0A)

    # route FIFO watermark to INT1
    write_reg(bus, INT1_CTRL, 0x08)

    # continuous FIFO mode
    write_reg(bus, FIFO_CTRL4, 0x06)

    print("INT1_CTRL readback:", hex(read_reg(bus, INT1_CTRL)))
    print("FIFO_CTRL4 readback:", hex(read_reg(bus, FIFO_CTRL4)))
    print("Watching GPIO17...")

    while True:
        level = fifo_level(bus)
        print("gpio:", gpio.is_pressed, "fifo_level:", level)

        if gpio.is_pressed:
            print("INT1 HIGH")
            break

        time.sleep(0.02)