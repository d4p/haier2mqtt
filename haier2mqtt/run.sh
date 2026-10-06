#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e
export MQTT_HOST="$(bashio::services mqtt 'host')"
export MQTT_PORT="$(bashio::services mqtt 'port')"
export MQTT_USER="$(bashio::services mqtt 'username')"
export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
export DATA_DIR=/data
bashio::log.info "Starting haier2mqtt (writes_enabled=$(bashio::config 'writes_enabled'))"
exec python3 -m haier2mqtt
