#!/bin/bash

echo "=== Checking HDFS Health ==="

# РџСЂРѕРІРµСЂРєР° HDFS (РјР°РєСЃРёРјСѓРј 5 РјРёРЅСѓС‚)
timeout=300
interval=15
elapsed=0

while [ $elapsed -lt $timeout ]; do
    # РџСЂРѕРІРµСЂРєР° СЃС‚Р°С‚СѓСЃР° NameNode
    if hdfs dfsadmin -safemode get | grep -q "Safe mode is OFF"; then
        echo "вњ… NameNode is out of safe mode"
        
        # РџСЂРѕРІРµСЂРєР° Р¶РёРІС‹С… DataNode
        live_datanodes=$(hdfs dfsadmin -report -liveDataNodes | grep "Live datanodes" | sed 's/.*Live datanodes (\([0-9]*\)).*/\1/')
        if [ -n "$live_datanodes" ] && [ "$live_datanodes" -gt 0 ] 2>/dev/null; then
            echo "вњ… Found $live_datanodes live DataNode(s)"
            
            # РџСЂРѕРІРµСЂРєР° РґРѕСЃС‚СѓРїРЅРѕСЃС‚Рё HDFS
            if hdfs dfs -test -d /; then
                echo "вњ… HDFS root directory is accessible"
                echo "вњ… HDFS Health Check: PASSED"
                exit 0
            else
                echo "вЏі Cannot access HDFS root directory, waiting... ($elapsed/$timeout seconds)"
            fi
        else
            echo "вЏі No live DataNodes found, waiting... ($elapsed/$timeout seconds)"
        fi
    else
        echo "вЏі NameNode is still in safe mode, waiting... ($elapsed/$timeout seconds)"
    fi
    
    sleep $interval
    elapsed=$((elapsed + interval))
done

echo "вќЊ HDFS failed to start within $timeout seconds"
echo "вќЊ HDFS Health Check: FAILED"
exit 1
