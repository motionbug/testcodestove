ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.19
FROM $BUILD_FROM

# Install Python 3, cryptography (for AES-128-CBC), and paho-mqtt from Alpine packages
RUN apk add --no-cache \
    python3 \
    py3-cryptography \
    py3-paho-mqtt \
    jq

WORKDIR /app

COPY stove_bridge.py /app/stove_bridge.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
