#pragma once

#include <Arduino.h>


class HX711Sensor
{
public:
    HX711Sensor(
        uint8_t dataPin,
        uint8_t clockPin
    );

    /**
     * Initialize the HX711 pins.
     */
    void begin();

    /**
     * Check whether the HX711 has completed a conversion.
     *
     * DOUT goes LOW when a new sample is ready.
     */
    bool isReady() const;

    /**
     * Read one signed 24-bit HX711 sample.
     *
     * This should only be called after isReady() returns true.
     */
    int32_t readRaw();

    /**
     * Read a new value when available.
     *
     * Returns true when latestRaw() was updated.
     * Returns false when no new conversion was available.
     */
    bool update();

    /**
     * Return the most recently acquired raw value.
     */
    int32_t latestRaw() const;

    /**
     * Return true after at least one valid HX711 reading has been acquired.
     */
    bool hasReading() const;

private:
    uint8_t _dataPin;
    uint8_t _clockPin;

    int32_t _latestRaw;
    bool _hasReading;
};