package io.dapp.openlineage.resolver;

import org.junit.jupiter.api.Test;

import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class NamespaceNormalizerTest {

  private static final Pattern MARQUEZ = Pattern.compile("^[a-zA-Z0-9_@+:;=/.-]{1,1024}$");
  private final NamespaceNormalizer normalizer = new NamespaceNormalizer(NamespaceNormalizer.DEFAULT_SEPARATOR);

  @Test
  void collapsesMultiHostAuthorityWithLegalSeparator() {
    assertEquals("postgres://pg1:5432+pg2:5432",
        normalizer.resolve("postgres://pg1:5432,pg2:5432"));
  }

  @Test
  void isOrderIndependent() {
    assertEquals(normalizer.resolve("postgres://pg1:5432,pg2:5432"),
        normalizer.resolve("postgres://pg2:5432,pg1:5432"));
  }

  @Test
  void collapsesKafkaBootstrapList() {
    assertEquals("kafka://b1:9092+b2:9092+b3:9092",
        normalizer.resolve("kafka://b2:9092,b1:9092,b3:9092"));
  }

  @Test
  void leavesSingleHostUnchanged() {
    assertEquals("postgres://pg1:5432", normalizer.resolve("postgres://pg1:5432"));
  }

  @Test
  void leavesPathBearingUrlUnchanged() {
    assertEquals("hdfs://namenode:9000/user/hive/warehouse",
        normalizer.resolve("hdfs://namenode:9000/user/hive/warehouse"));
    assertEquals("s3://my-bucket", normalizer.resolve("s3://my-bucket"));
  }

  @Test
  void sanitizesIllegalCharacters() {
    // скобки Oracle TNS и знак вопроса не входят в charset Marquez
    String out = normalizer.resolve("oracle://(DESCRIPTION=(HOST=h1))?x=1");
    assertTrue(MARQUEZ.matcher(out).matches(), "должно матчить Marquez-regex: " + out);
    assertEquals(-1, out.indexOf('('));
    assertEquals(-1, out.indexOf('?'));
  }

  @Test
  void isIdempotent() {
    String once = normalizer.resolve("postgres://pg2:5432,pg1:5432");
    assertEquals(once, normalizer.resolve(once));
  }

  @Test
  void outputAlwaysMatchesMarquezRegex() {
    String[] inputs = {
        "postgres://pg1:5432,pg2:5432",
        "kafka://b2:9092,b1:9092,b3:9092",
        "oracle://(DESCRIPTION=(ADDRESS=(HOST=scan)))",
        "sqlserver://h1:1433;databaseName=db",
        "hdfs://namenode:9000/user/hive/warehouse",
        "jdbc:weird space&sym,bols?here"
    };
    for (String in : inputs) {
      String out = normalizer.resolve(in);
      assertTrue(MARQUEZ.matcher(out).matches(), "не матчит Marquez-regex: " + in + " -> " + out);
    }
  }

  @Test
  void returnsNullAndEmptyUnchanged() {
    assertEquals(null, normalizer.resolve(null));
    assertEquals("", normalizer.resolve(""));
  }

  @Test
  void defaultsSeparatorWhenNullOrEmpty() {
    assertEquals("postgres://pg1:5432+pg2:5432",
        new NamespaceNormalizer(null).resolve("postgres://pg1:5432,pg2:5432"));
    assertEquals("postgres://pg1:5432+pg2:5432",
        new NamespaceNormalizer("").resolve("postgres://pg1:5432,pg2:5432"));
  }
}
