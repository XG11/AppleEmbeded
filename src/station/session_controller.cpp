#include <Arduino.h>

// ============================================================
// PIN CONFIGURATION
// ============================================================

// LEDs
const int GREEN_LED_PIN  = 10;
const int YELLOW_LED_PIN = 11;
const int RED_LED_PIN    = 12;

// Push button
// Wiring:
// Pin 6 ---- button ---- 3.3 V
const int BUTTON_PIN = 6;

// Connector sensing pins
const int CONNECTOR_PIN_1 = 14;
const int CONNECTOR_PIN_2 = 15;
const int CONNECTOR_PIN_3 = 16;
const int CONNECTOR_PIN_4 = 17;


// ============================================================
// TIMING
// ============================================================

// Hold button this long to start
const unsigned long LONG_PRESS_MS = 1500;

// Entire trial
const unsigned long SESSION_DURATION_MS = 65000;

// Connector must remain fully connected for this long
// before an insertion is considered successful.
const unsigned long SUCCESS_CONFIRM_MS = 200;

// LED blink timing
const unsigned long IDLE_GREEN_BLINK_MS = 500;
const unsigned long RECORDING_YELLOW_BLINK_MS = 500;

// Recording LED phase boundaries
const unsigned long GREEN_SOLID_END_MS = 55000;
const unsigned long YELLOW_BLINK_END_MS = 60000;


// ============================================================
// SUCCESS EVENT STORAGE
// ============================================================

// Maximum number of successful insertions that can be stored
// during one 65-second session.
const int MAX_SUCCESS_EVENTS = 50;


// ============================================================
// FSM
// ============================================================

enum State
{
    IDLE,
    RECORDING
};

State state = IDLE;


// ============================================================
// SESSION VARIABLES
// ============================================================

unsigned long sessionStartMs = 0;


// ------------------------------------------------------------
// FIRST FULL CONNECT
// ------------------------------------------------------------

// Keeps old behavior for compatibility:
// first instant all four contacts become connected.

bool firstFullConnectDetected = false;
unsigned long firstFullConnectTimeMs = 0;


// ------------------------------------------------------------
// MULTIPLE SUCCESSFUL INSERTIONS
// ------------------------------------------------------------

// Number of confirmed insertion events this session.
int successCount = 0;

// Timestamp of each successful insertion.
// IMPORTANT:
// This stores the START of the stable connection,
// not the time 200 ms later when it becomes confirmed.
unsigned long successTimesMs[MAX_SUCCESS_EVENTS];

// Confirmation timestamps are also saved in case they are useful.
unsigned long successConfirmTimesMs[MAX_SUCCESS_EVENTS];


// ------------------------------------------------------------
// CURRENT CONNECTION EVENT STATE
// ------------------------------------------------------------

// True while timing a possible successful insertion.
bool connectionTimerActive = false;

// Absolute millis() when current full connection started.
unsigned long allConnectedStartMs = 0;

// Session-relative time when current full connection started.
unsigned long currentInsertionStartMs = 0;

// Prevents one continuous connection from being counted repeatedly.
//
// Once a success is detected, this becomes true.
// It is reset only after the connector disconnects.
bool currentConnectionAlreadyCounted = false;


// ============================================================
// LAST TRIAL RESULT
// ============================================================

// These remain available after the trial finishes so Python can
// query them after record_multimodal.py releases the serial port.

bool lastTrialValid = false;

bool lastTrialSuccess = false;

bool lastFirstFullConnectDetected = false;
unsigned long lastFirstFullConnectTimeMs = 0;

int lastSuccessCount = 0;

unsigned long lastSuccessTimesMs[MAX_SUCCESS_EVENTS];
unsigned long lastSuccessConfirmTimesMs[MAX_SUCCESS_EVENTS];


// ============================================================
// BUTTON VARIABLES
// ============================================================

bool previousButtonState = false;
bool longPressTriggered = false;
unsigned long buttonPressStartMs = 0;


// ============================================================
// CONNECTOR STATE LOGGING
// ============================================================

