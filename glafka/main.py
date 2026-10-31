import logging
import sys

# Importing glafka.ui pins the GTK/Adw versions before any gi.repository import
from glafka.ui.application import Application


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return Application().run(sys.argv)
