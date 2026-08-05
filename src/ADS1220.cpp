#include "ADS1220.h"

ADS1220::ADS1220(int cs_pin, int drdy_pin) {
    _cs_pin = cs_pin;
    _drdy_pin = drdy_pin;
}

void ADS1220::begin() {
    pinMode(_cs_pin, OUTPUT);
    pinMode(_drdy_pin, INPUT);
    digitalWrite(_cs_pin, HIGH);
}

void ADS1220::reset() {
    spi_command(RESET); // RESET command
    delay(2); // ADS1220 requires ~1ms after RESET before any SPI command
}

void ADS1220::writeRegister(uint8_t address, uint8_t value) {
    digitalWrite(_cs_pin, LOW);
    SPI.transfer(WREG | (address << 2));
    SPI.transfer(value);
    digitalWrite(_cs_pin, HIGH);
}

uint8_t ADS1220::readRegister(uint8_t address) {
    digitalWrite(_cs_pin, LOW);
    SPI.transfer(RREG | (address << 2));
    uint8_t value = SPI.transfer(0x00);
    digitalWrite(_cs_pin, HIGH);
    return value;
}

int32_t ADS1220::readData() {

    uint8_t b0, b1, b2;

    digitalWrite(_cs_pin, LOW);
    //SPI.transfer(RDATA);
    b0 = SPI.transfer(0x00);
    b1 = SPI.transfer(0x00);
    b2 = SPI.transfer(0x00);
    digitalWrite(_cs_pin, HIGH);

    int32_t adc = (int32_t)b0 << 16 | (int32_t)b1 << 8 | (int32_t)b2;

    // Sign-extend 24-bit to 32-bit
    if (adc & 0x800000) {
        adc |= 0xFF000000;
    }

    return adc;
}

float ADS1220::readDataCalibrated(float loadcell_cal) {
    return (readData() - _lc_offset) * loadcell_cal;
}

void ADS1220::startConversion() {
    spi_command(START);
}

void ADS1220::powerDown() {
    spi_command(PDWN);
}

void ADS1220::findADCOffset(int32_t num_samples) {

  long cal_sum = 0;

  delay(300);

  for (int i = 0; i < num_samples; i++) {
    while (digitalRead(_drdy_pin)) {
      delayMicroseconds(1);
    }
    cal_sum += readData();
  }
  _lc_offset = (int32_t) (1.0f * cal_sum / num_samples);

  Serial.println("Finished Find ADCOFFSET ");
}


void ADS1220::spi_command(uint8_t command) {
    digitalWrite(_cs_pin, LOW);
    SPI.transfer(command);
    digitalWrite(_cs_pin, HIGH);
}
