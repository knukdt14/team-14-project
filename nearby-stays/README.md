---
title: Nearby Stays
emoji: 🏨
colorFrom: yellow
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
short_description: 여행지 주변 숙소를 카카오 로컬 API로 검색하는 Streamlit 서비스
license: mit
---

# 🏨 주변 숙소 리스트 (nearby-stays)

여행지 주소나 지명을 입력하면 **카카오 로컬 API**로 주변 숙박시설을 거리순으로 검색해
지도와 목록으로 보여주는 Streamlit 서비스입니다.

## 주요 기능

- 주소/지명 → 좌표 변환 (카카오 주소 검색 API, 실패 시 키워드 검색으로 폴백)
- 좌표 기준 반경(500m/1000m/2000m/3000m) 내 숙박시설 검색 (카테고리 그룹 코드 `AD5`)
- 검색 결과를 `st.map`으로 시각화, 카드 목록으로 이름/주소/거리/전화번호/카카오맵 링크 표시
- API 키 미설정 시 앱이 죽지 않고 안내 메시지를 보여줌

## 준비물

카카오 REST API 키가 필요합니다.

1. [Kakao Developers](https://developers.kakao.com/) 접속 → 로그인 → **내 애플리케이션** → **애플리케이션 추가하기**
2. 생성한 앱의 **앱 키** 탭에서 **REST API 키**를 복사 (JavaScript 키 아님)
3. `.env.example`을 복사해 `.env`를 만들고 키를 채워 넣기

```bash
cp .env.example .env
# .env 파일을 열어 KAKAO_REST_API_KEY=발급받은키 로 채우기
```

## 로컬에서 바로 실행 (Docker 없이)

```bash
pip install -r requirements.txt
streamlit run src/app.py
```

## Docker로 실행

```bash
docker build -t nearby-stays .
docker run -p 8501:7860 --env-file .env nearby-stays
```

브라우저에서 http://localhost:8501 접속.

이미지는 재현 가능한 빌드를 위해 `requirements.txt`에 버전을 고정했고,
non-root 사용자로 실행되며 `HEALTHCHECK`가 포함되어 있습니다
(`docker ps`로 `healthy` 상태 확인 가능).

## Docker Compose로 실행 (실 서비스와 유사한 방식)

재시작 정책과 헬스체크가 포함된 `docker-compose.yml`을 제공합니다.

```bash
docker compose up -d --build
docker compose ps      # STATUS 컬럼에서 healthy 확인
docker compose logs -f
docker compose down
```

## Docker Hub

빌드된 이미지는 [minehddld/nearby-stays](https://hub.docker.com/r/minehddld/nearby-stays)에
푸시되어 있습니다. 직접 빌드하지 않고 바로 받아서 실행할 수도 있습니다.

```bash
docker run -p 8501:7860 --env-file .env minehddld/nearby-stays:latest
```

## Hugging Face Spaces 배포

이 저장소를 그대로 Hugging Face Space(SDK: Docker)에 push하면 위 `README.md`
상단의 YAML front-matter(`sdk: docker`, `app_port: 7860`)를 그대로 인식해서 배포됩니다.
Space의 **Settings → Repository secrets**에 `KAKAO_REST_API_KEY`를 등록해야 합니다.

## 프로젝트 구조

```
nearby-stays/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── requirements.txt
├── .env.example
├── README.md
├── ISSUES.md
└── src/
    └── app.py
```
