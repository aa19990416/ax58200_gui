#!/bin/bash
# 取得目前腳本所在目錄的上一層 (專案根目錄)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# 允許 root 連接當前 X Server 顯示 GUI
xhost +local:root > /dev/null 2>&1

# 帶入 DISPLAY 變數執行
sudo DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY python3 main.py