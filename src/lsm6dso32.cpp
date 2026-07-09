#include "lsm6dso32.h"

static constexpr uint32_t SPI_SPEED = 1000000;

LSM6DSO32::LSM6DSO32(uint8_t csPin)
{
    _cs = csPin;
}

uint8_t LSM6DSO32::readRegister(uint8_t reg)
{
    SPI.beginTransaction(SPISettings(SPI_SPEED, MSBFIRST, SPI_MODE3));

    digitalWrite(_cs, LOW);

    SPI.transfer(reg | 0x80);
    uint8_t value = SPI.transfer(0x00);

    digitalWrite(_cs, HIGH);

    SPI.endTransaction();

    return value;
}

void LSM6DSO32::writeRegister(uint8_t reg, uint8_t value)
{
    SPI.beginTransaction(SPISettings(SPI_SPEED, MSBFIRST, SPI_MODE3));

    digitalWrite(_cs, LOW);

    SPI.transfer(reg & 0x7F);
    SPI.transfer(value);

    digitalWrite(_cs, HIGH);

    SPI.endTransaction();
}

bool LSM6DSO32::begin()
{
    pinMode(_cs, OUTPUT);
    digitalWrite(_cs, HIGH);

    SPI.begin();

    delay(20);

    uint8_t who = readRegister(0x0F);

    return who == 0x6C;
}