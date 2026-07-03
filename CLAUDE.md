# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Waldur deployment packaging for [GLAuth](https://github.com/glauth/glauth) (an LDAP server). It is deliberately **not a fork**: it consumes official upstream release binaries at a pinned version and contains no Go code. Do not vendor or patch GLAuth source here — upstream changes are adopted by bumping `GLAUTH_VERSION` in the `Dockerfile` (tag format `GLAuth-vX.Y.Z`, see upstream releases) and updating the matching version in `docs/systemd-instructions.md`.

## Architecture

The core is a config refresher (`refresher/refresh-glauth-config.py`) that fetches a TOML users config from the Waldur API (via `waldur-api-client`, endpoint `marketplace-provider-offerings/{UUID}/glauth_users_config/`), renders `refresher/preconfig.cfg.template` with Python `string.Template` (server/backend/admin settings), and merges the two at the TOML level (parse both, concatenate the `users`/`groups` collections, re-serialize) into GLAuth's config file. `WALDUR_OFFERING_UUID` may be a single UUID or a comma-separated list — the exports of all listed offerings are merged into one directory (one agent identity + event-subscription queue per offering), with first-wins de-duplication on any uid/gid/name collision since each offering allocates its ranges independently. Merging as text is unsafe — the API renders `groups` as a leading top-level inline array, which a raw concatenation would reparent under the preconfig's trailing `[[groups]]` table and silently drop. It refreshes on startup, on `offering_user` events received over STOMP-over-WebSocket (it registers an agent identity + event subscription in Waldur), and every 5 minutes as a fallback. GLAuth hot-reloads the file via `watchconfig` — but note watchconfig does NOT reload the `[ldap]`, `[ldaps]`, `[backend]` or `[api]` sections, so changes there require a restart.

The same refresher script serves two deployment modes, switched purely by environment variables (`GLAUTH_TEMPLATE_PATH`, `GLAUTH_OUTPUT_CONFIG_PATH`):

- **Docker** (`Dockerfile` + `docker/start.sh`): a `python:3.12-slim` image where `start.sh` backgrounds the refresher, waits up to 60s for the first config, then runs glauth. Defaults are overridden via `ENV` to paths under `/app`.
- **systemd** (`systemd/` + `docs/systemd-instructions.md`): two units on a host, using the script's built-in `/etc/glauth` defaults; refresher dependencies come from `refresher/requirements.txt`.

Keep the script's defaults pointing at the systemd paths; the Docker image overrides them.

## Constraints that are easy to break

- `string.Template.substitute` is strict: any stray `$` in `refresher/preconfig.cfg.template` (even in a comment) crashes the refresher with a KeyError/ValueError. Escape literal dollars as `$$` or avoid them.
- The refresher requires `WALDUR_API_VERIFY_TLS` to be set (no default) — the Dockerfile sets it via `ENV`, systemd installs get it from `refresher.env`.
- The upstream release binary (`glauth-linux-amd64`) is glibc-linked and amd64-only — keep the run image Debian-based (the `glauth --version` smoke test in the Dockerfile catches a libc mismatch).

## Build and test

```bash
# Build (the --platform flag is required on ARM hosts; CI runners are amd64)
docker build --platform linux/amd64 -t glauth-waldur-test .
```

End-to-end testing needs a real Waldur instance (token + offering UUID): besides fetching the users config, the refresher registers an agent identity, event subscription and RabbitMQ queue, and opens a STOMP WebSocket — a static mock API is not enough.

```bash
docker run -d --rm --name glauth-e2e --platform linux/amd64 \
  -e WALDUR_URL=https://waldur.example.com/api/ \
  -e WALDUR_OFFERING_UUID=... -e WALDUR_TOKEN=... \
  -p 13893:3893 glauth-waldur-test

# Verify bind + search returns both the templated admin and the fetched users
ldapsearch -H ldap://localhost:13893 \
  -D "cn=serviceuser,ou=svcaccts,dc=glauth,dc=com" -w passw0rd \
  -b "dc=glauth,dc=com" "(objectclass=posixAccount)" cn uidNumber
```

Syntax-check changes with `python3 -m py_compile refresher/refresh-glauth-config.py` and `sh -n docker/start.sh`.

## CI

GitLab CI inherits image publishing entirely from `waldur/waldur-pipelines` `templates/release/publish.yml` (buildah, root `Dockerfile`, pushes `opennode/${CI_PROJECT_NAME}` — `:latest` on the default branch, `:$CI_COMMIT_TAG` on tags). Do not add per-repo build script overrides; the `ARG DOCKER_REGISTRY` in the Dockerfile exists so CI pulls Docker Hub base images through `registry.hpc.ut.ee/mirror/`.
