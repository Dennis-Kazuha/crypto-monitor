# 使用輕量級 Python 映像檔
FROM python:3.9-slim

# 設定工作目錄
WORKDIR /app

# 安裝 Git (有些 Python 套件安裝時需要，保險起見裝上)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# 複製需求文件並安裝套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有程式碼到容器內
COPY . .

# [關鍵步驟] 賦予 start.sh 執行權限 (Linux 權限設定)
RUN chmod +x start.sh

# [關鍵步驟] 設定容器啟動時，執行我們的腳本
CMD ["./start.sh"]