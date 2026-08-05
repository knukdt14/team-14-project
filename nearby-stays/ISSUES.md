# nearby-stays 이슈 기록

## 0001. 카카오 API 키는 유효하지만 로컬 검색 호출이 401(NotAuthorizedError)로 거부됨

- **서비스 / 단계**: nearby-stays, 로컬 Docker 컨테이너 실행 후 Kakao API 스모크 테스트
- **증상**: `.env`에 넣은 `KAKAO_REST_API_KEY`로 `주소 검색 API`를 호출하면 다음 에러가 발생.
  ```json
  {"errorType":"NotAuthorizedError","message":"App(숙소 리스트) disabled OPEN_MAP_AND_LOCAL service."}
  ```
- **원인**: REST API 키 자체는 정상 발급되었지만, 카카오 디벨로퍼스에서 해당 애플리케이션에
  **카카오맵/로컬(OPEN_MAP_AND_LOCAL) 서비스가 활성화되어 있지 않음**. 키 발급만으로는
  로컬 검색 API를 바로 쓸 수 없고, 앱별로 사용할 제품을 켜줘야 한다.
- **해결**: Kakao Developers → 내 애플리케이션 → 해당 앱 → **제품 설정 → 카카오맵** →
  **활성화 설정 ON**. (JS SDK와 달리 REST API 사용에는 별도 도메인 등록이 필요 없음.)
- **교훈**: 카카오 API는 "키 발급 = 바로 사용 가능"이 아니라, 앱 단위로 사용할 제품
  (카카오맵/카카오톡 로그인 등)을 개별적으로 활성화해야 한다. 발표 때 "왜 처음엔 401이
  났는지" 설명할 때 이 메모를 그대로 쓰면 됨.
- **상태**: ✅ 해결 완료. 활성화 후 재검증 결과, 주소검색→키워드검색 폴백→반경 내
  숙박시설(AD5) 검색까지 전체 흐름 정상 동작 확인 (예: "부산 해운대해수욕장" 검색 시
  주변 1km 내 숙박시설 5건이 132~180m 거리로 정상 조회됨).

## 0002. "해운대해수욕장" 같은 관광지명은 주소 검색 API로 좌표를 못 찾음

- **서비스 / 단계**: nearby-stays, geocode_location() 로직 검증
- **증상**: `/v2/local/search/address.json`(주소 검색)에 "부산 해운대해수욕장"을 넣으면
  결과가 0건(`total_count: 0`)으로 나옴.
- **원인**: 주소 검색 API는 지번/도로명 **주소 문자열**만 매칭한다. "해운대해수욕장" 같은
  장소/관광지 이름은 주소가 아니라 POI(관심지점) 이름이라 이 API로는 찾을 수 없다.
- **해결**: `app.py`의 `geocode_location()`이 이미 이 케이스를 고려해서 설계되어 있음 —
  주소 검색 결과가 없으면 자동으로 `/v2/local/search/keyword.json`(키워드 검색)으로
  폴백해서 대표 좌표를 찾는다. 실제로 "부산 해운대해수욕장"은 키워드 검색에서 15건이
  잡혀 정상적으로 좌표를 구함.
- **교훈**: 사용자가 입력할 위치는 정확한 지번 주소일 수도, 관광지/건물 이름일 수도 있으므로
  처음부터 "주소 검색 → 실패 시 키워드 검색" 2단계 폴백을 넣어둔 게 유효했다.

## 0003. 요약 지표(최단/평균 거리) 계산 시 `TypeError: unsupported operand type(s) for +: 'int' and 'str'`

- **서비스 / 단계**: nearby-stays, 기능 업그레이드(유형 필터·요약 지표·정렬) 추가 후 실사용 테스트
- **증상**:
  ```
  File "src/app.py", line 243, in render_summary
      "평균 거리", f"{int(sum(distances) / len(distances)):,} m" if distances else "-"
  TypeError: unsupported operand type(s) for +: 'int' and 'str'
  ```
- **원인**: 카카오 카테고리 검색 API가 응답의 `distance` 필드를 **숫자가 아니라 문자열**
  (`"132"`)로 내려준다. 카드 UI에서는 `int(distance_m)`로 매번 형변환해서 우연히
  문제가 없었지만, 새로 추가한 `render_summary()`의 `sum(distances)`는 문자열 리스트를
  그대로 더하려다 실패했다(`sum()`은 시작값 `0`(int)에 문자열을 더하려 시도).
  같은 이유로 `filtered.sort(key=lambda p: p.get("distance") or 0)`도 값이 섞이면
  (문자열 vs int) 정렬 비교에서 터질 수 있는 잠재 버그였다.
- **해결**: API 응답을 받는 지점(`search_nearby_lodging`)에서 각 place의 `distance`를
  즉시 `int`로 정규화하도록 수정. 이후 카드 렌더링/요약 지표/정렬 등 모든 소비처가
  항상 `int | None`만 다루도록 통일.
- **교훈**: 외부 API 응답 필드의 실제 타입을 가정하지 말고, **데이터를 받는 지점에서
  한 번만 정규화**해서 이후 로직 전체가 일관된 타입을 다루게 하는 게 안전하다.
  화면 표시 코드마다 개별적으로 `int()`를 흩뿌려 놓으면 한 곳이라도 빠뜨렸을 때
  이런 버그가 재발한다.
