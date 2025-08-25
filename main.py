#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import math
import wiringpi as wp
import paho.mqtt.client as mqtt

# =========================
# ThingSpeak (MQTT credentials)
# =========================
CHANNEL_ID     = "3030971"
WRITE_API_KEY  = "7O059SUNMKM2MKFG"   # stays inside the payload

MQTT_CLIENT_ID = "MC4xIhkIFiMWDyUKBCkJJCY"
MQTT_USERNAME  = "MC4xIhkIFiMWDyUKBCkJJCY"
MQTT_PASSWORD  = "6fyCN6vvw5Ow0iR8ZmgXDSWo"

BROKER = "mqtt3.thingspeak.com"
PORT   = 1883
TOPIC  = f"channels/{CHANNEL_ID}/publish"   # api_key is NOT in the topic, only in payload

CHECK_PERIOD    = 15.0   # seconds: read goal/actual and decide every 15s
PUBLISH_PERIOD  = 15.0   # seconds: publish interval (kept the same)

# =========================
# wiringPi pins (Orange Pi)
# =========================
TRIG = 2   # Ultrasonic trigger
ECHO = 3   # Ultrasonic echo
IN1, IN2, IN3, IN4 = 4, 5, 6, 7   # Stepper motor control pins
PIN_CS, PIN_CLK, PIN_MOSI, PIN_MISO = 16, 14, 11, 12   # MCP3008 SPI (bit-bang)

# =========================
# Motor and control parameters
# =========================
HYST_CM       = 0.5     # Hysteresis band in cm
STEP_DELAY    = 0.002   # Delay between half-steps
OPEN_DURATION = 2.1     # Motor runtime for OPEN action
CLOSE_DURATION= 2.0     # Motor runtime for CLOSE action

# =========================
# GPIO initialization
# =========================
wp.wiringPiSetup()
wp.pinMode(TRIG, 1)  # TRIG as output
wp.pinMode(ECHO, 0)  # ECHO as input

# Stepper motor pins as outputs, default LOW
for p in (IN1, IN2, IN3, IN4):
    wp.pinMode(p, 1)
    wp.digitalWrite(p, 0)

# MCP3008 pins
wp.pinMode(PIN_CS,1); wp.pinMode(PIN_CLK,1)
wp.pinMode(PIN_MOSI,1); wp.pinMode(PIN_MISO,0)
wp.digitalWrite(PIN_CS,1); wp.digitalWrite(PIN_CLK,0)

# Stepper motor sequences
SEQ_FWD = [[1,0,0,1],[1,0,0,0],[1,1,0,0],[0,1,0,0],
           [0,1,1,0],[0,0,1,0],[0,0,1,1],[0,0,0,1]]
SEQ_BWD = list(reversed(SEQ_FWD))

# =========================
# Functions
# =========================
def measure_distance(timeout=0.03):
    """Measure distance in cm using HC-SR04 ultrasonic sensor."""
    wp.digitalWrite(TRIG,0); time.sleep(0.05)
    wp.digitalWrite(TRIG,1); time.sleep(0.00001); wp.digitalWrite(TRIG,0)
    t0=time.time(); to=t0+timeout
    while wp.digitalRead(ECHO)==0:
        pulse_start=time.time()
        if pulse_start>to: return float('nan')
    t1=time.time(); to=t1+timeout
    while wp.digitalRead(ECHO)==1:
        pulse_end=time.time()
        if pulse_end>to: return float('nan')
    return round((pulse_end-pulse_start)*17150,2)

def read_mcp3008(channel=0):
    """Read analog value (0-1023) from MCP3008 channel."""
    if not (0 <= channel <= 7): raise ValueError("Channel 0-7")
    cmd=0x18|channel
    wp.digitalWrite(PIN_CS,0)
    for i in range(5):
        bit=1 if (cmd & (0x10>>i)) else 0
        wp.digitalWrite(PIN_MOSI,bit)
        wp.digitalWrite(PIN_CLK,1); wp.digitalWrite(PIN_CLK,0)
    result=0
    for _ in range(12):
        wp.digitalWrite(PIN_CLK,1); wp.digitalWrite(PIN_CLK,0)
        result=(result<<1)|wp.digitalRead(PIN_MISO)
    wp.digitalWrite(PIN_CS,1)
    return (result>>1)&0x3FF

