#!/usr/bin/env python3

import json
import paho.mqtt.client as mqtt
import time

# MQTT Configuration
MQTT_BROKER = "localhost"  # Change to your MQTT broker address
MQTT_PORT = 1883
MQTT_USERNAME = None  # Set if required
MQTT_PASSWORD = None  # Set if required

def publish_configs():
    # Read the converted configurations
    with open("converted_configs.json", 'r') as f:
        configs = json.load(f)
    
    # Connect to MQTT broker
    client = mqtt.Client()
    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    
    client.connect(MQTT_BROKER, MQTT_PORT)
    
    # Publish each configuration
    for config in configs:
        topic = config["topic"]
        payload = json.dumps(config["payload"])
        client.publish(topic, payload, retain=True)
        time.sleep(0.1)  # Small delay to avoid flooding
    
    client.disconnect()

if __name__ == "__main__":
    publish_configs()