int lastP1 = -1;
int lastP2 = -1;
int lastP3 = -1;
int lastP4 = -1;


// ============================================================
// LED FUNCTIONS
// ============================================================

void LEDsOff()
{
    digitalWrite(GREEN_LED_PIN, LOW);
    digitalWrite(YELLOW_LED_PIN, LOW);
    digitalWrite(RED_LED_PIN, LOW);
}


void setLEDs(bool green, bool yellow, bool red)
{
    digitalWrite(GREEN_LED_PIN, green ? HIGH : LOW);
    digitalWrite(YELLOW_LED_PIN, yellow ? HIGH : LOW);
    digitalWrite(RED_LED_PIN, red ? HIGH : LOW);
}


void updateIdleLEDs()
{
    // Green LED flashes while waiting for a trial.
    // Yellow and red stay off.

    bool greenOn =
        ((millis() / IDLE_GREEN_BLINK_MS) % 2) == 0;

    setLEDs(greenOn, false, false);
}


void updateRecordingLEDs()
{
    unsigned long elapsed =
        millis() - sessionStartMs;

    // 0 - 55 s: green solid
    if (elapsed < GREEN_SOLID_END_MS)
    {
        setLEDs(true, false, false);
    }

    // 55 - 60 s: yellow flashing
    else if (elapsed < YELLOW_BLINK_END_MS)
    {
        bool yellowOn =
            ((elapsed / RECORDING_YELLOW_BLINK_MS) % 2) == 0;

        setLEDs(false, yellowOn, false);
    }

    // 60 - 65 s: red solid
    else
    {
        setLEDs(false, false, true);
    }
}


// ============================================================
// CONNECTOR FUNCTIONS
// ============================================================

// Connector wiring:
//
// OPEN:
//      approximately 3 V
//
// CONNECTED:
//      0 V
//
// Therefore:
//
// voltage below threshold = connected
//
// External circuit already drives the voltage, so use INPUT.

const int CONNECTED_THRESHOLD = 220;


bool connectorPinConnected(int pin)
{
    int value = analogRead(pin);

    return value < CONNECTED_THRESHOLD;
}


bool allConnectorPinsConnected()
{
    return (
        connectorPinConnected(CONNECTOR_PIN_1) &&
        connectorPinConnected(CONNECTOR_PIN_2) &&
        connectorPinConnected(CONNECTOR_PIN_3) &&
        connectorPinConnected(CONNECTOR_PIN_4)
    );
}


// ============================================================
// CONNECTOR LOGGING
// ============================================================

void updateConnectorLogging()
{
    int p1 = connectorPinConnected(CONNECTOR_PIN_1);
    int p2 = connectorPinConnected(CONNECTOR_PIN_2);
    int p3 = connectorPinConnected(CONNECTOR_PIN_3);
    int p4 = connectorPinConnected(CONNECTOR_PIN_4);

    // Only send a serial line when connector state changes.
    if (
        p1 != lastP1 ||
        p2 != lastP2 ||
        p3 != lastP3 ||
        p4 != lastP4
    )
    {
        unsigned long elapsed =
            millis() - sessionStartMs;

        Serial.print("C,");
        Serial.print(micros());
        Serial.print(",");
        Serial.print(elapsed);
        Serial.print(",");
        Serial.print(p1);
        Serial.print(",");
        Serial.print(p2);
        Serial.print(",");
        Serial.print(p3);
        Serial.print(",");
        Serial.println(p4);

        lastP1 = p1;
        lastP2 = p2;
        lastP3 = p3;
        lastP4 = p4;
    }
}


// ============================================================
// SUCCESS DETECTION
// ============================================================

