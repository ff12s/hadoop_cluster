#!/bin/bash

# Запуск SSH сервиса
sudo service ssh start

# Ожидание запуска SSH
sleep 2

echo "SSH сервис запущен"
echo "SSH ключи настроены для пользователя hadoop"

# Держим контейнер запущенным
tail -f /dev/null
