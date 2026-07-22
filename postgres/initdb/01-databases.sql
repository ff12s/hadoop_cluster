-- Роль и база Marquez в общем кластере PostgreSQL стенда.
-- Выполняется только при первичной инициализации: на существующем томе
-- docker-entrypoint пропускает каталог initdb.d целиком.
-- Учётные данные захардкожены синхронно с marquez/config/config.yml и
-- окружением сервиса marquez в docker-compose.yml.
CREATE ROLE marquez WITH LOGIN PASSWORD 'marquez';
CREATE DATABASE marquez OWNER marquez;
