package io.dapp.openlineage.resolver;

import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolver;
import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolverBuilder;
import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolverConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Билдер, регистрирующий {@link NamespaceNormalizer} в OpenLineage через ServiceLoader.
 * Тип {@code normalize} задаётся в {@code spark.openlineage.dataset.namespaceResolvers.<name>.type}.
 */
public class NamespaceNormalizerBuilder implements DatasetNamespaceResolverBuilder {

  private static final Logger log = LoggerFactory.getLogger(NamespaceNormalizerBuilder.class);

  @Override
  public String getType() {
    return "normalize";
  }

  @Override
  public DatasetNamespaceResolverConfig getConfig() {
    return new NamespaceNormalizerConfig();
  }

  /**
   * Собирает резолвер. Имя {@code name} для нормализатора — только метка (выход считается из входа).
   *
   * @param name   имя резолвера из ключа конфигурации (не используется в логике)
   * @param config конфиг типа {@link NamespaceNormalizerConfig}
   * @return готовый {@link NamespaceNormalizer}
   */
  @Override
  public DatasetNamespaceResolver build(String name, DatasetNamespaceResolverConfig config) {
    String separator = NamespaceNormalizer.DEFAULT_SEPARATOR;
    if (config instanceof NamespaceNormalizerConfig) {
      String cfgSeparator = ((NamespaceNormalizerConfig) config).getSeparator();
      if (cfgSeparator != null && !cfgSeparator.isEmpty()) {
        separator = cfgSeparator;
      }
    }
    log.info("NamespaceNormalizer active (name={}, separator={})", name, separator);
    return new NamespaceNormalizer(separator);
  }
}
