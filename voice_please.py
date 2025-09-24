#!/usr/bin/env python3
import os
import sys
import re
import time
import threading
import subprocess
import json
from getpass import getpass
from collections import defaultdict

# ===== 설정 =====
BIN_DIR  = "/home/unitree/unitree_sdk2-main/build/bin"
BIN_PATH = os.path.join(BIN_DIR, "go2_motion2")
IFACE    = "eth0"   # 네트워크 인터페이스명
VOSK_MODEL_DIR = "/models/vosk-ko"
MIC_DEVICE = os.environ.get("MIC_DEVICE", "pulse")  # pulseaudio 연결

# ===== sudo 인증 캐시 확보 =====
def ensure_sudo_cache():
    ok = subprocess.run(["sudo","-n","-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if ok.returncode == 0:
        return
    pw = getpass("[SUDO] password: ")
    p = subprocess.Popen(["sudo","-S","-v"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    p.communicate(pw + "\n")
    if p.returncode != 0:
        print("[ERR] sudo auth failed", file=sys.stderr)
        sys.exit(1)

# ===== Go2 Motion Controller =====
class Go2MotionController:
    def __init__(self, iface=IFACE, bin_path=BIN_PATH):
        self.iface = iface
        self.bin_path = bin_path
        self.proc = None
        self._running = False
        self._pump = None
        # 아주 단순한 자세 추적(앉음/서있음). 실제 피드백이 없어서 우리가 보낸 명령 기준으로만 저장.
        self.posture = "unknown"   # "sit" | "stand" | "unknown"

    def start(self):
        ensure_sudo_cache()
        if not os.path.isfile(self.bin_path):
            raise FileNotFoundError(f"BIN not found: {self.bin_path}")
        cmd = ["sudo","-E", self.bin_path, self.iface]
        print(f"[INFO] launch: {' '.join(cmd)}")
        self.proc = subprocess.Popen(cmd,
                                     cwd=BIN_DIR,
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT,
                                     text=True,
                                     bufsize=1)
        self._running = True
        self._pump = threading.Thread(target=self._pump_stdout, daemon=True)
        self._pump.start()
        time.sleep(1.0)
        print("[READY] 음성 명령 대기 시작.")

    def _pump_stdout(self):
        for line in self.proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()

    def send_id(self, motion_id: int):
        if not self.proc or self.proc.poll() is not None:
            print("[ERR] not running"); return
        self.proc.stdin.write(f"{int(motion_id)}\n")
        self.proc.stdin.flush()
        print(f"[SEND] {motion_id}")
        # 보낸 명령 기반으로 posture 추정 업데이트
        if motion_id in (1, 4, 5, 6):   # StandUp / RiseSit / BalanceStand / RecoveryStand
            self.posture = "stand"
        elif motion_id == 3:            # Sit
            self.posture = "sit"
        elif motion_id == 7:            # StopMove
            pass

    def send_go(self):
        if not self.proc or self.proc.poll() is not None:
            print("[ERR] not running"); return
        self.proc.stdin.write("/go\n")
        self.proc.stdin.flush()
        print("[SEND] /go")

    def stop(self):
        self._running = False
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.write("q\n")
                self.proc.stdin.flush()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.terminate()
        finally:
            self.proc = None
            print("[DONE] stopped.")

# ===== 한글 정규화 유틸 =====
_JOSA_RE = re.compile(r"(은|는|이|가|을|를|에|에서|으로|로|와|과|한테|에게|께|께서|에도|에도|까지|부터|밖에|마다|처럼|같이|인데|인데요|인데다|인데도)$")
_ENDING_RE = re.compile(r"(해줘|해주라|해줘요|해주세요|해|해라|해라요|해요|해라구|해라구요|해달라|하자|하시오|하세|하세요|해보자|해봐|해봐요|해볼래|해줄래|해줄수있어|해줄수있니|해줄수있나요)$")
_FILLERS = ("그냥","저기","음","어","에","아","그","저","이제","그러면","근데","자")
def normalize_korean(s: str) -> str:
    s = s.strip()
    # 공백 제거 + 소문자화(영문 섞였을 때만 영향)
    s = s.lower()
    # 보편적 구두점 제거
    s = re.sub(r"[^\w가-힣\s/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # 군더더기 토큰 제거
    toks = [t for t in s.split() if t not in _FILLERS]
    s = " ".join(toks)
    # 조사/끝맺음 제거(토큰별로 뒤에서 한 번만 삭제)
    def strip_tail(token):
        t = _ENDING_RE.sub("", token)
        t = _JOSA_RE.sub("", t)
        return t
    toks = [strip_tail(t) for t in s.split()]
    s = " ".join([t for t in toks if t])
    return s

# ===== NLP 매핑(스코어링 기반) =====
# 각 의도에 가중치 단어/정규식 정의
INTENTS = {
    # 번호: {키워드/패턴: 가중치}
    1: {  # StandUp(관절잠금 서기)
        r"일어(서|나)": 2.0,
        r"서": 1.5, r"일으키": 1.5, r"기립": 2.0
    },
    4: {  # RiseSit(앉은 자세 복구 = 앉아있을 때 일어서기)
        r"일어(서|나)": 2.0, r"복구": 1.8, r"일으켜": 1.8
    },
    2: {  # StandDown
        r"엎드려": 2.0, r"누워": 2.0, r"빵": 1.5
    },
    3: {  # Sit
        r"앉": 2.0, r"앉기": 2.0, r"앉혀": 1.5
    },
    5: {  # BalanceStand
        r"균형": 2.0, r"밸런스": 2.0, r"밸런싱": 2.0, r"밸런스서": 2.0
    },
    6: {  # RecoveryStand
        r"회복": 2.0, r"리커버": 1.5, r"넘어.*복구": 2.0, r"복구": 1.5
    },
    7: {  # StopMove
        r"정지": 2.0, r"멈춰": 2.0, r"멈추": 2.0, r"스탑": 1.5, r"그만": 1.5
    },
    8: {  # Hello
        r"인사": 2.0, r"헬로": 1.8, r"안녕(하세|)": 1.5, r"하이": 1.5, r"손.*흔": 1.5
    },
    9: {  # Stretch
        r"스트레칭": 2.0, r"기지개": 1.5, r"쭉": 1.5
    },
    10: { # Content
        r"행복": 2.0, r"기뻐": 1.5, r"해피": 1.5, r"응원": 2.0
    },
    11: { # Heart
        r"하트": 2.0, r"하뚜": 1.7, r"하트해": 2.0, r"사랑해": 2.0, r"사랑": 2.0
    },
    12: { # Scrape
        r"(절|머리\s*숙|사죄|사과)": 2.0, r"인사.*깊": 1.2, r"용서": 2.0, r"빌어": 2.0
    },
    13: { # FrontJump
        r"점프": 2.0, r"뛰어": 1.8, r"점프해": 2.0
    },
    # 특수 트리거 /go (텍스트로 보내지 않고 별도 핸들)
    "GO": {
        r"(출발|시작|가자|레디고|렛츠고|레츠고)": 2.0
    }
}

def score_intents(text_norm: str):
    scores = defaultdict(float)
    for intent, pats in INTENTS.items():
        if intent == "GO":
            continue
        for pat, w in pats.items():
            if re.search(pat, text_norm):
                scores[intent] += w
    # 동률/낮은 점수 필터링은 호출부에서 처리
    return scores

def detect_go(text_norm: str) -> bool:
    for pat, w in INTENTS["GO"].items():
        if re.search(pat, text_norm):
            return True
    return False

# ===== ASR (Vosk) =====
def asr_loop(on_final_text):
    try:
        import vosk
    except ImportError:
        print("[ERR] pip install vosk", file=sys.stderr); sys.exit(2)
    if not os.path.isdir(VOSK_MODEL_DIR):
        print(f"[ERR] VOSK 모델 폴더가 없습니다: {VOSK_MODEL_DIR}", file=sys.stderr)
        sys.exit(2)

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
                time.sleep(0.01); continue
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                txt = (res.get("text") or "").strip()
                if txt:
                    print(f"[ASR] {txt}")
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

# ===== 메인 =====
def main():
    ctrl = Go2MotionController()
    ctrl.start()

    last_fire_ts = 0.0
    cooldown_sec = 1.5     # 같은/유사 명령 연타 방지
    last_intent = None

    def choose_stand_variant(base_intent: int) -> int:
        # “일어서/일어나”를 들었을 때, 앉아있는 상태면 4(RiseSit), 아니면 1(StandUp)
        if base_intent == 1:
            return 4 if ctrl.posture == "sit" else 1
        return base_intent

    def on_text(txt_raw: str):
        nonlocal last_fire_ts, last_intent
        t0 = time.time()
        if (t0 - last_fire_ts) < cooldown_sec:
            print("[DEBOUNCE] 너무 빠른 연속 명령 → 무시")
            return

        text_norm = normalize_korean(txt_raw)
        if not text_norm:
            print("[NLP] 공백/무효")
            return

        # 특수 트리거(/go)
        if detect_go(text_norm):
            ctrl.send_go()
            last_fire_ts = time.time()
            last_intent = "GO"
            return

        scores = score_intents(text_norm)
        if not scores:
            print("[NLP] 매칭 없음")
            return

        # 최고 점수 의도 채택 (최소 임계치)
        best_id, best_score = max(scores.items(), key=lambda kv: kv[1])
        if best_score < 1.2:  # 너무 약하면 무시
            print(f"[NLP] 약한 신호({best_id}:{best_score:.2f}) → 무시")
            return

        # 같은 의도를 연달아 반복하면 한 번은 무시
        if last_intent == best_id and (time.time() - last_fire_ts) < (cooldown_sec * 2):
            print("[NLP] 같은 의도 반복 → 무시")
            return

        chosen = choose_stand_variant(best_id)
        ctrl.send_id(chosen)
        last_fire_ts = time.time()
        last_intent = best_id

    try:
        asr_loop(on_text)
    finally:
        ctrl.stop()

if __name__ == "__main__":
    main()
