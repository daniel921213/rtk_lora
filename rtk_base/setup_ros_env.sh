#!/bin/bash

sudo apt update
sudo apt install python3-pip

# 安裝 virtualenv 並創建虛擬環境
#python3 -m pip install --user virtualenv
sudo apt install python3.8-venv
#virtualenv venv
python3 -m venv venv

# 顏色設置
RED='\033[0;31m'
NC='\033[0m' # 沒有顏色

if [ ! -d "venv" ]; then
    echo -e "${RED}[ERROR]Virtual environment creation failed.${NC}" >&2
    return 1
fi

# 啟動虛擬環境
source venv/bin/activate


sudo apt install net-tools
sudo apt install openconnect
#git clone https://github.com/tomojitakasu/RTKLIB.git
until git clone https://github.com/tomojitakasu/RTKLIB.git; do
  echo "Git clone failed, retrying in 5 seconds..."
  sleep 5
done

cd RTKLIB/app/str2str/gcc
make
cd ../../../..
sudo cp RTKLIB/app/str2str/gcc/str2str venv/bin/

# 安裝所需的 Python 套件
pip install pygnssutils
#sudo openconnect --protocol=gp vpn.ntut.edu.tw
echo "Virtual environment setup complete and required packages installed."

sudo chmod +x rtk_base.py setting_base.py set_vpn.py