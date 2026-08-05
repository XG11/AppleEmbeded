#include <Arduino.h>
#include <SPI.h>
#include "ADS1220.h"

#define CS_PIN    9
#define DRDY_PIN  8

ADS1220 adc(CS_PIN, DRDY_PIN);

// Calibration factor: (Vref / gain) / (2^23) — tune to your load cell
const float LOAD_CELL_CAL = 1.0f;

void setup() {
    Serial.begin(115200);
    while (!Serial) {}
    Serial.println("Teensy booted");

    SPI.begin();
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE1));

    Serial.println("Initializing ADS1220...");

    adc.begin();
    adc.reset();

    Serial.println("ADS1220 reset done");

    adc.writeRegister(0x00, 0x2E);
    adc.writeRegister(0x01, 0xD4);


    Serial.println("Registers written");
    Serial.print("Reg0 readback (should be 60): 0x");
    Serial.println(adc.readRegister(0x00), HEX);
    Serial.print("Reg1 readback (should be D4): 0x");
    Serial.println(adc.readRegister(0x01), HEX);

    adc.startConversion();

    Serial.println("Conversion started, waiting for DRDY...");

    // Zero the offset with 100 samples --> sets the initial position as zero.
    //adc.findADCOffset(100);

    Serial.println("SETUP COMPLETE");

    delay(5000);
}

void loop() {

    //    Wait for DRDY to go LOW (conversion ready)
    while (digitalRead(DRDY_PIN)) {}
    delayMicroseconds(10);

    // Calibrated Values (?) not sure what they are calibrated to.
    //float value = adc.readDataCalibrated(1.0f);
    // Serial.println(value);
    //Serial.print("time: "); // printing values to plot on loadcell_liveplot.py
    //Serial.print(millis() / 1000.0, 3);
    //Serial.print(" modified_weight: ");
    //Serial.println(value);

    // Raw Values
    int32_t raw = adc.readData();
    Serial.println(raw);

}

