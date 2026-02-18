#!/bin/bash

echo "=== Checking Hive Health ==="

# Check HiveServer2 via beeline (Hive+TEZ startup can take 5–10 minutes)
timeout=600
interval=15
elapsed=0

while [ $elapsed -lt $timeout ]; do
    if beeline -u jdbc:hive2://localhost:10000 -n hadoop -p "" -e "SHOW DATABASES;" 2>/dev/null | grep -q "default"; then
        echo "✓ HiveServer2 is ready"
        echo "Hive Health Check: PASSED"
        exit 0
    else
        echo "⚠ HiveServer2 not ready, waiting... ($elapsed/$timeout seconds)"
        sleep $interval
        elapsed=$((elapsed + interval))
    fi
done

echo "✗ HiveServer2 failed to start within $timeout seconds"
echo "Hive Health Check: FAILED"
exit 1
