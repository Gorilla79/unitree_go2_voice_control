#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import shlex
import signal
import getpass
import subprocess
from threading import Thread

# =============================
# 환경 설정 (필수: 경로/장치 확인)
# =============================
BIN_PATH        = "/home/unitree/unitree_sdk2-main/build/bin/go2_motion2"
NET_IFACE       = "eth0"               # go2_motion 첫 인자
MIC_DEVICE      = "plughw:0,0"         # arecord 장치명 (arecord -l 로 확인)
VOSK_MODEL_DIR  = "/models/vosk-ko"    # 한국어 Vosk 모델 경로

# 중복 실행 방지(음성이 같은 명령어를 연달아 내뱉는 흔들림 방지)
COMMAND_COOLDOWN_SEC = 2.0

# =============================
# 유틸
# =============================

def _check_model():
    if not os.path.isdir(VOSK_MODEL_DIR):
        print(f"[ERR] VOSK 모델 폴더가 없습니다: {VOSK_MODEL_DIR}", file=sys.stderr)
        sys.exit(2)

def _try_launch_go2_motion():
    """
    sudo 캐시(비밀번호)가 이미 있는 상태를 가정하고, -n(비대화)로 실행.
    실패하면 안내 후 종료. 성공하면 subprocess.Popen 반환(표준입력에 번호 전송용).
    """
    cmd = ["sudo", "-n", "-E", BIN_PATH, NET_IFACE]
    print(f"[INFO] launch: {' '.join(cmd)}")

    try:
        # 표준입력 연결(text 모드) — 번호를 써 넣기 위함
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
    except Exception as e:
        print(f"[ERR] go2_motion 실행 실패: {e}", file=sys.stderr)
        sys.exit(1)

    # 몇 초간 출력 체크하여, sudo 비밀번호 필요/실패 상황 감지
    start = time.time()
    boot_log = []
    while True:
        line = p.stdout.readline()
        if line:
            boot_log.append(line.rstrip("\n"))
            # go2_motion 배너가 보이면 성공으로 간주
            if "Go2 Motion" in line or "==== Go2 Motion" in line:
                break
        if (time.time() - start) > 3.0:
            # 3초 내 배너가 안 보이면 sudo 문제 가능성
            break

    # 프로세스가 이미 죽었는지 체크
    ret = p.poll()
    if ret is not None and ret != 0:
        print("[ERR] go2_motion 프로세스가 즉시 종료되었습니다.", file=sys.stderr)
        print("아래 출력을 확인하세요:")
        for l in boot_log:
            print("  ", l)
        print("\n[HINT] 먼저 터미널에서 한번 직접 실행해 sudo 캐시를 채워두세요:")
        print(f"  sudo {BIN_PATH} {NET_IFACE}")
        print("그 다음 이 파이썬 스크립트를 실행하면 자동으로 번호만 보내 동작합니다.")
        sys.exit(1)

    # 남은 초기 출력은 덤으로 다 비워줌(비동기로 계속 출력되므로 따로 쓰레드로 흘려버려도 됨)
    def _drain_stdout(proc):
        for line in proc.stdout:
            # 필요하면 로그를 보고 싶을 때 이쪽에서 print(line, end='')
            pass

    t = Thread(target=_drain_stdout, args=(p,), daemon=True)
    t.start()
    return p

def _send_number(proc, n: int):
    """go2_motion 입력창에 번호 + 개행을 써 넣는다."""
    if proc.poll() is not None:
        print("[ERR] go2_motion 프로세스가 종료되었습니다.", file=sys.stderr)
        return
    try:
        s = f"{int(n)}\n"
        proc.stdin.write(s)
        proc.stdin.flush()
        print(f"[ACTION] 실행: {int(n)}")
    except Exception as e:
        print(f"[ERR] 번호 전송 실패: {e}", file=sys.stderr)

def _send_quit(proc):
    """go2_motion에 q 전송하여 종료."""
    try:
        proc.stdin.write("q\n")
        proc.stdin.flush()
    except:
        pass

# =============================
# ASR 루프(arecord -> Vosk)
# =============================

def asr_loop(on_final_text):
    try:
        import vosk
    except ImportError:
        print("[ERR] pip install vosk", file=sys.stderr); sys.exit(2)

    _check_model()
    print(f"[INFO] load vosk model: {VOSK_MODEL_DIR}")
    model = vosk.Model(VOSK_MODEL_DIR)
    rec   = vosk.KaldiRecognizer(model, 16000)

    cmd = ["arecord","-D",MIC_DEVICE,"-f","S16_LE","-r","16000","-c","1","-t","raw"]
    print(f"[INFO] mic: {MIC_DEVICE}, sr=16000, ch=1")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    try:
        while True:
            data = p.stdout.read(3200)  # ~100ms
            if not data:
                time.sleep(0.01)
                continue
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                txt = (res.get("text") or "").strip()
                if txt:
                    on_final_text(txt)
            else:
                pres = json.loads(rec.PartialResult())
                ptxt = (pres.get("partial") or "").strip()
                if ptxt:
                    print(f"[~] {ptxt}")
    except KeyboardInterrupt:
        pass
    finally:
        try: p.terminate()
        except: pass

# =============================
# NLP: 한국어 → 번호 매핑
# =============================

