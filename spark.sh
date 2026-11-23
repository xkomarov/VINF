# so ../enwiki-latest-pages-articles.xml.bz2 resolves the same way as on the host.
#   ./spark.sh spark-submit --master "local[*]" --driver-memory 4g --py-files wiki_regex.py,spark_common.py join.py out/extracted out/wiki out/joined
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE=f1-spark
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" -f "$HERE/docker/spark.Dockerfile" "$HERE/docker"
TTY=$([ -t 0 ] && echo "-it" || echo "-i")
MSYS_NO_PATHCONV=1 docker run --rm $TTY -v "$HERE/..:/app" -w /app/formula1 -p 4040:4040 "$IMAGE" "$@"
