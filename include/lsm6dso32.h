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
    void readRegisters(uint8_t startReg, uint8_t *buffer, size_t len);

    void configureAccel();
    void readAccelRaw(int16_t &ax, int16_t &ay, int16_t &az);

private:
    uint8_t _cs;
};