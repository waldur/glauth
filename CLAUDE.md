# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Waldur deployment packaging for [GLAuth](https://github.com/glauth/glauth) (an LDAP server). It is deliberately **not a fork**: it consumes official upstream release binaries at a pinned version and contains no Go code. Do not vendor or patch GLAuth source here — upstream changes are adopted by bumping `GLAUTH_VERSION` in the `Dockerfile` (tag format `GLAuth-vX.Y.Z`, see upstream releases) and updating the matching version in `docs/systemd-instructions.md`.

## Architecture

The core is a config-refresh loop (`refresher/refresh-glauth-config.sh`) that periodically fetches a TOML users config from the Waldur API (`{WALDUR_URL}marketplace-provider-offerings/{UUID}/glauth_users_config/`), renders `refresher/preconfig.cfg.template` with `envsubst` (server/backend/admin settings), and concatenates the two into GLAuth's config file. GLAuth hot-reloads it via `watchconfig` — but note watchconfig does NOT reload the `[ldap]`, `[ldaps]`, `[backend]` or `[api]` sections, so changes there require a restart.

The same refresher script serves two deployment modes, switched purely by environment variables (`PRECONFIG_TEMPLATE`, `CONFIG_OUT`, `REFRESH_PERIOD`):

- **Docker** (`Dockerfile` + `docker/start.sh`): a distroless image where `start.sh` backgrounds the refresher, waits up to 60s for the first config, then execs glauth. Defaults are overridden via `ENV` to paths under `/app`.
- **systemd** (`systemd/` + `docs/systemd-instructions.md`): two units on a host, using the script's built-in `/etc/glauth` defaults.

Keep the script's defaults pointing at the systemd paths; the Docker image overrides them.

## Constraints that are easy to break

- The runtime image is distroless: the only shell utilities are the busybox symlinks created in the `Dockerfile`'s `RUN busybox.static ln -s ...` block. If a script starts using a new command, add it to that list, and keep scripts POSIX/busybox-sh compatible (no bashisms).
- The upstream release binary (`glauth-linux-amd64`) is glibc-linked: it cannot execute in the musl/Alpine fetch stage (that's why the `glauth --version` smoke test lives in the run stage) and the image is amd64-only.
- `wget`/`envsubst` in the runtime image are the full Alpine binaries plus copied musl libs (`/usr/lib/ → /lib/`, the musl loader, CA certs) — busybox's wget is too limited for the authenticated TLS fetch.

## Build and test

```bash
# Build (the --platform flag is required on ARM hosts; CI runners are amd64)
docker build --platform linux/amd64 -t glauth-waldur-test .
```

End-to-end smoke test against a mock Waldur API (no real credentials needed):

```bash
# 1. Serve a fake users config — python http.server serves index.html for the directory URL
mkdir -p /tmp/mock-waldur/marketplace-provider-offerings/test-uuid/glauth_users_config
printf '[[users]]\n  name = "alice"\n  uidnumber = 6001\n  primarygroup = 6500\n  passsha256 = "%064d"\n\n[[groups]]\n  name = "users"\n  gidnumber = 6500\n' 0 \
  > /tmp/mock-waldur/marketplace-provider-offerings/test-uuid/glauth_users_config/index.html
(cd /tmp/mock-waldur && python3 -m http.server 8099 &)

# 2. Run the image against it
docker run -d --rm --name glauth-e2e --platform linux/amd64 \
  -e WALDUR_URL=http://host.docker.internal:8099/ \
  -e WALDUR_OFFERING_UUID=test-uuid -e WALDUR_TOKEN=dummy \
  -p 13893:3893 glauth-waldur-test

# 3. Verify bind + search returns both the templated admin and the fetched user
ldapsearch -H ldap://localhost:13893 \
  -D "cn=serviceuser,ou=svcaccts,dc=glauth,dc=com" -w passw0rd \
  -b "dc=glauth,dc=com" "(objectclass=posixAccount)" cn uidNumber
```

Syntax-check shell changes with `sh -n refresher/refresh-glauth-config.sh docker/start.sh`.

## CI

GitLab CI inherits image publishing entirely from `waldur/waldur-pipelines` `templates/release/publish.yml` (buildah, root `Dockerfile`, pushes `opennode/${CI_PROJECT_NAME}` — `:latest` on master, `:$CI_COMMIT_TAG` on tags). Do not add per-repo build script overrides; the `ARG DOCKER_REGISTRY` in the Dockerfile exists so CI pulls Docker Hub base images through `registry.hpc.ut.ee/mirror/`.
