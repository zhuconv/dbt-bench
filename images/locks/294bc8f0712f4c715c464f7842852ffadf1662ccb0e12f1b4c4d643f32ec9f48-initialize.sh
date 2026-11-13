#!/bin/sh
# Copyright (c) Contributors to the Apptainer project, established as
#   Apptainer a Series of LF Projects LLC.
#   For website terms of use, trademark policy, privacy policy and other
#   project policies see https://lfprojects.org/policies
# Copyright (c) 2018-2021, Sylabs Inc. All rights reserved.
# This software is licensed under a 3-clause BSD license. Please consult
# https://github.com/apptainer/apptainer/blob/main/LICENSE.md regarding your
# rights to use or distribute this software.

# Custom environment shell code should follow

    # Reproduce the native image's DuckDB ENTRYPOINT in each fresh container /tmp.
    # The explicit command used by Harbor's `apptainer exec` bypasses the OCI entrypoint.
    if [ "${DB_TYPE:-duckdb}" != duckdb ]; then
        echo "This task image supports only the reviewed DuckDB backend" >&2
        exit 1
    fi
    /entrypoint.sh /bin/true >/dev/null
    # dbt deps removes/recreates this tree; instantiate it in the fresh writable layer.
    cp -a /opt/dbt-bench-initial/dbt_packages /app/dbt_models_duckdb/dbt_packages

