#!/usr/bin/env python3

import json
import yaml
import paho.mqtt.client as mqtt
import time

# Load configuration from config.json
with open("config.json", 'r') as f:
    config = json.load(f)

# MQTT Configuration from config file
MQTT_BROKER = config["mqtt"]["host"]
MQTT_PORT = config["mqtt"]["port"]
MQTT_USERNAME = config["mqtt"].get("username", None)  # Optional
MQTT_PASSWORD = config["mqtt"].get("password", None)  # Optional
DISCOVERY_PREFIX = config["discovery_prefix"]

def publish_configs():
    # Read the converted configurations
    with open("converted_configs.yml", 'r') as f:
        configs = yaml.safe_load(f)

    # Connect to MQTT broker
    client = mqtt.Client()
    if MQTT_USERNAME and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    client.connect(MQTT_BROKER, MQTT_PORT)

    # Publish each configuration
    for component_type, devices in configs.items():
        for device_id, characteristics in devices.items():
            for char_type, config_data in characteristics.items():
                # Construct discovery topic: <discovery_prefix>/<component_type>/<device_id>/<char_type>/config
                topic = f"{DISCOVERY_PREFIX}/{component_type}/{device_id}/{char_type}/config"
                payload = json.dumps(config_data)
                client.publish(topic, payload, retain=True)
                time.sleep(0.1)  # Small delay to avoid flooding

    client.disconnect()

if __name__ == "__main__":
    publish_configs()
