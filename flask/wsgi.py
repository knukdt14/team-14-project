"""Flask 개발 서버 진입점.

실행: python test/wsgi.py  (프로젝트 루트에서)
기존 Streamlit 앱(8501/8503)과 포트가 겹치지 않도록 기본 5000번을 쓴다.
"""

import sys
from pathlib import Path

# services/geo.py·tour_api.py 등은 프로젝트 루트의 공용 services/ 패키지를
# 그대로 재사용한다 — 이 스크립트 자체는 test/ 아래에 있어서 루트를 따로
# sys.path에 넣어줘야 "from services.xxx import ..." 임포트가 풀린다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask_app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
