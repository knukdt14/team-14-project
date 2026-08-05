# 도커 사용법 (팀원용)

## 0. 처음 한 번만

**Docker Desktop 설치** → https://www.docker.com/products/docker-desktop
설치 후 프로그램을 켜 두어야 명령어가 동작합니다. 잘 깔렸는지 확인:

```bash
docker --version
```

## 1. 프로젝트 실행

```bash
git clone <레포 주소>
cd triproll

copy .env.example .env      # Windows
cp .env.example .env        # Mac / Linux

docker compose up --build
```

브라우저에서 `http://localhost:8501` 접속.
끄려면 터미널에서 `Ctrl + C`.

파이썬을 안 깔았어도 됩니다. 도커가 컨테이너 안에 다 넣어 줍니다.

## 2. 자주 쓰는 명령어

| 명령어 | 하는 일 | 언제 |
|---|---|---|
| `docker compose up` | 컨테이너 켜기 | 평소 개발할 때 |
| `docker compose up --build` | 이미지 다시 만들고 켜기 | requirements.txt가 바뀌었을 때 |
| `docker compose up -d` | 백그라운드로 켜기 | 터미널을 다른 데 쓰고 싶을 때 |
| `docker compose down` | 컨테이너 끄고 지우기 | 작업 끝냈을 때 |
| `docker compose logs -f` | 로그 실시간 보기 | 에러 원인 찾을 때 |
| `docker compose ps` | 지금 켜진 컨테이너 목록 | 뭐가 돌고 있는지 확인 |
| `docker compose exec app bash` | 컨테이너 안으로 들어가기 | 안에서 직접 확인하고 싶을 때 |

## 3. 팀플에서 도는 흐름

### 코드만 고칠 때 — 아무것도 안 해도 됩니다

`docker-compose.yml`의 볼륨 설정 덕분에 내 폴더가 컨테이너 안과 연결돼 있습니다.
`.py` 파일을 저장하면 Streamlit이 알아서 새로고침합니다. 재빌드 필요 없습니다.

### 라이브러리를 추가할 때

컨테이너 안에서 `pip install` 하면 **다음 빌드 때 사라집니다.**
반드시 이 순서로 하세요.

```bash
# 1. requirements.txt에 한 줄 추가
#    예) folium>=0.17

# 2. 다시 빌드
docker compose up --build

# 3. requirements.txt 변경분을 PR로 올린다  ← 이게 팀 환경을 맞추는 유일한 통로
```

### 팀원이 올린 변경을 받았을 때

```bash
git pull
docker compose up --build
```

`requirements.txt`가 바뀌었을 수 있으니 `--build`를 붙입니다.

## 4. 자주 나는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| `port is already allocated` | 8501을 다른 게 쓰는 중 | compose의 `"8501:8501"`을 `"8502:8501"`로 바꾸고 localhost:8502 접속 |
| 브라우저에서 안 열림 | Streamlit이 localhost로만 열림 | Dockerfile에 `--server.address=0.0.0.0`이 있는지 확인 |
| 코드를 고쳤는데 반영 안 됨 | 볼륨 설정 누락 | compose의 `volumes: - .:/app` 확인 |
| 이미지가 몇 GB로 커짐 | 데이터·가상환경까지 복사됨 | `.dockerignore` 확인 |
| `Cannot connect to Docker daemon` | Docker Desktop이 꺼져 있음 | Docker Desktop 실행 |
| 빌드가 매번 오래 걸림 | 캐시가 안 먹음 | Dockerfile에서 requirements를 코드보다 먼저 COPY |
| M1 맥만 안 됨 | CPU 아키텍처 차이 | 각자 로컬에서 빌드 (`--build`) |

## 5. 완전히 초기화하고 싶을 때

```bash
docker compose down -v          # 컨테이너와 볼륨까지 삭제
docker compose build --no-cache # 캐시 없이 처음부터 빌드
docker compose up
```

## 6. 알아둘 것

- **`.env`는 절대 커밋하지 않습니다.** `.gitignore`에 들어 있습니다.
  키를 새로 받으면 `.env.example`에 이름만 추가해서 공유하세요.
- **컨테이너 안에서 만든 파일은 사라집니다.** 남겨야 할 건 볼륨으로 연결된 폴더에 저장하세요.
- **P4 벡터DB(Chroma)는 compose에 주석으로 준비돼 있습니다.** 쓸 때 `#`만 지우면 됩니다.
