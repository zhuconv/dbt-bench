#!/bin/sh

    for folder in dbt_packages target logs; do
        if [ -d "/opt/dbt-bench-initial/$folder" ] && [ ! -e "/workspace/$folder" ]; then
            cp -a "/opt/dbt-bench-initial/$folder" "/workspace/$folder"
        fi
    done


