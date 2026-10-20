#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

# Auto-populate MQTT credentials from Home Assistant Supervisor if not explicitly set
if bashio::services.available "mqtt"; then
    if ! bashio::config.has_value "mqtt_user"; then
        export MQTT_HOST=$(bashio::services mqtt "host")
        export MQTT_PORT=$(bashio::services mqtt "port")
        export MQTT_USER=$(bashio::services mqtt "username")
        export MQTT_PASSWORD=$(bashio::services mqtt "password")
    fi
fi

bashio::log.info "Starting Duepi Stove Local MITM & MQTT Bridge..."
exec python3 -u /app/stove_bridge.py /data/options.json
