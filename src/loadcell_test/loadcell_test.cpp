#include <Arduino.h>

#include "hx711_sensor.h"


// =====================================================================
// Configuration
// =====================================================================

static constexpr uint8_t HX711_DATA_PIN = 6;
static constexpr uint8_t HX711_CLOCK_PIN = 7;

static constexpr uint32_t SERIAL_BAUD = 2000000;

// Calculate and update the measured SPS once per second.
static constexpr uint32_t SPS_WINDOW_US = 1000000;


// =====================================================================
// HX711 sensor
// =====================================================================

HX711Sensor loadCell(
    HX711_DATA_PIN,
    HX711_CLOCK_PIN
);


// =====================================================================
// Sampling-rate measurement
// =====================================================================

// Start time of the current SPS measurement window.
static uint32_t spsWindowStartUs = 0;

// Number of HX711 samples acquired during the current window.
static uint32_t samplesInWindow = 0;

// Most recently calculated sampling rate.
static float measuredSps = 0.0f;


// =====================================================================
// Update measured samples per second
// =====================================================================

void updateSps(uint32_t currentTimeUs)
{
    const uint32_t elapsedUs =
        currentTimeUs - spsWindowStartUs;

    if (elapsedUs < SPS_WINDOW_US)
    {
        return;
    }

    measuredSps =
        static_cast<float>(samplesInWindow) *
        1000000.0f /
        static_cast<float>(elapsedUs);

    // Print a human-readable status line once per second.
    //
    // Lines beginning with '#' can easily be ignored by a Python CSV
    // loader by using:
    //
    // pd.read_csv(file_path, comment="#")
    Serial.print("# HX711 SPS: ");
    Serial.println(measuredSps, 2);

    samplesInWindow = 0;
    spsWindowStartUs = currentTimeUs;
}


// =====================================================================
// Setup
// =====================================================================

void setup()
{
    Serial.begin(SERIAL_BAUD);
    delay(1000);

    Serial.println();
    Serial.println("# HX711 load-cell-only acquisition");

    Serial.print("# HX711 data pin: ");
    Serial.println(HX711_DATA_PIN);

    Serial.print("# HX711 clock pin: ");
    Serial.println(HX711_CLOCK_PIN);

    // Initialize the HX711.
    loadCell.begin();

    // Start SPS measurement window after initialization.
    spsWindowStartUs = micros();

    // CSV header.
    Serial.println(
        "timestamp_us,"
        "load_cell_raw,"
        "sps"
    );
}


// =====================================================================
// Main loop
// =====================================================================

void loop()
{
    const uint32_t currentTimeUs = micros();

    // Update the displayed SPS even if the HX711 temporarily stops
    // producing samples.
    //updateSps(currentTimeUs);

    // HX711 DOUT is LOW when a new conversion is ready.
    //
    // Checking the data-ready pin before calling update() allows us to
    // count actual conversions rather than repeatedly counting the
    // cached reading returned by latestRaw().
    if (digitalRead(HX711_DATA_PIN) != LOW)
    {
        return;
    }

    // Read the available HX711 conversion.
    loadCell.update();

    if (!loadCell.hasReading())
    {
        return;
    }

    const uint32_t sampleTimestampUs = micros();
    const int32_t rawValue = loadCell.latestRaw();

    //samplesInWindow++;

    // CSV data row.
    Serial.println(sampleTimestampUs);
    Serial.print(',');

    Serial.print(rawValue);
    Serial.print(',');

    //Serial.println(measuredSps, 2);
}