import sys
import yaml
from PyQt5.QtWidgets import QApplication
from core.ethercat_master import EtherCATMasterThread
from ui.main_window import MainWindow

def load_config(file_path="config/settings.yaml"):
    with open(file_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def main():
    config = load_config()
    app = QApplication(sys.argv)
    
    # 建立主站背景通訊執行緒
    master_thread = EtherCATMasterThread(config)
    
    # 啟動主視窗
    window = MainWindow(master_thread, config)
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()