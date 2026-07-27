#include "hx711_sensor.h"


HX711Sensor::HX711Sensor(
    uint8_t dataPin,
    uint8_t clockPin
)
    : _dataPin(dataPin),
      _clockPin(clockPin),
      _latestRaw(0),
      _hasReading(false)
{
}


void HX711Sensor::begin()
{
    pinMode(_dataPin, INPUT);
    pinMode(_clockPin, OUTPUT);

    // Keep PD_SCK low during normal operation.
    digitalWrite(_clockPin, LOW);
}


bool HX711Sensor::isReady() const
{
    // HX711 DOUT is LOW when a conversion is ready.
    return digitalRead(_dataPin) == LOW;
}


int32_t HX711Sensor::readRaw()
{
    // This function assumes isReady() was already checked.
    //
    // HX711 output:
    //   24 data bits, MSB first
    //
    // One additional clock pulse selects:
    //   Channel A, gain 128
    uint32_t rawValue = 0;

    // Keep the complete transfer short and prevent another interrupt
    // from holding PD_SCK high long enough to power down the HX711.
    noInterrupts();

    for (uint8_t bitIndex = 0; bitIndex < 24; bitIndex++)
    {
        digitalWrite(_clockPin, HIGH);
        delayMicroseconds(1);

        rawValue = (rawValue << 1)
            | static_cast<uint32_t>(
                digitalRead(_dataPin)
            );

        digitalWrite(_clockPin, LOW);
        delayMicroseconds(1);
    }

    // 25th pulse:
    // select channel A with gain 128 for the next conversion.
    digitalWrite(_clockPin, HIGH);
    delayMicroseconds(1);
    digitalWrite(_clockPin, LOW);
    delayMicroseconds(1);

    interrupts();

    // Sign-extend the 24-bit two's-complement result to int32_t.
    if (rawValue & 0x00800000UL)
    {
        rawValue |= 0xFF000000UL;
    }

    return static_cast<int32_t>(rawValue);
}


bool HX711Sensor::update()
{
    if (!isReady())
    {
        return false;
    }

    _latestRaw = readRaw();
    _hasReading = true;

    return true;
}


int32_t HX711Sensor::latestRaw() const
{
    return _latestRaw;
}


bool HX711Sensor::hasReading() const
{
    return _hasReading;
}