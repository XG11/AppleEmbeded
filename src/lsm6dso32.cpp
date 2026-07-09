#include "lsm6dso32.h"

static constexpr uint32_t SPI_SPEED = 100000;

// Registers
static constexpr uint8_t WHO_AM_I = 0x0F;

static constexpr uint8_t CTRL1_XL = 0x10;
static constexpr uint8_t CTRL2_G  = 0x11;
static constexpr uint8_t CTRL3_C  = 0x12;

static constexpr uint8_t OUTX_L_G = 0x22;
static constexpr uint8_t OUTX_L_A = 0x28;

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

void LSM6DSO32::configureAccelGyro()
{
    // CTRL3_C:
    // BDU = 1, IF_INC = 1
    writeRegister(CTRL3_C, 0x44);

    // CTRL1_XL:
    // ODR_XL = 6.66 kHz
    // FS_XL = ±32 g
    writeRegister(CTRL1_XL, 0xA8);

    // CTRL2_G:
    // ODR_G = 6.66 kHz
    // FS_G = 2000 dps
    writeRegister(CTRL2_G, 0xAC);

    delay(50);
}

void LSM6DSO32::readAccelRaw(int16_t &ax, int16_t &ay, int16_t &az)
{
    uint8_t data[6];

    readRegisters(OUTX_L_A, data, 6);

    ax = (int16_t)((data[1] << 8) | data[0]);
    ay = (int16_t)((data[3] << 8) | data[2]);
    az = (int16_t)((data[5] << 8) | data[4]);
}

void LSM6DSO32::readGyroRaw(int16_t &gx, int16_t &gy, int16_t &gz)
{
    uint8_t data[6];

    readRegisters(OUTX_L_G, data, 6);

    gx = (int16_t)((data[1] << 8) | data[0]);
    gy = (int16_t)((data[3] << 8) | data[2]);
    gz = (int16_t)((data[5] << 8) | data[4]);
};