@echo off
chcp 65001 >nul
echo [1/3] 가상환경 만드는 중...
python -m venv .venv
echo [2/3] 가상환경 켜는 중...
call .venv\Scripts\activate.bat
echo [3/3] 패키지 설치 중...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo ====================================
echo  설치 완료
echo  실행: streamlit run .py
echo ====================================
