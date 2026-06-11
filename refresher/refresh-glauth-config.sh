#!/bin/sh
set -e
# Periodically fetches the users config for a Waldur marketplace offering and
# merges it with the local preconfig template into the GLAuth config file.
# GLAuth picks up the change via watchconfig.
#
# Required environment:
#   WALDUR_URL              Waldur API base URL, with trailing slash
#   WALDUR_TOKEN            Waldur API token
#   WALDUR_OFFERING_UUID    Marketplace offering UUID
#   LDAP_ADMIN_USERNAME, LDAP_ADMIN_PASSWORD, LDAP_ADMIN_EMAIL
#   LDAP_ADMIN_UIDNUMBER, LDAP_ADMIN_PGROUP
# Optional:
#   WALDUR_API_VERIFY_TLS   default: false
#   PRECONFIG_TEMPLATE      default: /etc/glauth/preconfig.cfg.template
#   CONFIG_OUT              default: /etc/glauth/config.cfg
#   REFRESH_PERIOD          seconds between refreshes, default: 300

PRECONFIG_TEMPLATE="${PRECONFIG_TEMPLATE:-/etc/glauth/preconfig.cfg.template}"
CONFIG_OUT="${CONFIG_OUT:-/etc/glauth/config.cfg}"
REFRESH_PERIOD="${REFRESH_PERIOD:-300}"
VERIFY_TLS="${WALDUR_API_VERIFY_TLS:-false}"

if [ "$VERIFY_TLS" = "false" ]; then
  NO_CHECK_CERTIFICATE="--no-check-certificate"
fi

while true; do

  export | grep -Ei "waldur|ldap" | grep -Evi "token|password"

  echo "[+] Fetching users config file"
  # Creating an empty file to handle a case when a response is empty
  touch /tmp/offering-users-config.cfg

  if ! wget $NO_CHECK_CERTIFICATE --header="Authorization: Token $WALDUR_TOKEN" \
      "${WALDUR_URL}marketplace-provider-offerings/$WALDUR_OFFERING_UUID/glauth_users_config/" \
      -O /tmp/response.txt --server-response; then
    echo "Error during config file fetch:"
    cat /tmp/response.txt
    sleep "$REFRESH_PERIOD"
    continue
  fi

  mv /tmp/response.txt /tmp/offering-users-config.cfg
  DIFF=true
  if [ -f /tmp/prev-offering-users-config.cfg ]; then
    echo "[+] Executing diff with previous users config file"
    diff /tmp/prev-offering-users-config.cfg /tmp/offering-users-config.cfg \
      && echo "[+] There are no changes in the new glauth config, skipping merge" \
      && DIFF=false
  else
    echo "[+] Previous user config file is missing, skipping diff"
  fi

  if [ $DIFF = true ]; then
    echo "[+] Generating a digest for LDAP_ADMIN_PASSWORD"
    LDAP_ADMIN_PASSWORD_DIGEST=$(printf '%s' "$LDAP_ADMIN_PASSWORD" | sha256sum | cut -d' ' -f1)
    export LDAP_ADMIN_PASSWORD_DIGEST

    echo "[+] Templating preconfig file"
    envsubst < "$PRECONFIG_TEMPLATE" > /tmp/preconfig.cfg

    echo "[+] Merging preconfig with users config"
    cat /tmp/preconfig.cfg /tmp/offering-users-config.cfg > "$CONFIG_OUT"

    echo "[+] Cleanup"
    mv /tmp/offering-users-config.cfg /tmp/prev-offering-users-config.cfg
    rm /tmp/preconfig.cfg
  fi
  sleep "$REFRESH_PERIOD"
done