void updateSuccessDetection()
{
    bool allConnected =
        allConnectorPinsConnected();

    unsigned long now =
        millis();

    unsigned long elapsed =
        now - sessionStartMs;


    // ========================================================
    // FIRST FULL CONNECT
    // ========================================================

    // Keep the old first-full-connect label.
    if (allConnected && !firstFullConnectDetected)
    {
        firstFullConnectDetected = true;

        firstFullConnectTimeMs =
            elapsed;

        Serial.print("FIRST_FULL_CONNECT,");
        Serial.println(firstFullConnectTimeMs);
    }


    // ========================================================
    // ALL FOUR CONTACTS CONNECTED
    // ========================================================

    if (allConnected)
    {
        // ----------------------------------------------------
        // Start timing a new possible insertion
        // ----------------------------------------------------

        if (!connectionTimerActive)
        {
            connectionTimerActive = true;

            allConnectedStartMs =
                now;

            currentInsertionStartMs =
                elapsed;

            currentConnectionAlreadyCounted =
                false;

            Serial.print("ALL_CONTACT_START,");
            Serial.println(
                currentInsertionStartMs
            );
        }


        // ----------------------------------------------------
        // Has this connection lasted long enough?
        // ----------------------------------------------------

        unsigned long connectedTime =
            now - allConnectedStartMs;


        if (
            connectedTime >= SUCCESS_CONFIRM_MS &&
            !currentConnectionAlreadyCounted
        )
        {
            // Prevent this same continuous connection from
            // being counted repeatedly.
            currentConnectionAlreadyCounted =
                true;


            // ------------------------------------------------
            // Save event
            // ------------------------------------------------

            if (successCount < MAX_SUCCESS_EVENTS)
            {
                // Store the actual insertion/start timestamp.
                successTimesMs[successCount] =
                    currentInsertionStartMs;

                // Also store when the 200 ms confirmation
                // completed.
                successConfirmTimesMs[successCount] =
                    elapsed;


                // Serial format:
                //
                // SUCCESS_DETECTED,
                // index,
                // insertion_start_ms,
                // confirmation_ms

                Serial.print("SUCCESS_DETECTED,");
                Serial.print(successCount);
                Serial.print(",");
                Serial.print(
                    successTimesMs[successCount]
                );
                Serial.print(",");
                Serial.println(
                    successConfirmTimesMs[successCount]
                );


                successCount++;
            }

            else
            {
                Serial.println(
                    "SUCCESS_EVENT_BUFFER_FULL"
                );
            }
        }
    }


    // ========================================================
    // CONNECTION LOST
    // ========================================================

    else
    {
        if (connectionTimerActive)
        {
            Serial.print("ALL_CONTACT_LOST,");
            Serial.println(elapsed);
        }


        // Reset everything associated with the current
        // connection attempt.
        //
        // This re-arms the detector so another insertion can
        // be counted later in the same session.

        connectionTimerActive = false;

        allConnectedStartMs = 0;
        currentInsertionStartMs = 0;

        currentConnectionAlreadyCounted = false;
    }
}


// ============================================================
// START SESSION
// ============================================================

void startSession()
{
    state = RECORDING;

    sessionStartMs = millis();


    // --------------------------------------------------------
    // Reset first connection
    // --------------------------------------------------------

    firstFullConnectDetected = false;
    firstFullConnectTimeMs = 0;


    // --------------------------------------------------------
    // Reset success events
    // --------------------------------------------------------

    successCount = 0;

    for (int i = 0; i < MAX_SUCCESS_EVENTS; i++)
    {
        successTimesMs[i] = 0;
        successConfirmTimesMs[i] = 0;
    }


    // --------------------------------------------------------
    // Reset connection state
    // --------------------------------------------------------

    connectionTimerActive = false;
    allConnectedStartMs = 0;

    currentInsertionStartMs = 0;
    currentConnectionAlreadyCounted = false;


    // --------------------------------------------------------
    // Reset connector logging
    // --------------------------------------------------------

    lastP1 = -1;
    lastP2 = -1;
    lastP3 = -1;
    lastP4 = -1;


    // Invalidate previous result until this trial finishes.
    lastTrialValid = false;


    // Recording indication starts immediately.
    setLEDs(false, true, false);


    Serial.println();
    Serial.println("SESSION_START");
}


// ============================================================
// END SESSION
// ============================================================

