#!/bin/sh
set -eu

# Escape special chars in env vars for safe sed substitution
escape() {
    printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

# Render template with env vars
sed -e "s|\${PAGERDUTY_SERVICE_KEY}|$(escape "${PAGERDUTY_SERVICE_KEY}")|g" \
    -e "s|\${SLACK_WEBHOOK_URL}|$(escape "${SLACK_WEBHOOK_URL}")|g" \
    /etc/alertmanager/alertmanager.yml.tmpl > /tmp/alertmanager.yml

# Start Alertmanager with rendered config
exec /bin/alertmanager --config.file=/tmp/alertmanager.yml --storage.path=/alertmanager