def text_to_action_num(text: str):
    """
    인식된 한국어 문장에서 동작 번호(1~13)를 추출.
    일치 없음이면 None.
    """
    t = text.replace(" ", "")
    # 종료/종료어
    if any(k in t for k in ["종료","그만","끝","나가","나가기","quit","exit","큐","큐우","큐트"]):
        return "QUIT"

    # 번호 직접 말하기(한국어/숫자)
    _num_map = {
        "1":["1","일","하나","첫번째","첫번쨰","첫번재","원"],
        "2":["2","이","둘","두번째","투"],
        "3":["3","삼","셋","세번째","3번","썸","삼번","삼번쨰","삼번재","삼번재"],
        "4":["4","사","넷","네번째","포"],
        "5":["5","오","다섯","다섯번째","파이브"],
        "6":["6","육","여섯","여섯번째","식스"],
        "7":["7","칠","일곱","일곱번째","세븐"],
        "8":["8","팔","여덟","여덟번째","에잇","헬로","hello","인사"],
        "9":["9","구","아홉","아홉번째","나인","스트레칭","스트레치"],
        "10":["10","십","열","열번째","텐","행복","기쁨","컨텐트","콘텐트"],
        "11":["11","십일","열하나","일레븐","하트"],
        "12":["12","십이","열둘","트웰브","절","머리숙여","스크레이프","스크랩","스쿼트아님"],
        "13":["13","십삼","열셋","써틴","점프","점핑","프론트점프","앞으로점프"]
    }
    for k, ks in _num_map.items():
        for kw in ks:
            if kw in t:
                return int(k)

    # 자연어 키워드 매핑
    # 1~13: go2_motion 메뉴
    # 1. StandUp
    if any(k in t for k in ["일어서","서","일어나서","스탠드업","standup","일어"]):
        return 1
    # 2. StandDown
    if any(k in t for k in ["웅크려","앉지말고웅크려","스탠드다운","standdown","엎드려"]):
        return 2
    # 3. Sit
    if any(k in t for k in ["앉아","앉기","앉아라","시트","sit"]):
        return 3
    # 4. RiseSit
    if any(k in t for k in ["일어나","라이즈싯","risesit","복구해서서"]):
        return 4
    # 5. BalanceStand
    if any(k in t for k in ["균형","밸런스","밸런스스탠드","balance","balancestand"]):
        return 5
    # 6. RecoveryStand
    if any(k in t for k in ["복구","리커버리","리커버리스탠드","recover","recoverystand"]):
        return 6
    # 7. StopMove
    if any(k in t for k in ["정지","스톱","멈춰","멈추기","스탑","stop","그만해"]):
        return 7
    # 8. Hello
    if any(k in t for k in ["인사","헬로","hello","하이","안녕"]):
        return 8
    # 9. Stretch
    if any(k in t for k in ["스트레칭","스트레치","늘리기"]):
        return 9
    # 10. Content
    if any(k in t for k in ["행복","컨텐트","콘텐트","기쁨","해피"]):
        return 10
    # 11. Heart
    if any(k in t for k in ["하트","하트해","앞발하트","heart"]):
        return 11
    # 12. Scrape
    if any(k in t for k in ["절","머리숙여","스크레이프","scrape"]):
        return 12
    # 13. FrontJump
    if any(k in t for k in ["점프","점핑","프론트점프","앞으로점프","frontjump"]):
        return 13

    return None

# =============================
# 메인
# =============================

def main():
    # 1) go2_motion 실행(인터랙티브 대기)
    proc = _try_launch_go2_motion()

    print("[READY] 한국어로 명령하세요. (Ctrl+C 종료)")
    print("[GO2] [Safety] 평탄/무인/장애물 없는 환경에서 테스트하세요. 특수 동작은 이전 동작 완료 후 호출 권장.")
    print("[GO2] ")
    print("[GO2] ==== Go2 Motion (q=종료) ====")
    print("[GO2] 1. StandUp  2. StandDown  3. Sit  4. RiseSit  5. BalanceStand  6. RecoveryStand")
    print("[GO2] 7. StopMove  8. Hello  9. Stretch  10. Content  11. Heart  12. Scrape  13. FrontJump")
    print("[GO2] -----------------------------")
    print("[GO2] 음성으로 '앉아', '인사', '정지', '점프' 등으로 지시하세요. '종료'라고 말하면 끝냅니다.")
    print()

    last_sent_time = 0.0
    last_cmd = None

    def on_final_text(txt: str):
        nonlocal last_sent_time, last_cmd
        print(f"[ASR] {txt}")
        act = text_to_action_num(txt)
        if act == "QUIT":
            print("[INFO] 종료 명령 인식. 프로그램을 종료합니다.")
            _send_quit(proc)
            try:
                proc.wait(timeout=1.0)
            except:
                try: proc.terminate()
                except: pass
            os._exit(0)
            return

        if act is None:
            print("[NLP] 매칭 없음")
            return

        # 디바운스: 같은 명령을 연속으로 난사하지 않도록 최소 간격 유지
        now = time.time()
        if last_cmd == act and (now - last_sent_time) < COMMAND_COOLDOWN_SEC:
            print(f"[SKIP] {act} (cooldown)")
            return

        _send_number(proc, act)
        last_sent_time = now
        last_cmd = act

    try:
        asr_loop(on_final_text)
    except KeyboardInterrupt:
        pass
    finally:
        _send_quit(proc)
        try:
            proc.wait(timeout=1.0)
        except:
            try: proc.terminate()
            except: pass
        print("[EXIT] bye")

if __name__ == "__main__":
    main()