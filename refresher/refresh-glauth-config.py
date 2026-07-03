#!/usr/bin/env python3
"""GLAuth Configuration Refresher.

Subscribes to offering_user events from Waldur via STOMP-over-WebSocket
and refreshes the GLAuth config file whenever a change is detected.
Also performs an initial config fetch on startup.

A single GLAuth instance can expose the offering users of several offerings
at once: set WALDUR_OFFERING_UUID to a comma-separated list of offering UUIDs.
Their exports are merged into one directory; on a uid/gid/name collision the
first occurrence wins and the rest are skipped with a warning, so operators
must keep the per-offering uid/gid ranges from overlapping.

Environment Variables Required:
    WALDUR_URL
    WALDUR_TOKEN
    WALDUR_OFFERING_UUID     (single UUID, or a comma-separated list of UUIDs)
    WALDUR_API_VERIFY_TLS
    LDAP_ADMIN_USERNAME
    LDAP_ADMIN_PASSWORD
    LDAP_ADMIN_EMAIL
    LDAP_ADMIN_UIDNUMBER
    LDAP_ADMIN_PGROUP

Optional Environment Variables:
    GLAUTH_TEMPLATE_PATH      (defaults to /etc/glauth/preconfig.cfg.template)
    GLAUTH_OUTPUT_CONFIG_PATH (defaults to /etc/glauth/config.cfg)
    GLAUTH_REFRESH_PERIOD     (periodic refresh interval in seconds, defaults to 300)
    WALDUR_STOMP_WS_HOST      (defaults to host from WALDUR_URL)
    WALDUR_STOMP_WS_PORT      (defaults to 443)
    WALDUR_STOMP_WS_PATH      (defaults to /rmqws-stomp)
    WALDUR_WEBSOCKET_USE_TLS  (defaults to true)
"""

import datetime
import hashlib
import logging
import os
import sys
import time
from string import Template
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

