# 🎬 부산 영화 상영시간표 (Busan Movie Timetable)

부산 6개 영화관의 7일간 상영시간표를 매일 자동 수집하여 보여주는 초경량 웹 서비스입니다. 
클라우드 무료 티어(Render, Railway 등) 배포에 최적화되어 있습니다.

## 대상 극장

| 브랜드 | 지점 | URL ID |
|--------|------|------|
| 영화의전당 | 영화의전당 | rbsIdx=37 |
| 롯데시네마 | 센텀시티 | cinemaID=2006 |
| 롯데시네마 | 부산본점 | cinemaID=2004 |
| 롯데시네마 | 동래 | cinemaID=2007 |
| 메가박스 | 해운대(장산) | brchNo=0079 |
| 메가박스 | 서면대한 | brchNo=0082 |

---

## 🚀 클라우드 배포 방법 (GitHub + Render 무료 티어)

이 프로젝트는 브라우저 자동화(Selenium/Playwright)가 필요 없는 순수 HTTP `requests` 기반 스크래퍼를 사용하여 서버 메모리 점유율을 최소화했습니다.

### 1단계: GitHub 저장소 생성 및 푸시

터미널을 열고 다음 명령어를 순서대로 실행하세요.

```bash
# Git 초기화
git init

# 모든 파일 추가 (venv 등은 .gitignore에 의해 자동 제외됨)
git add .

# 첫 커밋
git commit -m "Initial commit: Busan Movie Timetable"

# 브랜치명 변경 (선택 사항)
git branch -M main

# GitHub 리포지토리 연결 (사용자의 실제 URL로 변경)
git remote add origin https://github.com/USERNAME/REPO_NAME.git

# 코드 푸시
git push -u origin main
```

### 2단계: Render에 웹 서버 배포하기 (무료)

1. [Render.com](https://render.com) 에 회원가입 및 로그인합니다.
2. Dashboard에서 **"New +" -> "Web Service"**를 클릭합니다.
3. GitHub을 연결하고 방금 만든 저장소를 선택합니다.
4. 설정 확인 (자동 감지됨):
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free`
5. **"Create Web Service"**를 클릭하면 몇 분 후 배포가 완료되고 공개 URL이 제공됩니다.

### 3단계: 매일 자동 데이터 업데이트 (GitHub Actions)

무료 클라우드 서버는 로컬 디스크가 초기화되는 경우가 많습니다. 이를 해결하기 위해 이 프로젝트는 **GitHub Actions**를 사용하여 매일 데이터를 갱신합니다.

1. `.github/workflows/daily-scraper.yml` 파일이 이미 포함되어 있습니다.
2. 매일 한국 시간 **오전 5시(UTC 20:00)**에 GitHub 서버가 자동으로 `scraper.py`를 실행합니다.
3. 새로운 상영 시간표를 `timetable.db`에 저장하고 리포지토리에 푸시(커밋)합니다.
4. 리포지토리에 새로운 커밋이 발생하므로 **Render 웹 서버가 이를 감지하고 자동으로 최신 데이터로 재배포**합니다.

> **수동 갱신 방법**: GitHub 리포지토리의 `Actions` 탭에서 `Daily Scraper` 워크플로우를 선택하고 **"Run workflow"**를 클릭하면 즉시 데이터를 갱신할 수 있습니다.

---

## 💻 로컬 개발 및 실행

### 1. 가상환경 생성 및 패키지 설치
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 수동 데이터 스크래핑
```bash
python scraper.py
```
> 6개 지점 × 7일 분량의 데이터를 수집하여 `timetable.db`에 저장합니다. (약 1분 소요)

### 3. 로컬 서버 실행
```bash
uvicorn app:app --reload --port 8000
```
브라우저에서 http://localhost:8000 에 접속하세요.

---

## 📡 API 연동 가이드

JSON 형태로 데이터를 받아올 수 있는 API 엔드포인트를 제공합니다.

`GET /api/screenings?date=YYYY-MM-DD&branch=지점명&movie=영화명`

**응답 예시**:
```json
{
  "date": "2026-08-24",
  "count": 141,
  "screenings": [
    {
      "id": 911,
      "date": "2026-08-24",
      "theater_brand": "롯데시네마",
      "branch_name": "동래",
      "movie_title": "오펜하이머",
      "screen_name": "6관",
      "format": "2D",
      "start_time": "12:55",
      "remaining_seats": "28/206",
      "booking_url": "https://..."
    }
  ]
}
```
