#!/bin/bash

echo "=== Checking HDFS Health ==="

# HDFS health check (maximum 5 minutes)
timeout=300
interval=15
elapsed=0

while [ $elapsed -lt $timeout ]; do
    # Check NameNode status
    if hdfs dfsadmin -safemode get | grep -q "Safe mode is OFF"; then
        echo "✓ NameNode is out of safe mode"
        
        # Check live DataNodes
        live_datanodes=$(hdfs dfsadmin -report -liveDataNodes | grep "Live datanodes" | sed 's/.*Live datanodes (\([0-9]*\)).*/\1/')
        if [ -n "$live_datanodes" ] && [ "$live_datanodes" -gt 0 ] 2>/dev/null; then
            echo "✓ Found $live_datanodes live DataNode(s)"
            
            # Check HDFS accessibility
            if hdfs dfs -test -d /; then
                echo "✓ HDFS root directory is accessible"
                echo "✓ HDFS Health Check: PASSED"
                exit 0
            else
                echo "⚠ Cannot access HDFS root directory, waiting... ($elapsed/$timeout seconds)"
            fi
        else
            echo "⚠ No live DataNodes found, waiting... ($elapsed/$timeout seconds)"
        fi
    else
        echo "⚠ NameNode is still in safe mode, waiting... ($elapsed/$timeout seconds)"
    fi
    
    sleep $interval
    elapsed=$((elapsed + interval))
done

echo "✗ HDFS failed to start within $timeout seconds"
echo "✗ HDFS Health Check: FAILED"
exit 1
