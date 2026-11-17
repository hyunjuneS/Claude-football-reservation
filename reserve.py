"""
용인시 공공체육시설 - 수지체육공원축구장 자동 예약 스크립트

동작 순서
  1. 이미 로그인된 Chrome 에 붙는다 (CDP, 원격 디버깅 포트).
  2. 예약 목록 페이지를 열어 두고 서버 시간 기준 09:00:00 까지 대기한다.
  3. 09:00 이 되면 목록을 새로고침하고 '수지체육공원축구장' 행의 [상세보기] 를 누른다.
  4. 대상 월의 첫째 주부터 월요일을 차례로 확인해서
     20:00~22:00 시간대가 예약 가능하면 선택하고 예약 신청한다.

사용법은 README.md 참고.
  python reserve.py            # 09:00 까지 기다렸다가 실제 예약
  python reserve.py --now      # 기다리지 않고 바로 실행 (테스트용)
  python reserve.py --dry-run  # 마지막 '예약' 버튼은 누르지 않음 (테스트용)
"""

import argparse
import calendar
import re
import sys
import time
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# 설정 (필요하면 여기만 고치세요)
# ---------------------------------------------------------------------------
LIST_URL = (
    "https://publicsports.yongin.go.kr/publicsports/sports/"
    "selectFcltyRceptResveListU.do?key=4290&searchResveType=RESIDENTRESVE"
)
CDP_URL = "http://127.0.0.1:9222"          # Chrome 원격 디버깅 주소
FACILITY_NAME = "수지체육공원축구장"
OPEN_TIME = "09:00:00"                      # 예약 오픈 시각 (서버 시간 기준)
WEEKDAY = 0                                 # 0=월요일 ... 6=일요일
SLOT_START, SLOT_END = "20", "22"           # 20:00 ~ 22:00
TARGET_MONTH = None                         # 예: "2026-12". None 이면 이번 달
MAX_RESERVATIONS = 1                        # 몇 건까지 예약할지
RETRY_SECONDS = 60                          # 09:00 이후 상세보기가 안 열리면 재시도할 시간

# 화면 문구가 다르면 아래 후보에 추가하세요.
DETAIL_TEXTS = ["상세보기", "상세", "예약하기"]
NEXT_MONTH_TEXTS = ["다음달", "다음 달", "다음", "next", ">", "▶"]
SUBMIT_TEXTS = ["예약신청", "예약 신청", "신청하기", "예약하기", "신청", "예약"]
UNAVAILABLE_WORDS = ["마감", "불가", "완료", "예약됨", "종료"]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 시간 맞추기
# ---------------------------------------------------------------------------
def server_offset(page) -> timedelta:
    """서버 Date 헤더와 내 PC 시계 차이 (서버 - 로컬)."""
    try:
        t0 = datetime.now().astimezone()
        resp = page.request.head(LIST_URL)
        t1 = datetime.now().astimezone()
        server = parsedate_to_datetime(resp.headers["date"]).astimezone()
        local_mid = t0 + (t1 - t0) / 2
        # Date 헤더는 초 단위 절삭이므로 평균 0.5초 보정
        offset = server + timedelta(milliseconds=500) - local_mid
        log(f"서버 시간 차이: {offset.total_seconds():+.3f}초")
        return offset
    except Exception as e:  # noqa: BLE001
        log(f"서버 시간 확인 실패({e}) - PC 시계 사용")
        return timedelta(0)


