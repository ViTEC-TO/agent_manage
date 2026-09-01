#!/bin/sh
set -eu

python3 -m agent_manage.seed \
  --seed /opt/unitag/openclaw-seed \
  --target /home/node/.openclaw

exec "$@"
