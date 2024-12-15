fetch-sprut-templates:
	git clone --depth 1 --branch main --single-branch --no-tags https://github.com/wirenboard/wb-spruthub-templates ./templates
	rm -rf ./templates/.git

convert-templates-to-ha:
	bin/python3 scripts/convert_templates.py

publish-to-ha:
	# Read the converted configs and publish each one to MQTT
	bin/python3 scripts/publish_mqtt.py

setup-ha-integration: fetch-sprut-templates convert-templates-to-ha publish-to-ha