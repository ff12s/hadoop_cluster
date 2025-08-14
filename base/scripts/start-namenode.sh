#!/bin/bash

echo "Starting NameNode..."

# Р¤РѕСЂРјР°С‚РёСЂРѕРІР°РЅРёРµ NameNode (РµСЃР»Рё РЅРµ РѕС‚С„РѕСЂРјР°С‚РёСЂРѕРІР°РЅ)
if [ ! -f /opt/hadoop/dfs/name/current/VERSION ]; then
    echo "Formatting NameNode..."
    hdfs namenode -format
fi

# Р—Р°РїСѓСЃРє HDFS NameNode
echo "Starting HDFS NameNode..."
hdfs namenode &

# РћР¶РёРґР°РЅРёРµ Р·Р°РїСѓСЃРєР° NameNode
sleep 10

# Р—Р°РїСѓСЃРє YARN ResourceManager
echo "Starting YARN ResourceManager..."
yarn resourcemanager &

# РћР¶РёРґР°РЅРёРµ Р·Р°РїСѓСЃРєР° ResourceManager
sleep 10

echo "NameNode and ResourceManager started successfully"

# Р”РµСЂР¶РёРј РєРѕРЅС‚РµР№РЅРµСЂ Р·Р°РїСѓС‰РµРЅРЅС‹Рј
tail -f /dev/null
