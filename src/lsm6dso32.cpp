#include "lsm6dso32.h"

static constexpr uint32_t SPI_SPEED = 1000000;

// Registers
static constexpr uint8_t WHO_AM_I = 0x0F;

static constexpr uint8_t FIFO_CTRL1 = 0x07;
static constexpr uint8_t FIFO_CTRL2 = 0x08;
static constexpr uint8_t FIFO_CTRL3 = 0x09;
static constexpr uint8_t FIFO_CTRL4 = 0x0A;

static constexpr uint8_t CTRL1_XL = 0x10;
static constexpr uint8_t CTRL2_G  = 0x11;
static constexpr uint8_t CTRL3_C  = 0x12;

static constexpr uint8_t FIFO_STATUS1 = 0x3A;
static constexpr uint8_t FIFO_STATUS2 = 0x3B;

static constexpr uint8_t FIFO_DATA_OUT_TAG = 0x78;

LSM6DSO32::LSM6DSO32(uint8_t csPin)
{
    _cs = csPin;
}

bool LSM6DSO32::begin()
{
    pinMode(_cs, OUTPUT);
    digitalWrite(_cs, HIGH);

    SPI.begin();
    delay(20);

    uint8_t who = readRegister(WHO_AM_I);

    Serial.print("WHO_AM_I = 0x");
    if (who < 0x10) Serial.print("0");
    Serial.println(who, HEX);

    return who == 0x6C;
}

uint8_t LSM6DSO32::readRegister(uint8_t reg)
{
    SPI.beginTransaction(SPISettings(SPI_SPEED, MSBFIRST, SPI_MODE0));

    digitalWrite(_cs, LOW);
    SPI.transfer(reg | 0x80);
    uint8_t value = SPI.transfer(0x00);
    digitalWrite(_cs, HIGH);

    SPI.endTransaction();
    return value;
}

void LSM6DSO32::writeRegister(uint8_t reg, uint8_t value)
{
    SPI.beginTransaction(SPISettings(SPI_SPEED, MSBFIRST, SPI_MODE0));

    digitalWrite(_cs, LOW);
    SPI.transfer(reg & 0x7F);
    SPI.transfer(value);
    digitalWrite(_cs, HIGH);

    SPI.endTransaction();
}

void LSM6DSO32::readRegisters(uint8_t startReg, uint8_t *buffer, size_t len)
{
    SPI.beginTransaction(SPISettings(SPI_SPEED, MSBFIRST, SPI_MODE0));

    digitalWrite(_cs, LOW);
    SPI.transfer(startReg | 0x80);

    for (size_t i = 0; i < len; i++)
    {
        buffer[i] = SPI.transfer(0x00);
    }

    digitalWrite(_cs, HIGH);

    SPI.endTransaction();
}

void LSM6DSO32::configureFifoAccelGyro()
{
    // Reset FIFO first: bypass mode
    writeRegister(FIFO_CTRL4, 0x00);
    delay(10);

    // CTRL3_C:
    // BDU = 1, IF_INC = 1
    writeRegister(CTRL3_C, 0x44);

    // Debug ODR:
    // 0x60 = 416 Hz accel, FS = ±32g
    // 0x6C = 416 Hz gyro,  FS = 2000 dps
    //
    // Later for 6.66 kHz:
    // CTRL1_XL = 0xA8
    // CTRL2_G  = 0xAC
    writeRegister(CTRL1_XL, 0x68);
    writeRegister(CTRL2_G,  0x6C);

    // FIFO watermark low/high.
    // For now, not using interrupt watermark, so set low value.
    writeRegister(FIFO_CTRL1, 0x20);
    writeRegister(FIFO_CTRL2, 0x00);

    // FIFO_CTRL3:
    // BDR_G  = 416 Hz
    // BDR_XL = 416 Hz
    //
    // 0x66 = gyro FIFO batch rate 416 Hz + accel FIFO batch rate 416 Hz
    writeRegister(FIFO_CTRL3, 0x66);

    // FIFO_CTRL4:
    // continuous FIFO mode
    writeRegister(FIFO_CTRL4, 0x06);

    delay(50);
}

uint16_t LSM6DSO32::fifoCount()
{
    uint8_t st1 = readRegister(FIFO_STATUS1);
    uint8_t st2 = readRegister(FIFO_STATUS2);

    return (uint16_t)(st1 | ((st2 & 0x03) << 8));
}

bool LSM6DSO32::readFifoSample(uint8_t &tag, int16_t &x, int16_t &y, int16_t &z)
{
    uint8_t data[7];

    readRegisters(FIFO_DATA_OUT_TAG, data, 7);

    // FIFO tag is usually in upper bits
    tag = (data[0] >> 3) & 0x1F;

    x = (int16_t)((data[2] << 8) | data[1]);
    y = (int16_t)((data[4] << 8) | data[3]);
    z = (int16_t)((data[6] << 8) | data[5]);

    return true;
}