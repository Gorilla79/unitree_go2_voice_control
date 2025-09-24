#!/usr/bin/env python3
import os
import sys
import time
import threading
import subprocess
import json
from getpass import getpass

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
            data = p.stdout.read(3200)  # 약 100ms
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

# ===== NLP 매핑 =====
COMMAND_MAP = {
    "일어서": 1,
    "일어나": 1,
    "앉아": 3,
    "앉기": 3,
    "복구": 4,
    "균형": 5,
    "회복": 6,
    "정지": 7,
    "멈춰": 7,
    "인사": 8,
    "스트레칭": 9,
    "행복": 10,
    "하트": 11,
    "절": 12,
    "머리숙여": 12,
    "점프": 13,
}

def nlp_to_motion(txt: str):
    for k, v in COMMAND_MAP.items():
        if k in txt:
            return v
    return None

# ===== 메인 =====
def main():
    ctrl = Go2MotionController()
    ctrl.start()

    def on_text(txt):
        mid = nlp_to_motion(txt)
        if mid:
            ctrl.send_id(mid)
        else:
            print("[NLP] 매칭 없음")

    asr_loop(on_text)

if __name__ == "__main__":
    main()
