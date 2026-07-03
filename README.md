# waldur-glauth

Waldur deployment packaging for [GLAuth](https://github.com/glauth/glauth),
a lightweight LDAP server. This repository does **not** fork or modify GLAuth —
it consumes official upstream release binaries at a pinned version and adds
the Waldur integration around them:

- a **config refresher** that fetches the users config for one or more
  marketplace offerings from the Waldur API and merges it with a local
  preconfig template into the GLAuth config (GLAuth hot-reloads it via
  `watchconfig`). It subscribes to `offering_user` events via
  STOMP-over-WebSocket so changes propagate immediately, with a periodic
  refresh as a fallback. A single instance can serve several offerings at once
  by setting `WALDUR_OFFERING_UUID` to a comma-separated list,
- a **Docker image** (`opennode/glauth`) bundling GLAuth + the refresher,
- **systemd units** for running GLAuth + the refresher directly on a host.

## Repository layout

```
Dockerfile                  Docker image: pinned GLAuth release + refresher
docker/start.sh             Container entrypoint (refresher + glauth)
refresher/
  refresh-glauth-config.py  The refresher (shared by Docker and systemd)
  requirements.txt          Python dependencies of the refresher
  preconfig.cfg.template    GLAuth server/backend/admin preconfig (string.Template)
systemd/
  glauth.service            GLAuth unit
  refresh-glauth-config.service
  refresher.env.example     Environment file template for the refresher
docs/systemd-instructions.md
```

## Bumping the GLAuth version

The version is pinned in exactly one place — the `GLAUTH_VERSION` build arg
at the top of the [Dockerfile](Dockerfile) (systemd installs reference the
same version in [docs/systemd-instructions.md](docs/systemd-instructions.md)).
To upgrade:

1. Change `GLAUTH_VERSION` to the new upstream release tag
   (see [releases](https://github.com/glauth/glauth/releases)).
2. Build and smoke-test:

   ```bash
   docker build -t glauth-waldur-test .
   docker run --rm -e WALDUR_URL=... -e WALDUR_TOKEN=... \
     -e WALDUR_OFFERING_UUID=... -p 3893:3893 glauth-waldur-test
   ldapsearch -H ldap://localhost:3893 -D "cn=admin,ou=svcaccts,dc=glauth,dc=com" -w ... -b "dc=glauth,dc=com"
   ```

3. Check the upstream [CHANGELOG](https://github.com/glauth/glauth/blob/master/v2/CHANGELOG.md)
   for config-format changes affecting `refresher/preconfig.cfg.template`.

## Configuration

The refresher and GLAuth are configured via environment variables — see
[systemd/refresher.env.example](systemd/refresher.env.example) and the
docstring of
[refresher/refresh-glauth-config.py](refresher/refresh-glauth-config.py).
The users config comes from the
`marketplace-provider-offerings/{WALDUR_OFFERING_UUID}/glauth_users_config/`
endpoint of the Waldur API. `WALDUR_OFFERING_UUID` may be a single offering
UUID or a comma-separated list; the exports of all listed offerings are merged
into one directory. Because each offering allocates its uid/gid ranges
independently, the operator must keep those ranges from overlapping — on a
uid/gid/name collision the first offering wins and the colliding record is
skipped with a warning.
