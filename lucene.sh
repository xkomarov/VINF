#   ./lucene.sh python lucene_indexer.py
#   ./lucene.sh python search.py "winner:senna AND season:[1985 TO 1990]"
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE=f1-lucene
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" "$HERE/docker"
TTY=$([ -t 0 ] && echo "-it" || echo "-i")
MSYS_NO_PATHCONV=1 docker run --rm $TTY -v "$HERE:/app/formula1" -w /app/formula1 "$IMAGE" "$@"
