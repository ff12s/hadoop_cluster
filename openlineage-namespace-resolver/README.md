# openlineage-namespace-resolver

Кастомный OpenLineage `DatasetNamespaceResolver` (`type=normalize`), делающий dataset namespace
валидным для Marquez 0.47.0 универсально для всех источников.

## Что делает

`scheme://authority[/path]` → если authority содержит несколько хостов через запятую, хосты
сортируются и склеиваются легальным сепаратором (`+`), результат порядконезависим. Затем любой
символ вне charset Marquez `^[a-zA-Z0-9_@+:;=/.-]{1,1024}$` заменяется на `_`.

Пример: `postgres://h1:5432,h2:5432` → `postgres://h1:5432+h2:5432`.

## Сборка

```bash
bash mvnd.sh package        # jar в target/openlineage-namespace-resolver-0.1.0.jar
bash mvnd.sh test           # unit + integration тесты
```

`mvnd.sh` использует хостовый Maven/JDK, если они есть в PATH; иначе гоняет Maven в Docker.

## Подключение к Spark

Положить jar на classpath драйвера (рядом с `openlineage-spark`), затем:

```
spark.openlineage.dataset.namespaceResolvers.default.type   normalize
```

`openlineage-java` — provided: классы SPI даёт `openlineage-spark` на classpath в рантайме.
