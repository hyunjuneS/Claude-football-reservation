# ⚽ Claude Football Reservation

축구장 예약 시스템입니다.

## 소개

이 프로젝트는 축구장을 손쉽게 조회하고 예약할 수 있는 **축구 예약 시스템**입니다.

## 자동 예약 스크립트 (`reserve.py`)

용인시 공공체육시설 사이트에서 **수지체육공원축구장**을 09:00 오픈에 맞춰 자동 예약합니다.
대상 월의 첫째 주 월요일부터 차례로 확인해서 **20:00~22:00** 시간대가 비어 있으면 신청합니다.

### 1. 설치

```bash
pip install -r requirements.txt
```

### 2. 원격 디버깅 모드로 Chrome 실행 후 로그인

스크립트는 이미 로그인된 Chrome 창에 연결해서 동작합니다.
새 Chrome 창을 아래 명령으로 띄운 뒤, **그 창에서** 사이트에 로그인해 두세요.

Windows (PowerShell, 맨 앞 `&` 필수):

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\chrome-reserve
```

macOS:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir=$HOME/chrome-reserve
```

### 3. 실행

```bash
python reserve.py --now --dry-run
```

위 명령은 미리 테스트하는 용도입니다. 바로 실행하고, 마지막 예약 버튼은 누르지 않습니다.

```bash
python reserve.py
```

실제로 예약하려면 08:55쯤 이 명령을 실행해 두세요. 서버 시간 기준 09:00:00까지 기다렸다가 예약을 시작합니다.

설정은 `reserve.py` 위쪽 `설정` 부분에서 바꿀 수 있습니다. 대상 월(`TARGET_MONTH`), 예약 건수(`MAX_RESERVATIONS`), 버튼 문구 등이 있습니다.
