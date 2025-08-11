#!/bin/bash

echo "=== Testing Hive Installation ==="

echo "1. Checking Hive version..."
hive --version

echo "2. Checking environment variables..."
echo "HIVE_HOME: $HIVE_HOME"
echo "HADOOP_HOME: $HADOOP_HOME"
echo "JAVA_HOME: $JAVA_HOME"

echo "3. Checking Hive directories..."
ls -la /opt/hive/
ls -la /opt/hive/conf/

echo "4. Checking Hive configuration..."
cat /opt/hive/conf/hive-site.xml | head -20

echo "5. Testing Hive CLI..."
echo "Creating test table..."
hive -e "CREATE TABLE IF NOT EXISTS test_table (id INT, name STRING);"

echo "6. Testing Hive Metastore connection..."
hive -e "SHOW DATABASES;"

echo "=== Hive Installation Test Completed ==="
