#!/usr/bin/env python3
"""tossctl 페이크 바이너리 (테스트 전용) — 픽스처 JSON을 stdout으로 출력한다.

TossctlRunner가 진짜 subprocess로 이 스크립트를 실행하므로, 인자 배열 전달·
타임아웃·재시도·JSON 파싱 경로가 실제 코드 그대로 검증된다.

환경변수:
  FAKE_TOSSCTL_FIXTURES  픽스처 디렉토리 (필수)
  FAKE_TOSSCTL_FAIL_FILE 카운터 파일 — 값이 n>0 이면 n회 exit 1 후 정상 동작
  FAKE_TOSSCTL_SLEEP     응답 전 sleep 초 (타임아웃 테스트)
  FAKE_TOSSCTL_DUMP_ARGS "1"이면 받은 argv를 JSON 배열로 출력 (인젝션 테스트)
"""
import json
import os
import sys
import time


def main() -> None:
    argv = sys.argv[1:]

    sleep_s = os.environ.get("FAKE_TOSSCTL_SLEEP")
    if sleep_s:
        time.sleep(float(sleep_s))

    fail_file = os.environ.get("FAKE_TOSSCTL_FAIL_FILE")
    if fail_file and os.path.exists(fail_file):
        n = int(open(fail_file).read().strip() or "0")
        if n > 0:
            with open(fail_file, "w") as f:
                f.write(str(n - 1))
            print("simulated failure", file=sys.stderr)
            sys.exit(1)

    if os.environ.get("FAKE_TOSSCTL_DUMP_ARGS") == "1":
        print(json.dumps(argv))
        return

    # push listen — JSONL 스트림 모드: 픽스처 파일을 한 줄씩 흘린다
    if argv[:2] == ["push", "listen"]:
        path = os.path.join(os.environ["FAKE_TOSSCTL_FIXTURES"], "push_listen.jsonl")
        with open(path, encoding="utf-8") as f:
            for line in f:
                sys.stdout.write(line)
                sys.stdout.flush()
        return

    # --output json 꼬리 제거 후, 플래그 전까지 최대 3개 토큰 — 3토큰(종목별)
    # 픽스처가 있으면 우선, 없으면 2토큰(명령 공통) 픽스처로 폴백
    if argv[-2:] == ["--output", "json"]:
        argv = argv[:-2]
    tokens: list[str] = []
    for a in argv:
        if a.startswith("-"):
            break
        tokens.append(a)
        if len(tokens) == 3:
            break
    fdir = os.environ["FAKE_TOSSCTL_FIXTURES"]
    path = None
    for depth in (3, 2, 1):
        candidate = os.path.join(fdir, "_".join(tokens[:depth]) + ".json")
        if os.path.exists(candidate):
            path = candidate
            break
    if path is None:
        print(f"unknown command fixture: {'_'.join(tokens)}", file=sys.stderr)
        sys.exit(2)
    with open(path, encoding="utf-8") as f:
        sys.stdout.write(f.read())


if __name__ == "__main__":
    main()
