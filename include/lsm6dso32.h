#pragma once

#include <Arduino.h>
#include <SPI.h>

class LSM6DSO32
{
public:
    explicit LSM6DSO32(uint8_t csPin);

    bool begin();

    uint8_t readRegister(uint8_t reg);
    void writeRegister(uint8_t reg, uint8_t value);

private:
    uint8_t _cs;
};