def drive_sequence(seq, duration, delay):
    """Run stepper motor with given sequence for specified duration."""
    start=time.time()
    while (time.time()-start)<duration:
        for a,b,c,d in seq:
            wp.digitalWrite(IN1,a); wp.digitalWrite(IN2,b)
            wp.digitalWrite(IN3,c); wp.digitalWrite(IN4,d)
            time.sleep(delay)
    # release motor coils
    for p in (IN1,IN2,IN3,IN4):
        wp.digitalWrite(p,0)

def accion_abrir():
    """Perform OPEN action with stepper motor."""
    print("[ACTION] OPEN")
    drive_sequence(SEQ_FWD, OPEN_DURATION, STEP_DELAY)

def accion_cerrar():
    """Perform CLOSE action with stepper motor."""
    print("[ACTION] CLOSE")
    drive_sequence(SEQ_BWD, CLOSE_DURATION, STEP_DELAY)

# =========================
# MQTT Setup
# =========================
client = mqtt.Client(client_id=MQTT_CLIENT_ID, protocol=mqtt.MQTTv311, clean_session=True)
client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

def on_connect(c,u,flags,rc): print(f"[MQTT] Connected rc={rc}")
def on_disconnect(c,u,rc): print(f"[MQTT] Disconnected rc={rc}")
client.on_connect=on_connect
client.on_disconnect=on_disconnect

client.connect_async(BROKER, PORT, keepalive=60)
client.loop_start()

# =========================
# Main loop
# =========================
try:
    last_pub   = 0.0
    last_check = 0.0
    last_action = None    # "open", "close" or None

    goal_cm   = float('nan')   # cached goal value
    actual_cm = float('nan')   # cached actual value

    while True:
        now = time.time()

        # Perform the full cycle every CHECK_PERIOD
        if now - last_check >= CHECK_PERIOD:
            # 1) Read goal from potentiometer via MCP3008
            raw = read_mcp3008(0)
            goal_cm = (raw/1023.0)*30.0

            # 2) Measure actual distance
            actual_cm = measure_distance()
            actual_str = f"{actual_cm:.2f}" if actual_cm == actual_cm else "NaN"
            print(f"[{time.strftime('%H:%M:%S')}] Goal={goal_cm:.2f} cm | Actual={actual_str} cm | Last action={last_action}")

            # 3) Publish to ThingSpeak via MQTT
            if (now - last_pub) >= PUBLISH_PERIOD and client.is_connected():
                actual_to_send = actual_cm if actual_cm == actual_cm else -1.0
                payload = f"api_key={WRITE_API_KEY}&field1={goal_cm:.2f}&field2={actual_to_send:.2f}"
                client.publish(TOPIC, payload, qos=0)
                print(f"[MQTT] Published: {payload}")
                last_pub = now

            # 4) Decide motor action with hysteresis, avoiding repeats
            if actual_cm == actual_cm:  # valid (not NaN)
                if (goal_cm - actual_cm) > HYST_CM:
                    # Needs CLOSE
                    if last_action != "close":
                        accion_cerrar()
                        last_action = "close"
                    else:
                        print("[SKIP] CLOSE already executed last time.")
                elif (actual_cm - goal_cm) > HYST_CM:
                    # Needs OPEN
                    if last_action != "open":
                        accion_abrir()
                        last_action = "open"
                    else:
                        print("[SKIP] OPEN already executed last time.")
                else:
                    print("[INFO] Within hysteresis band. No action.")
            else:
                print("[WARN] Invalid measurement (NaN). No action taken.")

            last_check = now

        # Prevent CPU overload between checks
        time.sleep(0.05)

except KeyboardInterrupt:
    print("Stopped by user.")
finally:
    client.loop_stop()
    client.disconnect()
    for p in (IN1,IN2,IN3,IN4):
        wp.digitalWrite(p,0)
