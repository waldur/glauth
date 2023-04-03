#!/bin/sh
set -e
# Requires:
# WALDUR_URL
# WALDUR_TOKEN
# WALDUR_OFFERING_UUID
# LDAP_ADMIN_USERNAME
# LDAP_ADMIN_PASSWORD

while true; do
  # Clean up the log file from the previous run
  if [ -f /var/log/refresh-glauth-config.log ]; then
    echo "" > /var/log/refresh-glauth-config.log
  fi

  export | grep -Ei "waldur|ldap" | grep -Evi "token|password"

  echo "[+] Fetching users config file"
  # Creating an empty file to handle a case when a response is empty
  touch /tmp/offering-users-config.cfg
  wget --no-check-certificate --header="Authorization: Token $WALDUR_TOKEN" \
    ${WALDUR_URL}marketplace-provider-offerings/$WALDUR_OFFERING_UUID/glauth_users_config/ \
    -O /tmp/offering-users-config.cfg

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
    export LDAP_ADMIN_PASSWORD_DIGEST=$(echo -n "$LDAP_ADMIN_PASSWORD" | openssl dgst -sha256 -binary | xxd -p -c 256)

    echo "[+] Templating preconfig file"
    envsubst < /app/docker/preconfig.cfg.template > /app/docker/preconfig.cfg

    echo "[+] Merging preconfig file with users config one"
    cat /app/docker/preconfig.cfg /tmp/offering-users-config.cfg > /app/config/config.cfg

    echo "[+] Cleanup"
    mv /tmp/offering-users-config.cfg /tmp/prev-offering-users-config.cfg
    rm /app/docker/preconfig.cfg
  fi
  sleep 300 # sleep for 5 minutes
done
