"""
Unofficial Home Assistant Apps (Add-ons) Repository Updater
"""

import importlib.metadata

APP_FULL_NAME = __doc__.strip()
APP_VERSION = importlib.metadata.version("homeassistant-apps-repository-updater")
