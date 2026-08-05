"""TripRoll Flask 앱 팩토리.

기존 Streamlit 앱(app.py, views/)은 건드리지 않는다 — 이 패키지는 완전히
독립적인 새 구현이며, 같은 services/*.py 비즈니스 로직을 재사용한다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, session
from flask_session import Session

from flask_app.services.cache import cache

BASE_DIR = Path(__file__).resolve().parent.parent


def create_app() -> Flask:
    load_dotenv()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24).hex()

    # 일정/숙소 후보 등은 쿠키 용량(4KB)을 넘을 수 있어 서버 사이드 세션을 쓴다.
    session_dir = BASE_DIR / ".flask_session"
    session_dir.mkdir(exist_ok=True)
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = str(session_dir)
    app.config["SESSION_PERMANENT"] = False
    Session(app)

    app.config["CACHE_TYPE"] = "SimpleCache"
    app.config["CACHE_DEFAULT_TIMEOUT"] = 300
    cache.init_app(app)

    from flask_app.routes.insurance import insurance_bp
    from flask_app.routes.lodging import lodging_bp
    from flask_app.routes.pick import pick_bp
    from flask_app.routes.planner import planner_bp

    app.register_blueprint(pick_bp)
    app.register_blueprint(planner_bp)
    app.register_blueprint(lodging_bp)
    app.register_blueprint(insurance_bp)

    @app.context_processor
    def inject_globals():
        return {"trip_context": session.get("trip_context", {})}

    return app
