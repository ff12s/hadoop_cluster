#!/bin/bash

echo "=== РўРµСЃС‚РёСЂРѕРІР°РЅРёРµ Р±Р°Р·РѕРІРѕРіРѕ РѕР±СЂР°Р·Р° ==="

# РџСЂРѕРІРµСЂРєР° Java
echo "1. РџСЂРѕРІРµСЂРєР° Java:"
java -version
if [ $? -eq 0 ]; then
    echo "вњ… Java СѓСЃС‚Р°РЅРѕРІР»РµРЅ РєРѕСЂСЂРµРєС‚РЅРѕ"
else
    echo "вќЊ РћС€РёР±РєР° СЃ Java"
    exit 1
fi

# РџСЂРѕРІРµСЂРєР° Python
echo -e "\n2. РџСЂРѕРІРµСЂРєР° Python:"
python3 --version
if [ $? -eq 0 ]; then
    echo "вњ… Python СѓСЃС‚Р°РЅРѕРІР»РµРЅ РєРѕСЂСЂРµРєС‚РЅРѕ"
else
    echo "вќЊ РћС€РёР±РєР° СЃ Python"
    exit 1
fi

# РџСЂРѕРІРµСЂРєР° Scala
echo -e "\n3. РџСЂРѕРІРµСЂРєР° Scala:"
scala -version
if [ $? -eq 0 ]; then
    echo "вњ… Scala СѓСЃС‚Р°РЅРѕРІР»РµРЅ РєРѕСЂСЂРµРєС‚РЅРѕ"
else
    echo "вќЊ РћС€РёР±РєР° СЃ Scala"
    exit 1
fi

# РџСЂРѕРІРµСЂРєР° SSH
echo -e "\n4. РџСЂРѕРІРµСЂРєР° SSH:"
if [ -f /home/hadoop/.ssh/id_rsa ]; then
    echo "вњ… SSH РєР»СЋС‡Рё РЅР°СЃС‚СЂРѕРµРЅС‹"
else
    echo "вќЊ SSH РєР»СЋС‡Рё РЅРµ РЅР°Р№РґРµРЅС‹"
    exit 1
fi

# РџСЂРѕРІРµСЂРєР° РґРёСЂРµРєС‚РѕСЂРёР№
echo -e "\n5. РџСЂРѕРІРµСЂРєР° РґРёСЂРµРєС‚РѕСЂРёР№:"
directories=("/opt/hadoop" "/opt/hive" "/opt/spark" "/opt/kyubi" "/opt/jupyter" "/mnt/data" "/mnt/logs")
for dir in "${directories[@]}"; do
    if [ -d "$dir" ]; then
        echo "вњ… $dir СЃСѓС‰РµСЃС‚РІСѓРµС‚"
    else
        echo "вќЊ $dir РЅРµ РЅР°Р№РґРµРЅ"
        exit 1
    fi
done

# РџСЂРѕРІРµСЂРєР° РїРµСЂРµРјРµРЅРЅС‹С… РѕРєСЂСѓР¶РµРЅРёСЏ
echo -e "\n6. РџСЂРѕРІРµСЂРєР° РїРµСЂРµРјРµРЅРЅС‹С… РѕРєСЂСѓР¶РµРЅРёСЏ:"
env_vars=("JAVA_HOME" "SCALA_HOME" "HADOOP_HOME" "HIVE_HOME" "SPARK_HOME" "KYUBI_HOME" "JUPYTER_HOME")
for var in "${env_vars[@]}"; do
    if [ -n "${!var}" ]; then
        echo "вњ… $var=${!var}"
    else
        echo "вќЊ $var РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅР°"
        exit 1
    fi
done

echo -e "\nрџЋ‰ Р‘Р°Р·РѕРІС‹Р№ РѕР±СЂР°Р· РіРѕС‚РѕРІ Рє СЂР°Р±РѕС‚Рµ!"
