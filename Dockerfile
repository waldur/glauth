#################
# Fetch Step
#################

# GLAuth upstream release tag, the single place to bump the version.
ARG GLAUTH_VERSION=GLAuth-v2.5.0

FROM alpine:3.21 AS fetch

ARG GLAUTH_VERSION

RUN apk add --no-cache busybox-static dumb-init gettext wget ca-certificates

# Note: the release binary is linked against glibc, so it cannot be executed
# in this musl-based stage; it is smoke-tested in the run stage below.
RUN wget -O /glauth \
      "https://github.com/glauth/glauth/releases/download/${GLAUTH_VERSION}/glauth-linux-amd64" \
    && chmod +x /glauth

#################
# Run Step
#################

FROM gcr.io/distroless/base-debian12 AS run

# Just what we need
COPY --from=fetch /glauth /app/glauth
COPY --from=fetch /usr/bin/dumb-init /usr/bin/dumb-init
COPY --from=fetch /bin/busybox.static /bin/busybox.static
COPY --from=fetch /bin/busybox.static /bin/sh

RUN busybox.static ln -s /bin/busybox.static /bin/mv \
    && busybox.static ln -s /bin/busybox.static /bin/mkdir \
    && busybox.static ln -s /bin/busybox.static /bin/cp \
    && busybox.static ln -s /bin/busybox.static /bin/rm \
    && busybox.static ln -s /bin/busybox.static /bin/ls \
    && busybox.static ln -s /bin/busybox.static /bin/cat \
    && busybox.static ln -s /bin/busybox.static /bin/grep \
    && busybox.static ln -s /bin/busybox.static /bin/sleep \
    && busybox.static ln -s /bin/busybox.static /bin/vi \
    && busybox.static ln -s /bin/busybox.static /bin/diff \
    && busybox.static ln -s /bin/busybox.static /bin/awk \
    && busybox.static ln -s /bin/busybox.static /bin/touch \
    && busybox.static ln -s /bin/busybox.static /bin/sha256sum \
    && busybox.static ln -s /bin/busybox.static /bin/cut

# Busybox's wget is limited, using the standard one
COPY --from=fetch /usr/bin/wget /bin/wget
COPY --from=fetch /usr/lib/ /lib/
# envsubst
COPY --from=fetch /usr/bin/envsubst /bin/envsubst
# Required by envsubst and wget
COPY --from=fetch /lib/ld-musl-x86_64.so.1 /lib/ld-musl-x86_64.so.1
COPY --from=fetch /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# Smoke-test that the fetched binary runs against this image's libc
RUN ["/app/glauth", "--version"]

COPY refresher/preconfig.cfg.template /app/refresher/preconfig.cfg.template
COPY refresher/refresh-glauth-config.sh /app/refresher/refresh-glauth-config.sh
COPY docker/start.sh /app/start.sh

# Expose web and LDAP ports
EXPOSE 389 636 5555

ENV WALDUR_URL="changeme"
ENV WALDUR_TOKEN="changeme"
ENV WALDUR_OFFERING_UUID="changeme"
ENV LDAP_ADMIN_USERNAME="serviceuser"
ENV LDAP_ADMIN_PASSWORD="passw0rd"
ENV LDAP_ADMIN_UIDNUMBER="5003"
ENV LDAP_ADMIN_EMAIL="serviceuser@example.com"
ENV LDAP_ADMIN_PGROUP="5502"
# Refresher paths inside the container (defaults in the script target systemd hosts)
ENV PRECONFIG_TEMPLATE="/app/refresher/preconfig.cfg.template"
ENV CONFIG_OUT="/app/config/config.cfg"

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/bin/sh", "/app/start.sh"]
