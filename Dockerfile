# GLAuth upstream release tag, the single place to bump the version.
ARG GLAUTH_VERSION=GLAuth-v2.5.0
# CI passes registry.hpc.ut.ee/mirror/ to pull base images through the mirror
ARG DOCKER_REGISTRY=docker.io/

# Debian-based (glibc), so the upstream release binary runs natively and the
# Python refresher has an interpreter.
FROM ${DOCKER_REGISTRY}python:3.12-slim AS run

ARG GLAUTH_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends dumb-init wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app \
    && wget -O /app/glauth \
      "https://github.com/glauth/glauth/releases/download/${GLAUTH_VERSION}/glauth-linux-amd64" \
    && chmod +x /app/glauth

# Smoke-test the fetched binary
RUN ["/app/glauth", "--version"]

COPY refresher/requirements.txt /app/refresher/requirements.txt
RUN pip install --no-cache-dir -r /app/refresher/requirements.txt

COPY refresher/preconfig.cfg.template /app/refresher/preconfig.cfg.template
COPY refresher/refresh-glauth-config.py /app/refresher/refresh-glauth-config.py
COPY docker/start.sh /app/start.sh

# Expose web and LDAP ports
EXPOSE 389 636 5555

ENV WALDUR_URL="changeme"
ENV WALDUR_TOKEN="changeme"
ENV WALDUR_OFFERING_UUID="changeme"
ENV WALDUR_API_VERIFY_TLS="false"
ENV LDAP_ADMIN_USERNAME="serviceuser"
ENV LDAP_ADMIN_PASSWORD="passw0rd"
ENV LDAP_ADMIN_UIDNUMBER="5003"
ENV LDAP_ADMIN_EMAIL="serviceuser@example.com"
ENV LDAP_ADMIN_PGROUP="5502"
# Refresher paths inside the container (defaults in the script target systemd hosts)
ENV GLAUTH_TEMPLATE_PATH="/app/refresher/preconfig.cfg.template"
ENV GLAUTH_OUTPUT_CONFIG_PATH="/app/config/config.cfg"

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/bin/sh", "/app/start.sh"]
