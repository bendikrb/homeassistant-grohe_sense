from datetime import timedelta

DOMAIN = 'grohe_sense'

CONF_REFRESH_TOKEN = 'refresh_token'

BASE_URL = 'https://idp2-apigw.cloud.grohe.com/v3/iot/'

GROHE_SENSE_TYPE = 101  # Type identifier for the battery powered water detector
GROHE_SENSE_GUARD_TYPE = 103  # Type identifier for sense guard, the water guard installed on your water pipe

VALVE_UPDATE_DELAY = timedelta(minutes=1)

NOTIFICATION_TYPES = {
    # The protocol returns notification information as a (category, type) tuple, this maps to strings
    (10, 60): 'Firmware update available',
    (10, 460): 'Firmware update available',
    (20, 11): 'Battery low',
    (20, 12): 'Battery empty',
    (20, 20): 'Below temperature threshold',
    (20, 21): 'Above temperature threshold',
    (20, 30): 'Below humidity threshold',
    (20, 31): 'Above humidity threshold',
    (20, 40): 'Frost warning',
    (20, 80): 'Lost wifi',
    (20, 320): 'Unusual water consumption (water shut off)',
    (20, 321): 'Unusual water consumption (water not shut off)',
    (20, 330): 'Micro leakage',
    (20, 340): 'Frost warning',
    (20, 380): 'Lost wifi',
    (30, 0): 'Flooding',
    (30, 310): 'Pipe break',
    (30, 400): 'Maximum volume reached',
    (30, 430): 'Sense detected water (water shut off)',
    (30, 431): 'Sense detected water (water not shut off)',
}
