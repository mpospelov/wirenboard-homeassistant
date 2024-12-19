#!/usr/bin/env python3

import json
import os
from pathlib import Path
import re
import paho.mqtt.client as mqtt
from collections import defaultdict
import time
import logging
import sys
from typing import Optional, Set, Dict, List
from dataclasses import dataclass
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('conversion.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Configuration
TEMPLATES_DIRS = ["./templates", "./custom_templates"]
OUTPUT_FILE = "converted_configs.yml"
BASE_TOPIC = "homeassistant"

@dataclass
class MQTTConfig:
    broker: str = "localhost"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: str = "wb-converter"
    timeout: int = 5
    discovery_prefix: str = "homeassistant"

class MQTTTopicCache:
    def __init__(self, config: MQTTConfig):
        self.topics: Set[str] = set()
        self.config = config
        self.client = mqtt.Client(client_id=config.client_id)
        self._setup_client()

    def _setup_client(self):
        """Setup MQTT client with error handling"""
        if self.config.username and self.config.password:
            self.client.username_pw_set(self.config.username, self.config.password)

        self.client.on_message = self._on_message
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        try:
            self.client.connect(self.config.broker, self.config.port, self.config.timeout)
        except Exception as e:
            logging.error(f"Failed to connect to MQTT broker: {e}")
            raise

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("Connected to MQTT broker")
        else:
            logging.error(f"Failed to connect to MQTT broker with code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logging.warning(f"Unexpected disconnection from MQTT broker: {rc}")

    def _on_message(self, client, userdata, message):
        self.topics.add(message.topic)

    def fetch_topics(self) -> Set[str]:
        """Fetch all topics starting with /devices with error handling"""
        try:
            self.client.subscribe("/devices/#")
            self.client.loop_start()
            time.sleep(self.config.timeout)  # Wait for topics
            self.client.loop_stop()

            if not self.topics:
                logging.warning("No topics were collected from MQTT broker")
            else:
                logging.info(f"Collected {len(self.topics)} topics")

            return self.topics

        except Exception as e:
            logging.error(f"Error fetching topics: {e}")
            raise
        finally:
            try:
                self.client.disconnect()
            except Exception as e:
                logging.error(f"Error disconnecting from MQTT broker: {e}")

def load_config() -> MQTTConfig:
    """Load MQTT configuration from file or environment"""
    config_path = "config.json"

    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                config_data = json.load(f)
                mqtt_config = config_data.get("mqtt", {})
                return MQTTConfig(
                    broker=mqtt_config.get("host", "localhost"),
                    port=int(mqtt_config.get("port", 1883)),
                    username=mqtt_config.get("username"),
                    password=mqtt_config.get("password"),
                    client_id="wb-converter",
                    discovery_prefix=config_data.get("discovery_prefix", "homeassistant")
                )
    except Exception as e:
        logging.warning(f"Failed to load config file: {e}")

    # Fall back to environment variables
    return MQTTConfig(
        broker=os.getenv("MQTT_BROKER", "localhost"),
        port=int(os.getenv("MQTT_PORT", "1883")),
        username=os.getenv("MQTT_USERNAME"),
        password=os.getenv("MQTT_PASSWORD"),
        client_id="wb-converter"
    )

def find_matching_devices(topic_cache, topic_search):
    """Find all devices matching the topicSearch pattern"""
    if not topic_search:
        return []

    # Replace the problematic group naming with simple numbered groups
    pattern = topic_search
    group_count = 1

    # Keep track of positions we've already processed
    processed_positions = set()
    pos = 0

    while True:
        pos = pattern.find('(', pos)
        if pos == -1 or pos in processed_positions:
            break

        pattern = f"{pattern[:pos]}(?P<group{group_count}>{pattern[pos+1:]}"
        processed_positions.add(pos)
        pos += 1
        group_count += 1

    try:
        regex = re.compile(pattern)
        logging.info(f"Using regex pattern: {pattern}")
    except re.error as e:
        logging.warning(f"Invalid regex pattern '{pattern}': {e}")
        return []

    matches = []
    for topic in topic_cache.topics:
        match = regex.match(topic)
        if match:
            groups = match.groupdict()
            logging.info(f"Found match for topic {topic}. Groups: {groups}")
            matches.append(groups)

    return matches

def substitute_topic(topic_template, matches):
    """Replace placeholders in topicGet/topicSet with matched values"""
    result = topic_template
    logging.info(f"Substituting topic template {topic_template} with matches {matches}")
    for group_name, value in matches.items():
        group_num = group_name.replace('group', '')  # Extract number from 'group1', 'group2', etc.
        result = result.replace(f"({group_num})", value)
    return result

def get_value_template(link_type, service_type, char_type):
    """Generate value template based on type information"""
    if link_type == "Double":
        return "{{ value | float }}"
    elif link_type == "Integer":
        if service_type in ["Switch", "ContactSensor", "LeakSensor"]:
            return "{{ value | int }}"
        elif service_type == "Lightbulb" and char_type == "Brightness":
            return "{{ (value | int * 255 / 10000) | round | int }}"
        return "{{ value | int }}"
    return "{{ value }}"

def convert_characteristic_to_ha(device_matches, service, characteristic, topic_cache, seen_unique_ids):
    """Convert a characteristic to HA MQTT discovery format"""
    configs = []
    mqtt_config = load_config()  # Get config for discovery prefix

    for match in device_matches:
        char_type = characteristic.get("type")
        link = characteristic.get("link", {})
        link_type = link.get("type")
        service_type = service.get("type")

        # Get topic patterns
        topic_get = substitute_topic(link.get("topicGet", ""), match)
        topic_set = substitute_topic(link.get("topicSet", ""), match) if link.get("topicSet") else None

        device_id = match.get("group1", "unknown")
        # Extract model name and include it in device_id
        model = device_id.split('_')[0] if device_id and '_' in device_id else device_id

        # Include model prefix in device_id
        if "ecodim" in model.lower():
            device_id = f"ecodim_dali_{device_id}"
        elif "wb-mr" in model.lower():
            device_id = f"wb_modbus_relay_{device_id}"
        elif "wb-ms" in model.lower():
            device_id = f"wb_modbus_sensor_{device_id}"

        # Include group2 in device identifiers if available
        if match.get('group2'):
            device_id = f"{device_id}_{match.get('group2')}"

        # Generate unique_id
        unique_id = f"wb_{device_id}_{char_type}"

        # Skip if we've seen this unique_id before
        if unique_id in seen_unique_ids:
            continue

        seen_unique_ids.add(unique_id)

        # Base configuration
        config = {
            "name": device_id,  # Use the prefixed device_id as the name
            "unique_id": unique_id,
            "state_topic": topic_get,
            "device": {
                "identifiers": [f"wb_{device_id}"],
                "name": device_id,
                "manufacturer": service.get("manufacturer", "Wiren Board"),
                "model": model
            },
            "qos": 0
        }

        # Only add retain and optimistic for non-sensor components
        if service_type not in ["TemperatureSensor", "HumiditySensor", "LightSensor",
                              "AirQualitySensor", "LeakSensor", "ContactSensor", "BatteryService"]:
            config.update({
                "retain": True,
                "optimistic": False
            })

        # Add value template
        if service_type != "Lightbulb":  # Don't add value_template for lights
            config["value_template"] = get_value_template(link_type, service_type, char_type)

        # Handle command value conversion
        if topic_set:
            config["command_topic"] = topic_set

        # Map service types to HA components and classes
        component = "sensor"  # default

        if service_type == "TemperatureSensor":
            config.update({
                "device_class": "temperature",
                "unit_of_measurement": "°C",
                "state_class": "measurement"
            })
            component = "sensor"

        elif service_type == "HumiditySensor":
            config.update({
                "device_class": "humidity",
                "unit_of_measurement": "%",
                "state_class": "measurement"
            })
            component = "sensor"

        elif service_type == "LightSensor":
            config.update({
                "device_class": "illuminance",
                "unit_of_measurement": "lx",
                "state_class": "measurement"
            })
            component = "sensor"

        elif service_type == "AirQualitySensor":
            config.update({
                "device_class": "aqi",
                "state_class": "measurement"
            })
            component = "sensor"

        elif service_type == "LeakSensor":
            config.update({
                "device_class": "moisture",
                "payload_on": "1",
                "payload_off": "0"
            })
            component = "binary_sensor"

        elif service_type == "Switch":
            config.update({
                "payload_on": "1",
                "payload_off": "0"
            })
            component = "switch"

        elif service_type == "ContactSensor":
            config.update({
                "device_class": "opening",
                "payload_on": "1",
                "payload_off": "0"
            })
            component = "binary_sensor"

        elif service_type == "Lightbulb":
            # Base light configuration
            config.update({
                "payload_on": "1",
                "payload_off": "0",
                "brightness_scale": 100,
                "brightness_state_topic": f"{topic_get} Brightness",
                "brightness_command_topic": f"{topic_get} Brightness/on"
            })

            # Add color temperature support if it's a ColorTemperature characteristic
            if char_type == "ColorTemperature":
                config.update({
                    "color_temp_state_topic": topic_get,
                    "color_temp_command_topic": topic_set if topic_set else f"{topic_get}/on",
                    "min_mireds": 153,  # 6500K
                    "max_mireds": 370,  # 2700K
                    "color_temp_value_template": "{{ value | int }}"
                })

            component = "light"

            # Only add the configuration if it's the main "On" characteristic
            if char_type == "On":
                configs.append((component, config))

        elif service_type == "Valve":
            config.update({
                "payload_on": "1",
                "payload_off": "0",
                "device_class": "valve",
                "state_class": "measurement"
            })
            component = "switch"

        elif service_type == "C_PulseMeter" or char_type == "C_PulseCount":
            config.update({
                "device_class": "energy",
                "state_class": "total_increasing"
            })
            component = "sensor"

        configs.append((component, config))

    return configs

def load_template(file_path: Path) -> List[Dict]:
    """Load and validate a template file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            template = json.load(f)

        # Ensure template is a list
        if not isinstance(template, list):
            template = [template]

        logging.info(f"Loaded template from {file_path}: {len(template)} devices")
        return template

    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON in template {file_path}: {e}")
        raise
    except Exception as e:
        logging.error(f"Error loading template {file_path}: {e}")
        raise

def process_template(template_file: Path, topic_cache: MQTTTopicCache, seen_unique_ids: set) -> List[tuple]:
    """Process a single template file and return HA configurations"""
    template = load_template(template_file)
    configs = []

    for device in template:
        try:
            device_id = device.get("model", "unknown").lower().replace(" ", "-")
            manufacturer = device.get("manufacturer")

            for service in device.get("services", []):
                for characteristic in service.get("characteristics", []):
                    try:
                        topic_search = characteristic.get("link", {}).get("topicSearch")
                        device_matches = find_matching_devices(topic_cache, topic_search)

                        if device_matches:
                            service_configs = convert_characteristic_to_ha(
                                device_matches,
                                {**service, "manufacturer": manufacturer},
                                characteristic,
                                topic_cache,
                                seen_unique_ids
                            )
                            configs.extend(service_configs)
                        else:
                            logging.warning(f"No matching devices found for {topic_search}")

                    except Exception as e:
                        logging.error(f"Error processing characteristic {characteristic}: {e}")
                        continue

        except Exception as e:
            logging.error(f"Error processing device {device.get('model', 'unknown')}: {e}")
            continue

    return configs

def main():
    try:
        # Load configuration
        mqtt_config = load_config()

        # Initialize MQTT topic cache
        topic_cache = MQTTTopicCache(mqtt_config)
        topic_cache.fetch_topics()

        all_configs = defaultdict(list)

        # Process all template files from all directories
        template_files = []
        for template_dir in TEMPLATES_DIRS:
            template_dir_path = Path(template_dir)
            if template_dir_path.exists():
                template_files.extend(list(template_dir_path.glob("**/*.json")))
            else:
                logging.warning(f"Template directory {template_dir} does not exist")

        logging.info(f"Found {len(template_files)} template files")

        seen_unique_ids = set()
        for template_file in template_files:
            try:
                logging.info(f"Processing template: {template_file}")
                configs = process_template(template_file, topic_cache, seen_unique_ids)
                # Group configs by component type
                for component, config in configs:
                    # Create a deep copy to prevent YAML references
                    config_copy = json.loads(json.dumps(config))
                    all_configs[component].append(config_copy)
                logging.info(f"Successfully processed configurations from {template_file}")
            except Exception as e:
                logging.error(f"Error processing template {template_file}: {e}")

        # Write output file
        try:
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                yaml.dump(dict(all_configs), f, allow_unicode=True, sort_keys=False, default_flow_style=False, default_style='')
            logging.info(f"Successfully wrote configurations to {OUTPUT_FILE}")
        except Exception as e:
            logging.error(f"Error writing output file: {e}")
            raise

    except Exception as e:
        logging.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
