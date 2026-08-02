package io.dapp.openlineage.resolver;

import io.openlineage.client.dataset.namespace.resolver.DatasetNamespaceResolver;

import java.util.Arrays;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * Приводит dataset namespace к форме, валидной для Marquez 0.47.0.
 *
 * Multi-host authority (например {@code postgres://h1:5432,h2:5432}) схлопывается:
 * хосты сортируются и склеиваются легальным сепаратором (по умолчанию {@code +}),
 * что даёт порядконезависимый результат. Затем любой символ вне charset Marquez
 * заменяется на {@code _}.
 */
public class NamespaceNormalizer implements DatasetNamespaceResolver {

  /** Разделитель хостов по умолчанию; входит в charset Marquez. */
  public static final String DEFAULT_SEPARATOR = "+";

  private static final int MAX_LENGTH = 1024;
  private static final Pattern ILLEGAL = Pattern.compile("[^A-Za-z0-9_@+:;=/.-]");

  private final String separator;

  /**
   * @param separator разделитель для склейки хостов; пустой/null → {@link #DEFAULT_SEPARATOR}
   */
  public NamespaceNormalizer(String separator) {
    this.separator = (separator == null || separator.isEmpty()) ? DEFAULT_SEPARATOR : separator;
  }

  /**
   * Нормализует namespace под charset Marquez.
   *
   * @param namespace исходный namespace (может быть null/пустым)
   * @return Marquez-валидный namespace; null/пустое возвращаются без изменений
   */
  @Override
  public String resolve(String namespace) {
    if (namespace == null || namespace.isEmpty()) {
      return namespace;
    }
    String result = collapseAuthority(namespace);
    result = ILLEGAL.matcher(result).replaceAll("_");
    if (result.length() > MAX_LENGTH) {
      result = result.substring(0, MAX_LENGTH);
    }
    return result;
  }

  /**
   * Схлопывает multi-host authority (часть между {@code ://} и первым {@code /}).
   *
   * @param namespace исходный namespace
   * @return namespace с отсортированным и склеенным списком хостов; без {@code ://} или без
   *         запятой в authority — возвращается как есть
   */
  private String collapseAuthority(String namespace) {
    int schemeEnd = namespace.indexOf("://");
    if (schemeEnd < 0) {
      return namespace;
    }
    int authorityStart = schemeEnd + 3;
    int pathStart = namespace.indexOf('/', authorityStart);
    String scheme = namespace.substring(0, authorityStart);
    String authority = pathStart < 0 ? namespace.substring(authorityStart)
                                     : namespace.substring(authorityStart, pathStart);
    String path = pathStart < 0 ? "" : namespace.substring(pathStart);

    if (authority.indexOf(',') < 0) {
      return namespace;
    }
    String joined = Arrays.stream(authority.split(","))
        .map(String::trim)
        .filter(s -> !s.isEmpty())
        .sorted()
        .collect(Collectors.joining(separator));
    return scheme + joined + path;
  }
}
