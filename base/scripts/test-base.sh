#!/bin/bash

echo "=== Testing Base Image ==="

# Check Java
echo "1. Java Check:"
java -version
if [ $? -eq 0 ]; then
    echo "✓ Java installed correctly"
else
    echo "✗ Java error"
    exit 1
fi

# Check Python
echo -e "\n2. Python Check:"
python3 --version
if [ $? -eq 0 ]; then
    echo "✓ Python installed correctly"
else
    echo "✗ Python error"
    exit 1
fi

# Check Scala
echo -e "\n3. Scala Check:"
scala -version
if [ $? -eq 0 ]; then
    echo "✓ Scala installed correctly"
else
    echo "✗ Scala error"
    exit 1
fi

# Check SSH
echo -e "\n4. SSH Check:"
if [ -f /home/hadoop/.ssh/id_rsa ]; then
    echo "✓ SSH keys configured"
else
    echo "✗ SSH keys not found"
    exit 1
fi

# Check directories
echo -e "\n5. Directory Check:"
directories=("/opt/hadoop" "/opt/hive" "/opt/spark" "/opt/kyubi" "/opt/jupyter" "/mnt/data" "/mnt/logs")
for dir in "${directories[@]}"; do
    if [ -d "$dir" ]; then
        echo "✓ $dir exists"
    else
        echo "✗ $dir not found"
        exit 1
    fi
done

# Check environment variables
echo -e "\n6. Environment Variables Check:"
env_vars=("JAVA_HOME" "SCALA_HOME" "HADOOP_HOME" "HIVE_HOME" "SPARK_HOME" "KYUBI_HOME" "JUPYTER_HOME")
for var in "${env_vars[@]}"; do
    if [ -n "${!var}" ]; then
        echo "✓ $var=${!var}"
    else
        echo "✗ $var not set"
        exit 1
    fi
done

echo -e "\n🎉 Base image ready for work!"
