#!/bin/bash

echo "=== Starting TEZ UI ==="

TEZ_UI_PORT=9999
TEZ_UI_DIR=/opt/tez-ui

# TEZ UI WAR is pre-extracted during Docker build into /opt/tez-ui
if [ ! -d "$TEZ_UI_DIR" ] || [ -z "$(ls -A $TEZ_UI_DIR 2>/dev/null)" ]; then
    echo "ERROR: TEZ UI directory $TEZ_UI_DIR is empty or missing"
    exit 1
fi

echo "TEZ UI found at $TEZ_UI_DIR"

# Configure TEZ UI to connect to YARN Timeline Server and ResourceManager
CONFIG_DIR="$TEZ_UI_DIR/config"
mkdir -p "$CONFIG_DIR"

cat > "$CONFIG_DIR/configs.env" << 'ENVEOF'
# TEZ UI configuration
# URLs go through nginx webproxy, so localhost works without hosts file
ENV = {
  defined: {
    defined: true,
    timelineBaseUrl: "http://localhost:8188",
    RMWebUrl: "http://localhost:8088"
  }
};
ENVEOF

echo "TEZ UI configuration written"

# Wait for YARN Timeline Server to be available
echo "Waiting for YARN Timeline Server to be ready..."
until nc -z namenode 8188; do
    echo "Timeline Server not ready, waiting..."
    sleep 5
done
echo "YARN Timeline Server is ready!"

# Start a simple HTTP server to serve the TEZ UI
echo "Starting TEZ UI on port $TEZ_UI_PORT..."
cd $TEZ_UI_DIR
exec python3 -m http.server $TEZ_UI_PORT --bind 0.0.0.0
