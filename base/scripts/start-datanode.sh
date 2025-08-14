#!/bin/bash

echo "Starting DataNode..."

# РћР¶РёРґР°РЅРёРµ Р·Р°РїСѓСЃРєР° NameNode
echo "Waiting for NameNode to start..."
sleep 10

# Р—Р°РїСѓСЃРє HDFS DataNode
echo "Starting HDFS DataNode..."
hdfs datanode &

# РћР¶РёРґР°РЅРёРµ Р·Р°РїСѓСЃРєР° DataNode
sleep 10

# Р—Р°РїСѓСЃРє YARN NodeManager
echo "Starting YARN NodeManager..."
yarn nodemanager &

# РћР¶РёРґР°РЅРёРµ Р·Р°РїСѓСЃРєР° NodeManager
sleep 10

echo "DataNode and NodeManager started successfully"

# Р”РµСЂР¶РёРј РєРѕРЅС‚РµР№РЅРµСЂ Р·Р°РїСѓС‰РµРЅРЅС‹Рј
tail -f /dev/null
