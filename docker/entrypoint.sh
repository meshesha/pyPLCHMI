#!/bin/sh
# Container start script of pyPLCHMI.
#
# 1. Checks that SECRET_KEY is set and is not the placeholder of .env.example.
# 2. Brings the database up to date (flask db upgrade) and creates the default
#    roles and permissions (flask seed) – both are safe to run on every start.
# 3. Optionally creates the first admin user (HMI_ADMIN_USER / HMI_ADMIN_PASSWORD;
#    an existing user is left unchanged) and the demo screen (HMI_DEMO=1).
# 4. Starts the command given to the container (waitress by default).
#
# Set HMI_SKIP_MIGRATIONS=1 to skip steps 2 and 3 (e.g. when another job migrates).
set -e

if [ "${FLASK_CONFIG}" = "production" ] && { [ -z "${SECRET_KEY}" ] || [ "${SECRET_KEY}" = "change-me-to-a-long-random-string" ]; }; then
    echo "ERROR: set SECRET_KEY (a long random string), e.g. in the .env file." >&2
    echo "       python -c \"import secrets; print(secrets.token_hex(32))\"" >&2
    exit 1
fi

mkdir -p /app/instance

if [ "${HMI_SKIP_MIGRATIONS:-0}" != "1" ]; then
    echo "pyPLCHMI: updating the database..."
    flask db upgrade
    flask seed
    if [ -n "${HMI_ADMIN_USER}" ] && [ -n "${HMI_ADMIN_PASSWORD}" ]; then
        # the password is read from HMI_ADMIN_PASSWORD (never on the command line)
        flask create-admin --username "${HMI_ADMIN_USER}" --if-missing
    fi
    if [ "${HMI_DEMO:-0}" = "1" ]; then
        flask demo
    fi
fi

echo "pyPLCHMI: starting on port ${HMI_PORT:-8000}"
exec "$@"
