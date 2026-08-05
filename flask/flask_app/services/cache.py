"""Flask 라우트/서비스가 공유하는 캐시 인스턴스.

flask_app/__init__.py의 create_app()에서 cache.init_app(app)으로 실제 앱에 연결한다.
services/*.py는 이 모듈만 알면 되고 flask_app을 몰라도 되게 해서 순환 임포트를 피한다.
"""

from flask_caching import Cache

cache = Cache()
