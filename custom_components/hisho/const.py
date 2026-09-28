"""Constants for the Hisho integration."""

DOMAIN = "hisho"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 8787
DEFAULT_SCAN_INTERVAL = 30

ENDPOINT_FLEET_HEALTH = "/api/fleet-health"
ENDPOINT_STATUS = "/api/status"
ENDPOINT_HOME_ASSISTANT = "/api/home-assistant"
ENDPOINT_COMPUTE = "/api/compute"
ENDPOINT_IPMI = "/api/ipmi"

MANUFACTURER = "Yepiz Tech LLC"
MODEL = "Hisho Control Plane"

DATA_FLEET_HEALTH = "fleet_health"
DATA_STATUS = "status"
