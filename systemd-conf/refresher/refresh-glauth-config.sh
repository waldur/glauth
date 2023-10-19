#!/bin/sh
set -e
# Requires:
# WALDUR_URL
# WALDUR_TOKEN
# WALDUR_OFFERING_UUID
# WALDUR_API_VERIFY_TLS
# LDAP_ADMIN_USERNAME
# LDAP_ADMIN_PASSWORD
# LDAP_ADMIN_UIDNUMBER
# LDAP_ADMIN_PGROUP

VERIFY_TLS="${WALDUR_API_VERIFY_TLS:-false}"

if [ $VERIFY_TLS = "false" ]; then
  export NO_CHECK_CERTIFICATE="--no-check-certificate"
fi

while true; do

  export | grep -Ei "waldur|ldap" | grep -Evi "token|password"

  echo "[+] Fetching users config file"
  # Creating an empty file to handle a case when a response is empty
  touch /tmp/offering-users-config.cfg

  wget $NO_CHECK_CERTIFICATE --header="Authorization: Token $WALDUR_TOKEN" \
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
    envsubst < /etc/glauth/preconfig.cfg.template > /etc/glauth/preconfig.cfg

    echo "[+] Merging preconfig with users config"
    cat /etc/glauth/preconfig.cfg /tmp/offering-users-config.cfg > /etc/glauth/config.cfg

    echo "[+] Cleanup"
    mv /tmp/offering-users-config.cfg /tmp/prev-offering-users-config.cfg
    rm /etc/glauth/preconfig.cfg
  fi
  sleep 300 # sleep for 5 minutes
done
