#!/bin/bash

# Start SSH service
sudo service ssh start

# Wait for SSH startup
sleep 2

echo "SSH service started"
echo "SSH keys configured for user hadoop"

# Keep container running
tail -f /dev/null