void endSession()
{
    unsigned long duration =
        millis() - sessionStartMs;


    // ========================================================
    // SAVE RESULT FOR LATER PYTHON QUERY
    // ========================================================

    lastTrialValid = true;

    lastTrialSuccess =
        successCount > 0;

    lastFirstFullConnectDetected =
        firstFullConnectDetected;

    lastFirstFullConnectTimeMs =
        firstFullConnectTimeMs;

    lastSuccessCount =
        successCount;


    for (int i = 0; i < successCount; i++)
    {
        lastSuccessTimesMs[i] =
            successTimesMs[i];

        lastSuccessConfirmTimesMs[i] =
            successConfirmTimesMs[i];
    }


    // ========================================================
    // SESSION END SUMMARY
    // ========================================================

    // Keep this relatively simple for compatibility.
    //
    // Format:
    //
    // SESSION_END,SUCCESS|FAIL,duration_ms,success_count

    Serial.print("SESSION_END,");

    if (successCount > 0)
    {
        Serial.print("SUCCESS,");
    }
    else
    {
        Serial.print("FAIL,");
    }

    Serial.print(duration);
    Serial.print(",");
    Serial.println(successCount);


    // ========================================================
    // PRINT ALL SUCCESS TIMESTAMPS
    // ========================================================

    // Format:
    //
    // SUCCESS_TIMES,count,time1,time2,time3,...

    Serial.print("SUCCESS_TIMES,");
    Serial.print(successCount);

    for (int i = 0; i < successCount; i++)
    {
        Serial.print(",");
        Serial.print(successTimesMs[i]);
    }

    Serial.println();


    // Also provide confirmation timestamps:
    //
    // SUCCESS_CONFIRM_TIMES,count,time1,time2,...

    Serial.print("SUCCESS_CONFIRM_TIMES,");
    Serial.print(successCount);

    for (int i = 0; i < successCount; i++)
    {
        Serial.print(",");
        Serial.print(successConfirmTimesMs[i]);
    }

    Serial.println();


    // ========================================================
    // RETURN TO IDLE
    // ========================================================

    state = IDLE;

    updateIdleLEDs();

    Serial.println("READY");
}


// ============================================================
// BUTTON HANDLING
// ============================================================

void updateButton()
{
    // Button connected to 3.3 V:
    // LOW  = released
    // HIGH = pressed

    bool pressed =
        digitalRead(BUTTON_PIN) == HIGH;


    // ========================================================
    // BUTTON JUST PRESSED
    // ========================================================

    if (pressed && !previousButtonState)
    {
        buttonPressStartMs =
            millis();

        longPressTriggered = false;
    }


    // ========================================================
    // BUTTON BEING HELD
    // ========================================================

    if (pressed)
    {
        unsigned long heldTime =
            millis() - buttonPressStartMs;


        if (
            heldTime >= LONG_PRESS_MS &&
            !longPressTriggered
        )
        {
            longPressTriggered = true;


            if (state == IDLE)
            {
                startSession();
            }
        }
    }


    // ========================================================
    // BUTTON RELEASED
    // ========================================================

    if (!pressed && previousButtonState)
    {
        longPressTriggered = false;
    }


    previousButtonState =
        pressed;
}


// ============================================================
// SERIAL COMMAND HANDLING
// ============================================================

