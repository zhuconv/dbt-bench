#!/bin/sh

    for folder in dbt_packages target logs; do
        if [ -d "/opt/dbt-bench-initial/$folder" ]; then
            cp -a "/opt/dbt-bench-initial/$folder" "/app/$folder"
        fi
    done

