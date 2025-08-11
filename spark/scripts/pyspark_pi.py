from __future__ import print_function
import sys
from pyspark.sql import SparkSession


def compute_pi(num_samples: int) -> float:
    spark = SparkSession.builder.appName("PySparkPi").getOrCreate()
    sc = spark.sparkContext

    import random

    def inside(_: int) -> int:
        x, y = random.random(), random.random()
        return 1 if x * x + y * y <= 1 else 0

    count = sc.parallelize(range(0, num_samples), numSlices=2).map(inside).reduce(lambda a, b: a + b)
    pi = 4.0 * count / float(num_samples)
    print("Pi is roughly %f" % pi)

    spark.stop()
    return pi


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    compute_pi(n)
