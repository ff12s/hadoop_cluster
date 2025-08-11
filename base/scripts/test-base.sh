#!/bin/bash

echo "=== Тестирование базового образа ==="

# Проверка Java
echo "1. Проверка Java:"
java -version
if [ $? -eq 0 ]; then
    echo "✅ Java установлен корректно"
else
    echo "❌ Ошибка с Java"
    exit 1
fi

# Проверка Python
echo -e "\n2. Проверка Python:"
python3 --version
if [ $? -eq 0 ]; then
    echo "✅ Python установлен корректно"
else
    echo "❌ Ошибка с Python"
    exit 1
fi

# Проверка Scala
echo -e "\n3. Проверка Scala:"
scala -version
if [ $? -eq 0 ]; then
    echo "✅ Scala установлен корректно"
else
    echo "❌ Ошибка с Scala"
    exit 1
fi

# Проверка SSH
echo -e "\n4. Проверка SSH:"
if [ -f /home/hadoop/.ssh/id_rsa ]; then
    echo "✅ SSH ключи настроены"
else
    echo "❌ SSH ключи не найдены"
    exit 1
fi

# Проверка директорий
echo -e "\n5. Проверка директорий:"
directories=("/opt/hadoop" "/opt/hive" "/opt/spark" "/opt/kyubi" "/opt/jupyter" "/mnt/data" "/mnt/logs")
for dir in "${directories[@]}"; do
    if [ -d "$dir" ]; then
        echo "✅ $dir существует"
    else
        echo "❌ $dir не найден"
        exit 1
    fi
done

# Проверка переменных окружения
echo -e "\n6. Проверка переменных окружения:"
env_vars=("JAVA_HOME" "SCALA_HOME" "HADOOP_HOME" "HIVE_HOME" "SPARK_HOME" "KYUBI_HOME" "JUPYTER_HOME")
for var in "${env_vars[@]}"; do
    if [ -n "${!var}" ]; then
        echo "✅ $var=${!var}"
    else
        echo "❌ $var не установлена"
        exit 1
    fi
done

echo -e "\n🎉 Базовый образ готов к работе!"
