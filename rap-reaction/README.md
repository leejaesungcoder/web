# Rap Reaction

래퍼가 발매 전 가사를 넣으면, 곡이 공개됐을 때 **평론가 / 힙합 커뮤니티 / 일반 유튜브 청자**가 어떻게 반응할지 예측하는 웹앱입니다.

- 종합 반응 점수와 확산 범위 (니치 / 힙합 코어 팬층 / 대중 확산 가능)
- 청자층별 점수, 좋아할 점, 비판할 점
- 실제로 달릴 법한 예상 댓글 (호평 / 혹평 / 중립 / 밈)
- 화제가 될 구절과 고치면 좋을 구절, 개선 제안
- 논란 소지 (디스, 혐오 표현 등)
- 자동 계산 가사 지표: 끝 라임 일치율, 어휘 다양성, 영어 비율, 훅 후보

분석에는 Claude API(`claude-opus-5-5`)를 씁니다. 유튜브 댓글을 수집해 두면 실제 한국 힙합 청자의 말투를 참고해서 예상 댓글을 더 현실적으로 만듭니다.

## 실행

```bash
cd rap-reaction
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # 없으면 데모 모드로 실행됨
uvicorn app.main:app --reload
```

브라우저에서 http://localhost:8000 을 엽니다.

## 유튜브 댓글 수집

공식 YouTube Data API v3를 씁니다. [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com)에서 YouTube Data API v3를 켜고 API 키를 발급하세요 (무료, 하루 10,000 유닛).

```bash
export YOUTUBE_API_KEY=AIza...

# 검색어로 수집 (검색어당 조회수 상위 영상 10개, 영상당 댓글 300개)
python -m collector.youtube_comments --query "국힙 MV"

# 미리 준비한 검색어 목록으로 수집
python -m collector.youtube_comments --queries-file collector/seed_queries.txt

# 특정 영상만 수집
python -m collector.youtube_comments --video https://youtu.be/VIDEO_ID --max-comments 1000
```

결과물:

| 파일 | 내용 |
|---|---|
| `data/comments.jsonl` | 댓글 본문, 좋아요 수, 답글 수, 작성 시각, 영상 정보. **작성자 이름과 ID는 저장하지 않습니다.** |
| `data/videos.jsonl` | 영상별 조회수, 좋아요, 댓글 수 (4단계 점수 모델의 정답 데이터) |

여러 번 실행해도 같은 댓글은 중복 저장되지 않습니다. 쿼터를 다 쓰면 그때까지 수집한 데이터는 저장된 채로 멈춥니다.

쿼터 사용량: 검색 1회 100유닛, 영상 정보 50개당 1유닛, 댓글 100개당 1유닛. `seed_queries.txt` 전체(7개 검색어 × 10개 영상 × 300개 댓글)를 돌리면 약 900유닛입니다.

수집한 데이터는 `.gitignore`로 저장소에 올라가지 않게 해 두었습니다. YouTube API 약관상 원본 데이터를 공개 배포하거나 오래 보관하는 데 제한이 있으니, 서비스로 출시할 때는 약관을 다시 확인하세요.

## 구조

```
app/
  main.py             FastAPI 서버 (/api/analyze, /api/status)
  analyzer.py         Claude 호출, 출력 스키마, 프롬프트, 데모 모드
  lyrics_features.py  라임/어휘/영어 비율 등 정량 지표
  references.py       수집된 댓글에서 참고 샘플 추출
  static/index.html   화면
collector/
  youtube_comments.py 유튜브 댓글 수집기
  seed_queries.txt    기본 검색어
data/                 수집 데이터 (git 제외)
```

## 다음 단계

1. ~~MVP: 가사 → 청자 반응 예측~~ (현재)
2. 유사 곡 검색: 수집한 댓글과 영상을 임베딩해서 입력 가사와 비슷한 곡의 실제 반응을 근거로 제시
3. 음원 분석: 오디오 업로드 → BPM, 분위기, 보컬 톤 추출
4. 점수 보정: `videos.jsonl`의 조회수·좋아요 비율을 정답으로 반응 점수 보정 모델 학습