def wait_until_open(page) -> None:
    offset = server_offset(page)
    h, m, s = map(int, OPEN_TIME.split(":"))
    now_server = datetime.now().astimezone() + offset
    target = now_server.replace(hour=h, minute=m, second=s, microsecond=0)
    if now_server >= target:
        log("이미 오픈 시각이 지났습니다. 바로 진행합니다.")
        return
    log(f"서버 시간 {target:%H:%M:%S} 까지 대기 (로컬 기준 {(target - offset):%H:%M:%S.%f})")
    keepalive_at = time.monotonic() + 600
    while True:
        remain = (target - (datetime.now().astimezone() + offset)).total_seconds()
        if remain <= 0:
            break
        # 오래 기다리는 동안 세션 만료 방지용 새로고침 (10분마다, 오픈 1분 전부터는 안 함)
        if remain > 60 and time.monotonic() > keepalive_at:
            page.reload(wait_until="domcontentloaded")
            keepalive_at = time.monotonic() + 600
        time.sleep(min(remain, 30) if remain > 1 else 0.005)
    log("오픈 시각 도달!")


# ---------------------------------------------------------------------------
# 페이지 조작 헬퍼
# ---------------------------------------------------------------------------
def click_first(scope, texts, exact=False, timeout=1500) -> bool:
    for t in texts:
        loc = scope.get_by_role("button", name=t, exact=exact)
        if loc.count() == 0:
            loc = scope.get_by_role("link", name=t, exact=exact)
        if loc.count() == 0:
            loc = scope.get_by_text(t, exact=True)
        if loc.count() > 0:
            try:
                loc.first.click(timeout=timeout)
                log(f"클릭: '{t}'")
                return True
            except PWTimeout:
                continue
    return False


def open_detail(page, context):
    """목록 새로고침 → 시설 행의 [상세보기] 클릭. 새 탭이 열리면 그 탭을 반환."""
    deadline = time.monotonic() + RETRY_SECONDS
    while time.monotonic() < deadline:
        page.reload(wait_until="domcontentloaded")
        row = page.locator("tr, li, div.list_item, div.item").filter(has_text=FACILITY_NAME)
        if row.count() == 0:
            log(f"'{FACILITY_NAME}' 항목을 못 찾음. 재시도...")
            time.sleep(0.3)
            continue
        row = row.last  # 가장 안쪽(구체적인) 요소
        pages_before = len(context.pages)
        if click_first(row, DETAIL_TEXTS):
            page.wait_for_load_state("domcontentloaded")
            if len(context.pages) > pages_before:  # 새 창으로 열린 경우
                new_page = context.pages[-1]
                new_page.wait_for_load_state("domcontentloaded")
                return new_page
            return page
        log("상세보기 버튼이 아직 없음. 재시도...")
        time.sleep(0.3)
    raise RuntimeError("상세보기를 열지 못했습니다.")


def target_dates() -> list[date]:
    if TARGET_MONTH:
        y, m = map(int, TARGET_MONTH.split("-"))
    else:
        today = date.today()
        y, m = today.year, today.month
    days = calendar.monthrange(y, m)[1]
    return [date(y, m, d) for d in range(1, days + 1) if date(y, m, d).weekday() == WEEKDAY]


def find_day(page, d: date):
    """달력에서 날짜 요소 찾기: 먼저 yyyymmdd 가 들어간 속성, 없으면 숫자 텍스트."""
    keys = [d.strftime("%Y%m%d"), d.strftime("%Y-%m-%d"), d.strftime("%Y.%m.%d")]
    for k in keys:
        loc = page.locator(
            f"[onclick*='{k}'], [href*='{k}'], [data-date*='{k}'], "
            f"[data-day*='{k}'], [id*='{k}'], [value*='{k}']"
        )
        if loc.count() > 0:
            return loc.first
    cal = page.locator("table.calendar, .calendar, .cal, #calendar, table").first
    loc = cal.locator("a, button, td, span").filter(
        has_text=re.compile(rf"^\s*{d.day}\s*$")
    )
    return loc.first if loc.count() > 0 else None