void handleSerialCommands()
{
    if (!Serial.available())
    {
        return;
    }


    String command =
        Serial.readStringUntil('\n');

    command.trim();


    // ========================================================
    // GET LAST RESULT
    // ========================================================

    if (command == "GET_LAST_RESULT")
    {
        if (!lastTrialValid)
        {
            Serial.println(
                "LAST_RESULT,NONE,-1,0"
            );

            return;
        }


        // Format:
        //
        // LAST_RESULT,SUCCESS|FAIL,
        // first_full_connect_ms,
        // success_count

        Serial.print("LAST_RESULT,");

        if (lastTrialSuccess)
        {
            Serial.print("SUCCESS,");
        }
        else
        {
            Serial.print("FAIL,");
        }


        if (lastFirstFullConnectDetected)
        {
            Serial.print(
                lastFirstFullConnectTimeMs
            );
        }
        else
        {
            Serial.print(-1);
        }


        Serial.print(",");
        Serial.println(lastSuccessCount);
    }


    // ========================================================
    // GET ALL SUCCESS TIMES
    // ========================================================

    else if (command == "GET_SUCCESS_TIMES")
    {
        if (!lastTrialValid)
        {
            Serial.println(
                "SUCCESS_TIMES,NONE"
            );

            return;
        }


        // Format:
        //
        // SUCCESS_TIMES,count,t1,t2,t3,...

        Serial.print("SUCCESS_TIMES,");
        Serial.print(lastSuccessCount);


        for (int i = 0; i < lastSuccessCount; i++)
        {
            Serial.print(",");
            Serial.print(
                lastSuccessTimesMs[i]
            );
        }


        Serial.println();
    }


    // ========================================================
    // GET CONFIRMATION TIMES
    // ========================================================

    else if (command == "GET_SUCCESS_CONFIRM_TIMES")
    {
        if (!lastTrialValid)
        {
            Serial.println(
                "SUCCESS_CONFIRM_TIMES,NONE"
            );

            return;
        }


        Serial.print(
            "SUCCESS_CONFIRM_TIMES,"
        );

        Serial.print(lastSuccessCount);


        for (int i = 0; i < lastSuccessCount; i++)
        {
            Serial.print(",");
            Serial.print(
                lastSuccessConfirmTimesMs[i]
            );
        }


        Serial.println();
    }


    // ========================================================
    // STATUS
    // ========================================================

    else if (command == "STATUS")
    {
        if (state == IDLE)
        {
            Serial.println(
                "STATUS,IDLE"
            );
        }

        else
        {
            Serial.print(
                "STATUS,RECORDING,"
            );

            Serial.println(
                millis() - sessionStartMs
            );
        }
    }
}


// ============================================================
// SETUP
// ============================================================

void setup()
{
    Serial.begin(2000000);


    // ========================================================
    // LED OUTPUTS
    // ========================================================

    pinMode(
        GREEN_LED_PIN,
        OUTPUT
    );

    pinMode(
        YELLOW_LED_PIN,
        OUTPUT
    );

    pinMode(
        RED_LED_PIN,
        OUTPUT
    );

    LEDsOff();


    // ========================================================
    // BUTTON
    // ========================================================

    pinMode(
        BUTTON_PIN,
        INPUT_PULLDOWN
    );


    // ========================================================
    // CONNECTOR INPUTS
    // ========================================================

    pinMode(
        CONNECTOR_PIN_1,
        INPUT
    );

    pinMode(
        CONNECTOR_PIN_2,
        INPUT
    );

    pinMode(
        CONNECTOR_PIN_3,
        INPUT
    );

    pinMode(
        CONNECTOR_PIN_4,
        INPUT
    );


    delay(500);


    Serial.println();

    Serial.println(
        "=============================="
    );

    Serial.println(
        "DATA COLLECTION CONTROLLER"
    );

    Serial.println(
        "=============================="
    );

    Serial.println(
        "Hold button 1.5 s to start"
    );

    Serial.println(
        "Trial duration: 65 s"
    );

    Serial.println(
        "Multiple successful insertions enabled"
    );

    Serial.println(
        "Success confirmation: 200 ms"
    );

    Serial.println(
        "Green flashing = idle"
    );

    Serial.println(
        "READY"
    );
}


// ============================================================
// LOOP
// ============================================================

void loop()
{
    // Always handle button.
    updateButton();

    // Handle commands from Python.
    handleSerialCommands();


    // ========================================================
    // IDLE
    // ========================================================

    if (state == IDLE)
    {
        updateIdleLEDs();

        return;
    }


    // ========================================================
    // RECORDING
    // ========================================================

    if (state == RECORDING)
    {
        unsigned long elapsed =
            millis() - sessionStartMs;


        updateRecordingLEDs();

        updateConnectorLogging();

        updateSuccessDetection();


        // ----------------------------------------------------
        // HARD STOP AT 65 SECONDS
        // ----------------------------------------------------

        if (elapsed >= SESSION_DURATION_MS)
        {
            endSession();
        }
    }
}