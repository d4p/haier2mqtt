#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e
if ! bashio::services.available mqtt; then
    bashio::log.error "MQTT service not available: install and start the Mosquitto broker add-on"
    exit 1
fi
# Assign first, export separately: a combined export+assignment would hide a bashio failure from set -e.
MQTT_HOST="$(bashio::services mqtt 'host')"
MQTT_PORT="$(bashio::services mqtt 'port')"
MQTT_USER="$(bashio::services mqtt 'username')"
MQTT_PASSWORD="$(bashio::services mqtt 'password')"
export MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASSWORD
export DATA_DIR=/data
bashio::log.info "Starting haier2mqtt (writes_enabled=$(bashio::config 'writes_enabled'))"
exec python3 -m haier2mqtt
