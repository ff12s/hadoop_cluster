package io.dapp.openlineage.resolver;

import io.openlineage.client.dataset.DatasetConfig;
import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolver;
import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolverConfig;
import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolverLoader;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ResolverLoadingTest {

  @Test
  void serviceLoaderDiscoversNormalizerAndResolves() {
    Map<String, DatasetNamespaceResolverConfig> resolvers = new HashMap<>();
    resolvers.put("default", new NamespaceNormalizerConfig());
    DatasetConfig cfg = new DatasetConfig();
    cfg.setNamespaceResolvers(resolvers);

    List<DatasetNamespaceResolver> loaded =
        DatasetNamespaceResolverLoader.loadDatasetNamespaceResolvers(cfg);

    assertEquals(1, loaded.size(), "должен быть найден ровно один резолвер");
    assertTrue(loaded.get(0) instanceof NamespaceNormalizer,
        "ServiceLoader должен подхватить NamespaceNormalizer из META-INF/services");
    assertEquals("postgres://pg1:5432+pg2:5432",
        loaded.get(0).resolve("postgres://pg2:5432,pg1:5432"));
  }

  @Test
  void typeStringResolvesToConfigClass() {
    // .type=normalize из SparkConf должен резолвиться в наш класс конфига через ServiceLoader —
    // это тот же путь, что использует Jackson OpenLineage при разборе spark.openlineage.dataset.namespaceResolvers.*
    assertEquals(NamespaceNormalizerConfig.class,
        io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolverLoader
            .loadDatasetNamespaceResolverConfigByType("normalize"));
  }
}
