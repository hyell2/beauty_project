"""
딜레이더 매일 자동 업데이트 (Windows 작업 스케줄러가 실행)

순서:
    1) 뷰티컬리 수집 (kurly.py)
    2) 지그재그 뷰티 수집 (scrape.py --cat1 뷰티, 쿠폰 포함)
    3) 사이트 데이터(data.js, kurly.js)만 GitHub에 commit & push → 몇 분 뒤 Pages 사이트 갱신

- 수집이 실패하면 각 스크립트가 기존 데이터를 덮어쓰지 않고 종료하므로, 이전 데이터가 그대로 유지됨
- 로그: output/logs/update_YYYY-MM-DD_HHMM.log (30일 지난 로그는 자동 삭제)
- 직접 실행해도 됨:  ..\\venv\\Scripts\\python update.py
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PY = ROOT / "venv" / "Scripts" / "python.exe"
SITE_FILES = ["zigzag-compare/data.js", "zigzag-compare/kurly.js"]
LOG_DIR = HERE / "output" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG = LOG_DIR / f"update_{datetime.now():%Y-%m-%d_%H%M}.log"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if sys.stdout:
        try:
            print(line, flush=True)
        except Exception:
            pass


def run(args, cwd):
    """명령 실행, 출력은 로그 파일에 그대로 기록. 종료 코드 반환."""
    with open(LOG, "a", encoding="utf-8") as f:
        p = subprocess.run(args, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=ENV,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p.returncode


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main():
    t0 = time.time()
    log("=== 딜레이더 자동 업데이트 시작 ===")

    k = run([str(PY), "-u", "kurly.py"], HERE)
    log(f"뷰티컬리 수집 {'성공' if k == 0 else f'실패 (코드 {k})'}")
    z = run([str(PY), "-u", "scrape.py", "--cat1", "뷰티"], HERE)
    log(f"지그재그 수집 {'성공' if z == 0 else f'실패 (코드 {z})'}")

    if k != 0 and z != 0:
        log("둘 다 실패해서 GitHub 업로드를 건너뜀 (사이트는 이전 데이터 유지)")
        return 1

    # 사이트 데이터만 커밋 (작업 중인 다른 파일은 건드리지 않음)
    if not git("status", "--porcelain", "--", *SITE_FILES).stdout.strip():
        log("사이트 데이터 변경 없음 → 업로드 생략")
    else:
        msg = f"자동 업데이트: {datetime.now():%Y-%m-%d} 데이터"
        r = git("commit", "-m", msg, "--", *SITE_FILES)
        log(f"커밋: {'완료' if r.returncode == 0 else '실패 ' + (r.stderr or r.stdout).strip()[:300]}")
    r = git("push", "-q")
    # push 실패(인터넷 끊김 등) 시 커밋은 남아 있다가 다음 실행 때 같이 올라감
    log(f"GitHub 업로드: {'완료' if r.returncode == 0 else '실패 ' + r.stderr.strip()[:300]}")

    # 30일 지난 로그 정리
    for f in LOG_DIR.glob("update_*.log"):
        if time.time() - f.stat().st_mtime > 30 * 86400:
            f.unlink(missing_ok=True)

    log(f"=== 끝 ({(time.time() - t0) / 60:.0f}분) ===")
    return 0 if (k == 0 and z == 0) else 2


if __name__ == "__main__":
    sys.exit(main())
