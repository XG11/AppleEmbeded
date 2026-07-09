#include <Arduino.h>
#include <SPI.h>

void setup()
{
    Serial.begin(115200);
    while (!Serial) {}

    SPI.begin();

    Serial.println("SPI Loopback Test");
}

void loop()
{
    SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));

    uint8_t tx[] = {
        0x01,
        0x23,
        0x45,
        0x67,
        0x89,
        0xAB,
        0xCD,
        0xEF
    };

    uint8_t rx[sizeof(tx)];

    for (size_t i = 0; i < sizeof(tx); i++)
    {
        rx[i] = SPI.transfer(tx[i]);
    }

    SPI.endTransaction();

    Serial.print("TX: ");
    for (uint8_t b : tx)
    {
        Serial.printf("%02X ", b);
    }

    Serial.println();

    Serial.print("RX: ");
    for (uint8_t b : rx)
    {
        Serial.printf("%02X ", b);
    }

    Serial.println();

    bool pass = true;
    for (size_t i = 0; i < sizeof(tx); i++)
    {
        if (tx[i] != rx[i])
        {
            pass = false;
            break;
        }
    }

    Serial.println(pass ? "PASS\n" : "FAIL\n");

    delay(1000);
}