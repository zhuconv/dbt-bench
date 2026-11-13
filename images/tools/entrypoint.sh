#!/bin/sh
set -eu
. /opt/dbt-bench-initialize.sh
exec "$@"
