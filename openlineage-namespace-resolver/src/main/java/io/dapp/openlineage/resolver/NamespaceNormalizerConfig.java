package io.dapp.openlineage.resolver;

import io.openlineage.client.MergeConfig;
import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolverConfig;

/**
 * Конфиг резолвера-нормализатора. Обычный POJO — Jackson OpenLineage биндит его по
 * bean-property из ключей {@code spark.openlineage.dataset.namespaceResolvers.<name>.*}.
 */
public class NamespaceNormalizerConfig
    implements DatasetNamespaceResolverConfig, MergeConfig<NamespaceNormalizerConfig> {

  private String separator;

  public NamespaceNormalizerConfig() {
  }

  public NamespaceNormalizerConfig(String separator) {
    this.separator = separator;
  }

  public String getSeparator() {
    return separator;
  }

  public void setSeparator(String separator) {
    this.separator = separator;
  }

  /**
   * Сливает конфиг с непустым: ненулевое значение из {@code other} перекрывает текущее.
   *
   * @param other конфиг с более высоким приоритетом
   * @return новый слитый конфиг
   */
  @Override
  public NamespaceNormalizerConfig mergeWithNonNull(NamespaceNormalizerConfig other) {
    String merged = other.getSeparator() != null ? other.getSeparator() : this.separator;
    return new NamespaceNormalizerConfig(merged);
  }
}
