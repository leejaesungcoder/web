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

**음원 파일은 서버로 올라가지 않습니다.** 브라우저 안에서(`app/static/audio-analyzer.js`, Web Worker) 음원을 분석하고, BPM·구간 구조 같은 수치(수 KB)만 서버로 보냅니다. 미발매곡이 외부로 나가지 않고, 서버도 가벼워서 무료 호스팅에서 돌아갑니다.

Claude는 오디오를 직접 듣지 못하고 이 수치만 보고 추론합니다. 그래서 다음은 **판단하지 못합니다**: 보컬 톤, 발음, 플로우의 맛, 믹스·마스터링 완성도. 결과 화면의 "한계"에도 이 점이 표시됩니다.

| 항목 | 계산 방법 |
|---|---|
| BPM | 온셋 강도의 자기상관 (곡 중간 90초). 하프타임/더블타임 값도 함께 표시 |
| 비트 들어오는 시점 | 에너지가 곡의 높은 수준의 45%에 처음 닿는 시점 |
| 구간 구조 | 화성(chroma)과 음색(MFCC)이 비슷한 구간끼리 묶어 A/B/C로 표시 |
| 훅 후보 | 가장 많이 반복되면서 에너지가 높은 구간 |
| 에너지 상승 순간 | 4초 사이 에너지가 크게 오르는 시점 (드롭, 훅 진입) |
| 저음 비중 | 150Hz 미만 에너지 비율 (808 강도) |

librosa(Python 오디오 분석 라이브러리)와 같은 계산 방식으로 만들었고, 테스트 음원에서 librosa 버전과 모든 값이 일치하는 것을 확인했습니다. 3~4분짜리 곡 분석에 2~5초 걸리고, 7분이 넘는 음원은 앞 7분만 분석합니다. 브라우저가 읽을 수 있는 형식(mp3, wav, m4a 등)이면 됩니다.

## 실행

```bash
cd rap-reaction
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # 없으면 데모 모드로 실행됨
uvicorn app.main:app --reload
```

브라우저에서 http://localhost:8000 을 엽니다.

## 웹에 배포하기 (Render, 무료)

서버는 Claude 호출만 하므로 메모리 약 100MB로 돌아갑니다. Render 무료 플랜에 Docker 없이 올릴 수 있습니다. 무료 플랜은 15분 동안 접속이 없으면 잠들고, 다음 접속 때 깨어나는 데 1분 정도 걸립니다.

1. [render.com](https://render.com)에 GitHub 계정으로 가입합니다.
2. 대시보드에서 **New → Blueprint**를 누르고 이 저장소(`web`)를 연결합니다. 저장소 최상위의 `render.yaml`을 읽어 `rap-reaction` 서비스를 무료 플랜으로 만듭니다.
3. 입력 칸이 나오면 값을 넣습니다:

   | 이름 | 값 |
   |---|---|
   | `ANTHROPIC_API_KEY` | Claude API 키 |
   | `APP_ACCESS_CODE` | (선택) 베타 테스터에게만 알려줄 접속 코드. 비워 두면 누구나 사용 |

   `RATE_LIMIT_PER_HOUR`(IP당 시간당 분석 횟수, 기본 10), `DAILY_LIMIT`(하루 전체 분석 횟수, 기본 200)는 나중에 서비스의 **Environment** 메뉴에서 바꿀 수 있습니다.
4. **Apply**를 누르면 빌드가 시작됩니다 (2~3분). 끝나면 `https://rap-reaction-xxxx.onrender.com` 같은 주소가 생깁니다. 이 주소를 래퍼들에게 공유하면 됩니다.
5. 이후 GitHub에 코드를 올릴 때마다 자동으로 다시 배포됩니다.

### 공개 배포 시 보호 장치

- **요금 보호:** IP당 시간당 10회, 하루 전체 200회로 분석을 제한합니다 (환경 변수로 조절). 접속 코드를 설정하면 코드를 아는 사람만 쓸 수 있습니다. Anthropic 콘솔에서 월 사용 한도도 함께 걸어 두는 것을 권장합니다.
- **미발매곡 보호:** 음원 파일은 브라우저 밖으로 나가지 않습니다. 가사와 분석 수치만 Claude API로 전송되며, 화면에 이 내용을 안내합니다.
- **입력 검증:** 브라우저가 보낸 분석 수치는 형식과 범위를 서버에서 검사합니다. Claude 호출은 동시에 4개까지만 실행합니다.
- 서버를 재시작하면 횟수 제한 기록은 초기화됩니다.
- 수집한 유튜브 댓글(`data/`)은 배포에 포함되지 않습니다. 배포된 앱은 참고 댓글 없이 분석합니다.

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
  main.py             FastAPI 서버 (/api/analyze, /api/status, /healthz)
  guard.py            접속 코드, 횟수 제한, 동시 실행 제한
  analyzer.py         Claude 호출, 출력 스키마, 프롬프트, 데모 모드
  lyrics_features.py  라임/어휘/영어 비율 등 정량 지표
  references.py       수집된 댓글에서 참고 샘플 추출
  static/index.html          화면
  static/audio-analyzer.js   브라우저 음원 분석 (BPM, 구간, 훅, 에너지 흐름)
  static/audio-worker.js     분석을 별도 스레드에서 실행
collector/
  youtube_comments.py 유튜브 댓글 수집기
  seed_queries.txt    기본 검색어
data/                 수집 데이터 (git 제외)
../render.yaml         Render 배포 설정 (저장소 최상위)
```

## 다음 단계

1. ~~MVP: 가사 → 청자 반응 예측~~ (현재)
2. 유사 곡 검색: 수집한 댓글과 영상을 임베딩해서 입력 가사와 비슷한 곡의 실제 반응을 근거로 제시
3. ~~음원 분석: 곡 구조, 훅 타이밍, 타임스탬프 댓글 예측~~ (완료. 보컬 톤 분석, 가사 자동 받아쓰기는 남음)
4. 점수 보정: `videos.jsonl`의 조회수·좋아요 비율을 정답으로 반응 점수 보정 모델 학습