def pick_slot(page) -> bool:
    """20:00~22:00 시간대 선택. 예약 불가 표시면 False."""
    pat = re.compile(rf"{SLOT_START}\s*:\s*00\s*[~\-]\s*{SLOT_END}\s*:\s*00")
    cands = page.locator("label, li, tr, td, a, button, span, div").filter(has_text=pat)
    if cands.count() == 0:
        return False
    slot = cands.last  # 가장 안쪽 요소
    text = slot.inner_text()
    if any(w in text for w in UNAVAILABLE_WORDS):
        log(f"  시간대 예약 불가: {text.strip()[:40]}")
        return False
    # 체크박스/라디오가 있으면 그걸 선택
    box = slot.locator("input[type=checkbox], input[type=radio]")
    if box.count() == 0:
        row = page.locator("tr, li").filter(has_text=pat).last
        box = row.locator("input[type=checkbox], input[type=radio]")
    if box.count() > 0:
        if box.first.is_disabled():
            log("  시간대 체크박스 비활성 (예약 불가)")
            return False
        box.first.check()
    else:
        slot.click()
    log(f"  시간대 선택: {text.strip()[:40]}")
    return True


def try_reserve(page, d: date, dry_run: bool) -> bool:
    el = find_day(page, d)
    if el is None:
        # 다른 달이 표시 중일 수 있으니 '다음달' 한 번 눌러보고 재시도
        if click_first(page, NEXT_MONTH_TEXTS, exact=True):
            page.wait_for_load_state("domcontentloaded")
            el = find_day(page, d)
    if el is None:
        log(f"{d} : 달력에서 날짜를 못 찾음")
        return False
    cls = (el.get_attribute("class") or "") + " " + el.inner_text()
    if any(w in cls for w in UNAVAILABLE_WORDS) or "disabled" in cls:
        log(f"{d} : 예약 불가 날짜")
        return False
    el.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(300)  # 시간대 목록 ajax 로딩 여유
    log(f"{d} 선택")
    if not pick_slot(page):
        log(f"{d} : 20~22시 예약 불가")
        return False
    if dry_run:
        log(f"{d} : [DRY-RUN] 여기서 예약 버튼을 누릅니다 (실제로는 안 누름)")
        return True
    if not click_first(page, SUBMIT_TEXTS):
        log("예약 버튼을 못 찾음")
        return False
    page.wait_for_load_state("domcontentloaded")
    log(f"{d} 20:00~22:00 예약 신청 완료 (화면에서 결과를 확인하세요)")
    return True


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true", help="09시까지 기다리지 않고 즉시 실행")
    ap.add_argument("--dry-run", action="store_true", help="최종 예약 버튼은 누르지 않음")
    args = ap.parse_args()

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:  # noqa: BLE001
            log(f"Chrome 연결 실패: {e}")
            log(f"브라우저 주소창에 {CDP_URL}/json/version 을 열어 보세요.")
            log("내용이 안 나오면 Chrome 을 모두 종료한 뒤 README 의 명령으로 다시 실행하세요.")
            return 1
        context = browser.contexts[0]
        # 확인창(alert/confirm)은 모두 '확인'
        def on_dialog(dlg):
            log(f"알림창: {dlg.message}")
            dlg.accept()
        context.on("page", lambda pg: pg.on("dialog", on_dialog))
        for pg in context.pages:
            pg.on("dialog", on_dialog)

        page = context.new_page()
        page.on("dialog", on_dialog)
        page.goto(LIST_URL, wait_until="domcontentloaded")
        if "login" in page.url.lower():
            log("로그인 페이지로 이동됨 - 이 Chrome 에서 먼저 로그인하세요.")
            return 1

        if not args.now:
            wait_until_open(page)

        detail = open_detail(page, context)
        detail.on("dialog", on_dialog)
        list_url = detail.url

        done = 0
        for d in target_dates():
            if d < date.today():
                continue
            try:
                if try_reserve(detail, d, args.dry_run):
                    done += 1
                    if done >= MAX_RESERVATIONS:
                        break
                    detail.goto(list_url, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001
                log(f"{d} : 오류 {e}")
                detail.goto(list_url, wait_until="domcontentloaded")

        log(f"종료: {done}건 {'(dry-run)' if args.dry_run else ''}")
        return 0 if done else 2


if __name__ == "__main__":
    sys.exit(main())
