import os
import sys

import yaml
from PyQt5.QtWidgets import QApplication

from core.ethercat_master import EtherCATMasterThread
from ui.main_window import MainWindow

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_config(file_path=None):
    if file_path is None:
        file_path = os.path.join(ROOT, "config", "settings.yaml")
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    config = load_config()
    app = QApplication(sys.argv)

    master_thread = EtherCATMasterThread(config)
    window = MainWindow(master_thread, config)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
