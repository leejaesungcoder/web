# Rap Reaction

래퍼가 발매 전 가사나 음원을 넣으면, 곡이 공개됐을 때 **평론가 / 힙합 커뮤니티 / 일반 유튜브 청자**가 어떻게 반응할지 예측하는 웹앱입니다.

- 종합 반응 점수와 확산 범위 (니치 / 힙합 코어 팬층 / 대중 확산 가능)
- 청자층별 점수, 좋아할 점, 비판할 점
- 실제로 달릴 법한 예상 댓글 (호평 / 혹평 / 중립 / 밈)
- 화제가 될 구절과 고치면 좋을 구절, 개선 제안
- 논란 소지 (디스, 혐오 표현 등)
- 자동 계산 가사 지표: 끝 라임 일치율, 어휘 다양성, 영어 비율, 훅 후보
- **음원 분석**: BPM, 조성, 저음 비중, 비트가 들어오는 시점, 구간 구조, 반복되는 훅 위치, 에너지 흐름 그래프
- **유튜브 타임스탬프 댓글 예측**: "0:44 여기서 소름"처럼 댓글이 몰릴 순간과 예상 댓글
- 인트로 길이, 훅 타이밍 등 곡 구성이 유튜브 반응에 미칠 영향

분석에는 Claude API(`claude-opus-5-5`)를 씁니다. 유튜브 댓글을 수집해 두면 실제 한국 힙합 청자의 말투를 참고해서 예상 댓글을 더 현실적으로 만듭니다.

## 음원 분석 방식

Claude는 오디오를 직접 듣지 못합니다. 서버에서 [librosa](https://librosa.org)로 음원을 분석해 수치와 구간 정보로 바꾼 뒤 Claude에게 넘깁니다. 그래서 다음은 **판단하지 못합니다**: 보컬 톤, 발음, 플로우의 맛, 믹스·마스터링 완성도. 결과 화면의 "한계"에도 이 점이 표시됩니다.

| 항목 | 계산 방법 |
|---|---|
| BPM | 박자 추적. 하프타임/더블타임으로 들릴 수 있는 값도 함께 표시 |
| 비트 들어오는 시점 | 에너지가 곡의 높은 수준의 45%에 처음 닿는 시점 |
| 구간 구조 | 화성(chroma)과 음색(MFCC)이 비슷한 구간끼리 묶어 A/B/C로 표시 |
| 훅 후보 | 가장 많이 반복되면서 에너지가 높은 구간 |
| 에너지 상승 순간 | 4초 사이 에너지가 크게 오르는 시점 (드롭, 훅 진입) |
| 저음 비중 | 150Hz 미만 에너지 비율 (808 강도) |

3분짜리 곡 분석에 약 5~10초 걸립니다. 서버에 ffmpeg가 있어야 mp3/m4a를 읽을 수 있습니다.

**가사 자동 받아쓰기 (선택):** `pip install faster-whisper`를 설치하면, 가사 없이 음원만 넣었을 때 받아쓰기를 시도합니다. 처음 실행할 때 Hugging Face에서 모델(약 500MB)을 내려받습니다. 랩은 받아쓰기 정확도가 낮아서 가사를 직접 넣는 쪽이 훨씬 정확합니다. 설치하지 않으면 음원만 넣었을 때 곡 구조만으로 분석합니다.

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
  audio_features.py   음원 분석 (BPM, 구간, 훅, 에너지 흐름)
  transcribe.py       가사 자동 받아쓰기 (faster-whisper 설치 시)
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
3. ~~음원 분석: 오디오 업로드 → 곡 구조, 훅 타이밍, 타임스탬프 댓글 예측~~ (완료. 보컬 톤 분석은 남음)
4. 점수 보정: `videos.jsonl`의 조회수·좋아요 비율을 정답으로 반응 점수 보정 모델 학습