import stomp
import tomli_w
from stomp.exception import StompException
from waldur_api_client import AuthenticatedClient
from waldur_api_client.api.event_subscriptions import event_subscriptions_create_queue
from waldur_api_client.api.marketplace_provider_offerings import (
    marketplace_provider_offerings_glauth_users_config_retrieve,
)
from waldur_api_client.api.marketplace_site_agent_identities import (
    marketplace_site_agent_identities_create,
    marketplace_site_agent_identities_list,
    marketplace_site_agent_identities_register_event_subscription,
    marketplace_site_agent_identities_register_service,
    marketplace_site_agent_identities_update,
)
from waldur_api_client.api.marketplace_site_agent_services import (
    marketplace_site_agent_services_register_processor,
)
from waldur_api_client.models import (
    AgentIdentity,
    AgentIdentityRequest,
    AgentProcessorCreateRequest,
    EventSubscriptionQueueCreateRequest,
)
from waldur_api_client.models.agent_event_subscription_create_request import (
    AgentEventSubscriptionCreateRequest,
)
from waldur_api_client.models.agent_service import AgentService
from waldur_api_client.models.agent_service_create_request import (
    AgentServiceCreateRequest,
)
from waldur_api_client.models.observable_object_type_enum import (
    ObservableObjectTypeEnum,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TEMPLATE_PATH = os.environ.get(
    "GLAUTH_TEMPLATE_PATH", "/etc/glauth/preconfig.cfg.template"
)
OUTPUT_CONFIG_PATH = os.environ.get(
    "GLAUTH_OUTPUT_CONFIG_PATH", "/etc/glauth/config.cfg"
)
AGENT_VERSION = "1.0.0"
# Periodic config refresh interval in seconds (fallback for missed events)
REFRESH_PERIOD = int(os.environ.get("GLAUTH_REFRESH_PERIOD", "300"))

SENSITIVE_KEYS = {"WALDUR_TOKEN", "LDAP_ADMIN_PASSWORD"}

REQUIRED_ENV_VARS = [
    "WALDUR_URL",
    "WALDUR_TOKEN",
    "WALDUR_OFFERING_UUID",
    "WALDUR_API_VERIFY_TLS",
    "LDAP_ADMIN_USERNAME",
    "LDAP_ADMIN_PASSWORD",
    "LDAP_ADMIN_EMAIL",
    "LDAP_ADMIN_UIDNUMBER",
    "LDAP_ADMIN_PGROUP",
]

OPTIONAL_ENV_VARS = [
    "WALDUR_STOMP_WS_HOST",
    "WALDUR_STOMP_WS_PORT",
    "WALDUR_STOMP_WS_PATH",
    "WALDUR_WEBSOCKET_USE_TLS",
]


def read_config():
    config = {}
    missing = []
    for var in REQUIRED_ENV_VARS:
        value = os.environ.get(var)
        if value is None:
            missing.append(var)
        else:
            config[var] = value
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        sys.exit(1)
    for var in OPTIONAL_ENV_VARS:
        value = os.environ.get(var)
        if value is not None:
            config[var] = value
    return config


def log_env_vars(config):
    for key in sorted(config):
        if key in SENSITIVE_KEYS:
            continue
        logger.info("  %s=%s", key, config[key])


def parse_offering_uuids(config):
    """Parse WALDUR_OFFERING_UUID into a list of offering UUIDs.

    Accepts a single UUID or a comma-separated list, so one GLAuth instance can
    expose the offering users of several offerings at once. Surrounding
    whitespace is ignored and empty entries are dropped. Duplicate UUIDs are
    collapsed while preserving order, so a repeated offering is fetched once.
    """
    raw = config["WALDUR_OFFERING_UUID"]
    uuids = []
    for entry in raw.split(","):
        uuid = entry.strip()
        if uuid and uuid not in uuids:
            uuids.append(uuid)
    if not uuids:
        logger.error("WALDUR_OFFERING_UUID does not contain any offering UUID")
        sys.exit(1)
    return uuids


def get_waldur_client(config):
    verify_ssl = config["WALDUR_API_VERIFY_TLS"].lower() != "false"
    return AuthenticatedClient(
        base_url=config["WALDUR_URL"].rstrip("/").removesuffix("/api"),
        token=config["WALDUR_TOKEN"],
        verify_ssl=verify_ssl,
    )


def fetch_glauth_config(client, offering_uuid):
    logger.info("Fetching users config file")
    result = marketplace_provider_offerings_glauth_users_config_retrieve.sync(
        uuid=offering_uuid,
        client=client,
    )
    return result if result is not None else ""


def generate_password_digest(password):
    logger.info("Generating a digest for LDAP_ADMIN_PASSWORD")
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def template_preconfig(config, password_digest):
    logger.info("Templating preconfig file: %s", TEMPLATE_PATH)
    with open(TEMPLATE_PATH) as f:
        content = f.read()
    template = Template(content)
    return template.substitute(
        LDAP_ADMIN_USERNAME=config["LDAP_ADMIN_USERNAME"],
        LDAP_ADMIN_UIDNUMBER=config["LDAP_ADMIN_UIDNUMBER"],
        LDAP_ADMIN_EMAIL=config["LDAP_ADMIN_EMAIL"],
        LDAP_ADMIN_PGROUP=config["LDAP_ADMIN_PGROUP"],
        LDAP_ADMIN_PASSWORD_DIGEST=password_digest,
    )


def combine_users_configs(users_configs):
    """Combine one or more Waldur users configs into a single users/groups set.

    Each offering is exported independently, so two offerings can hand out the
    same uidnumber, gidnumber or account name. GLAuth needs each of those to be
    unique across the directory, so on a collision the first occurrence wins and
    the rest are skipped with a warning. Operators are expected to stagger the
    per-offering ``initial_uidnumber``/``initial_primarygroup_number`` ranges so
    collisions do not happen in practice.

    Returns a dict with the merged ``users`` and ``groups`` lists plus any other
    top-level keys the API might introduce later (first occurrence wins).
    """
    users = []
    groups = []
    extras = {}
    seen_uidnumbers = set()
    seen_usernames = set()
    seen_gidnumbers = set()
    seen_groupnames = set()
    collisions = 0

    for users_config in users_configs:
        parsed = tomllib.loads(users_config or "")
        for group in parsed.get("groups", []):
            gid, name = group.get("gidnumber"), group.get("name")
            if gid in seen_gidnumbers or name in seen_groupnames:
                collisions += 1
                logger.warning(
                    "Skipping duplicate group name=%s gidnumber=%s "
                    "(already provided by an earlier offering)",
                    name,
                    gid,
                )
                continue
            seen_gidnumbers.add(gid)
            seen_groupnames.add(name)
            groups.append(group)
        for user in parsed.get("users", []):
            uid, name = user.get("uidnumber"), user.get("name")
            if uid in seen_uidnumbers or name in seen_usernames:
                collisions += 1
                logger.warning(
                    "Skipping duplicate user name=%s uidnumber=%s "
                    "(already provided by an earlier offering)",
                    name,
                    uid,
                )
                continue
            seen_uidnumbers.add(uid)
            seen_usernames.add(name)
            users.append(user)
        for key, value in parsed.items():
            if key not in ("users", "groups"):
                extras.setdefault(key, value)

    if collisions:
        logger.warning(
            "Merged %d offering config(s) with %d collision(s) skipped; "
            "check that the per-offering uid/gid ranges do not overlap",
            len(users_configs),
            collisions,
        )
    return {"users": users, "groups": groups, "extras": extras}


def merge_configs(preconfig_content, users_configs):
    """Merge the rendered preconfig with one or more Waldur users configs at the
    TOML level.

    ``users_configs`` may be a single config string (one offering) or a list of
    config strings (several offerings merged into one GLAuth instance).

    A naive string concatenation is unsafe: the API renders ``users`` and
    ``groups`` as top-level keys (``groups`` in particular as a leading inline
    array), and appending that text after the preconfig's trailing ``[[groups]]``
    table reparents those keys under that table, silently dropping every
    Waldur-provided group. Parsing every document and concatenating the
    ``users``/``groups`` collections keeps the result a single coherent config.
    """
    if isinstance(users_configs, str):
        users_configs = [users_configs]
    base = tomllib.loads(preconfig_content)
    combined = combine_users_configs(users_configs)
    for key in ("users", "groups"):
        merged = base.get(key, []) + combined[key]
        if merged:
            base[key] = merged
    for key, value in combined["extras"].items():
        base.setdefault(key, value)
    return tomli_w.dumps(base)


def merge_and_write_config(preconfig_content, users_configs):
    logger.info(
        "Merging preconfig with users config, writing to: %s", OUTPUT_CONFIG_PATH
    )
    merged = merge_configs(preconfig_content, users_configs)
    with open(OUTPUT_CONFIG_PATH, "w") as f:
        f.write(merged)


def refresh_config(config, client, offering_uuids):
    """Fetch the GLAuth config for every offering and write the merged file."""
    try:
        users_configs = [
            fetch_glauth_config(client, offering_uuid)
            for offering_uuid in offering_uuids
        ]
        password_digest = generate_password_digest(config["LDAP_ADMIN_PASSWORD"])
        preconfig_content = template_preconfig(config, password_digest)
        merge_and_write_config(preconfig_content, users_configs)
        logger.info("Config refreshed successfully")
    except Exception as e:
        logger.exception("Error refreshing config: %s", e)


def register_agent_identity(client, offering_uuid):
    """Register or update agent identity for this GLAuth refresher."""
    name = f"glauth-agent-{offering_uuid}"
    logger.info("Registering agent identity: %s", name)
    existing = marketplace_site_agent_identities_list.sync(client=client, name=name)
    body = AgentIdentityRequest(
        offering=offering_uuid,
        name=name,
        version=AGENT_VERSION,
        last_restarted=datetime.datetime.now(),
        dependencies=[],
        config_file_path="",
        config_file_content="",
    )
    if existing:
        identity = marketplace_site_agent_identities_update.sync(
            uuid=existing[0].uuid.hex, body=body, client=client
        )
        logger.info("Updated existing identity: %s", identity.uuid.hex)
        return identity
    identity = marketplace_site_agent_identities_create.sync(body=body, client=client)
    logger.info("Created new identity: %s", identity.uuid.hex)
    return identity


def register_event_subscription(client, identity):
    """Register event subscription for offering_user events."""
    logger.info("Registering event subscription for offering_user")
    body = AgentEventSubscriptionCreateRequest(
        observable_object_type=ObservableObjectTypeEnum.OFFERING_USER,
        description=f"GLAuth config refresher for identity {identity.name}",
    )
    event_subscription = (
        marketplace_site_agent_identities_register_event_subscription.sync(
            uuid=identity.uuid.hex, body=body, client=client
        )
    )
    logger.info("Registered event subscription: %s", event_subscription.uuid.hex)
    return event_subscription


def create_event_subscription_queue(client, event_subscription, offering_uuid):
    """Create RabbitMQ queue for the event subscription."""
    logger.info("Creating event subscription queue")
    body = EventSubscriptionQueueCreateRequest(
        offering_uuid=offering_uuid,
        object_type=ObservableObjectTypeEnum.OFFERING_USER,
    )
    queue = event_subscriptions_create_queue.sync(
        uuid=event_subscription.uuid.hex, client=client, body=body
    )
    logger.info("Event subscription queue created")
    return queue


def register_agent_service(client, agent_identity: AgentIdentity):
    logger.info("Registering agent service")
    body = AgentServiceCreateRequest(
        name="glauth-sync-service",
        mode="glauth-sync",
    )
    service = marketplace_site_agent_identities_register_service.sync(
        uuid=agent_identity.uuid,
        body=body,
        client=client,
    )
    logger.info("Agent service %s registered with UUID: %s", service.name, service.uuid)
    return service


def register_agent_processor(
    client, agent_service: AgentService, name="glauth-sync-processor"
):
    logger.info("Registering agent processor")
    body = AgentProcessorCreateRequest(
        name=name,
        backend_type="glauth",
        backend_version=AGENT_VERSION,
    )
    processor = marketplace_site_agent_services_register_processor.sync(
        uuid=agent_service.uuid,
        body=body,
        client=client,
    )
    logger.info(
        "Agent processor %s registered with UUID: %s", processor.name, processor.uuid
    )
    return processor


def connect_to_stomp(connection, username, password):
    """Connect to STOMP server with retry logic."""
    while not connection.is_connected():
        try:
            logger.info("Connecting to STOMP server...")
            connection.connect(
                username,
                password,
                wait=True,
                headers={
                    "accept-version": "1.2",
                    "heart-beat": "10000,10000",
                },
            )
        except StompException as e:
            logger.error(
                "Failed to connect to STOMP server, retrying in 10 seconds: %s", e
            )
            time.sleep(10)


def reregister_agents(client, offering_uuids):
    """Refresh the agent identity/service/processor liveness for every offering."""
    for offering_uuid in offering_uuids:
        identity = register_agent_identity(client, offering_uuid)
        service = register_agent_service(client, identity)
        register_agent_processor(client, service)


class GlauthConfigListener(stomp.ConnectionListener):
    """STOMP listener that triggers GLAuth config refresh on offering_user events."""

    def __init__(
        self, conn, queues, username, password, config, client, offering_uuids
    ):
        self.conn = conn
        self.queues = queues
        self.username = username
        self.password = password
        self.config = config
        self.client = client
        self.offering_uuids = offering_uuids

    def on_connected(self, frame):
        # One queue per offering; subscribe to all so an event for any of the
        # exposed offerings triggers a refresh of the merged config.
        for queue in self.queues:
            destination = f"/amq/queue/{queue}"
            self.conn.subscribe(destination=destination, id=queue, ack="auto")
            logger.info("Subscribed to %s", destination)

    def on_message(self, frame):
        logger.info("Received offering_user event: %s", frame.body)
        reregister_agents(self.client, self.offering_uuids)
        refresh_config(self.config, self.client, self.offering_uuids)

    def on_error(self, frame):
        logger.error("STOMP error: %s", frame.body)

    def on_disconnected(self):
        logger.warning("Disconnected from STOMP server, reconnecting...")
        connect_to_stomp(self.conn, self.username, self.password)


def main():
    config = read_config()
    log_env_vars(config)

    client = get_waldur_client(config).__enter__()
    offering_uuids = parse_offering_uuids(config)
    logger.info(
        "Exposing offering users from %d offering(s): %s",
        len(offering_uuids),
        ", ".join(offering_uuids),
    )

    # Register an agent identity per offering. The event subscription is
    # per-user (deduplicated by Waldur), so it is registered once; a separate
    # RabbitMQ queue is created per offering and bound to that subscription.
    identities = [
        register_agent_identity(client, offering_uuid)
        for offering_uuid in offering_uuids
    ]
    event_subscription = register_event_subscription(client, identities[0])
    queue_names = [
        create_event_subscription_queue(
            client, event_subscription, offering_uuid
        ).queue_name
        for offering_uuid in offering_uuids
    ]
    for identity in identities:
        service = register_agent_service(client, identity)
        register_agent_processor(client, service, "initial-glauth-sync-processor")

    # Initial config fetch
    logger.info("Performing initial config fetch")
    refresh_config(config, client, offering_uuids)

    # Set up STOMP connection
    stomp_host = (
        config.get("WALDUR_STOMP_WS_HOST") or urlparse(config["WALDUR_URL"]).hostname
    )
    stomp_port = int(config.get("WALDUR_STOMP_WS_PORT", 443))
    ws_path = config.get("WALDUR_STOMP_WS_PATH", "/rmqws-stomp")
    use_tls = config.get("WALDUR_WEBSOCKET_USE_TLS", "true").lower() != "false"

    vhost = event_subscription.user_uuid.hex
    username = event_subscription.uuid.hex
    password = config["WALDUR_TOKEN"]

    logger.info(
        "Setting up STOMP connection to %s:%s%s", stomp_host, stomp_port, ws_path
    )
    connection = stomp.WSStompConnection(
        host_and_ports=[(stomp_host, stomp_port)],
        ws_path=ws_path,
        vhost=vhost,
    )
    if use_tls:
        connection.set_ssl(for_hosts=[(stomp_host, stomp_port)])

    connection.set_listener(
        "glauth-listener",
        GlauthConfigListener(
            connection,
            queue_names,
            username,
            password,
            config,
            client,
            offering_uuids,
        ),
    )

    connect_to_stomp(connection, username, password)

    # Keep the main thread alive and periodically refresh config
    try:
        while True:
            time.sleep(REFRESH_PERIOD)
            logger.info("Periodic config refresh")
            refresh_config(config, client, offering_uuids)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        connection.disconnect()


if __name__ == "__main__":
    main()
