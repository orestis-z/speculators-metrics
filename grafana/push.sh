#!/bin/sh
# Upload the generated dashboards to a Grafana (overwrites dashboards with the same uid).
# usage: grafana/push.sh <grafana-base-url> [extra curl args, e.g. -H "Cookie: ..." --socks5-hostname localhost:1080]
set -e
G=$1; shift
DIR=$(cd "$(dirname "$0")" && pwd)/dashboards
for f in "$DIR"/*.json; do
  python3 -c "import json,sys;print(json.dumps({'dashboard':json.load(open(sys.argv[1])),'overwrite':True,'message':'grafana/gen_dashboards.py'}))" "$f" \
    | curl -s -m 60 "$@" -X POST "$G/api/dashboards/db" -H 'Content-Type: application/json' --data @-
  echo
done
