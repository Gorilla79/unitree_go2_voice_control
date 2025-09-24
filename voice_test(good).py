#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, time, json, subprocess

# === 환경 ===
VOSK_MODEL_DIR = os.environ.get("VOSK_MODEL_DIR", "/models/vosk-ko")
MIC_DEVICE     = os.environ.get("MIC_DEVICE", "plughw:0,0")  # arecord 사용

def main():
    try:
        import vosk
    except ImportError:
        print("[ERR] 'pip install vosk' 먼저 설치하세요.", file=sys.stderr)
        sys.exit(2)

    if not os.path.isdir(VOSK_MODEL_DIR):
        print(f"[ERR] VOSK 모델 폴더가 없습니다: {VOSK_MODEL_DIR}", file=sys.stderr)
        sys.exit(2)

    print(f"[INFO] load vosk model: {VOSK_MODEL_DIR}")
    model = vosk.Model(VOSK_MODEL_DIR)
    rec   = vosk.KaldiRecognizer(model, 16000)
    rec.SetWords(True)  # 단어 단위 타임스탬프(선택)

    # arecord로 16kHz/mono/16bit PCM 스트림 받기 (로스 없음, 딜레이 적음)
    cmd = ["arecord","-D",MIC_DEVICE,"-f","S16_LE","-r","16000","-c","1","-t","raw"]
    print(f"[INFO] mic: {MIC_DEVICE}, sr=16000, ch=1")
    print("[READY] 한국어로 말씀하세요. (Ctrl+C 종료)")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    try:
        partial_last = ""
        while True:
            data = p.stdout.read(3200)  # 약 100ms 분량(16kHz * 0.1s * 2바이트)
            if not data:
                time.sleep(0.01)
                continue

            # 문장(발화) 단위로 신뢰도 높게 완성되면 출력
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result())
                txt = (res.get("text") or "").strip()
                if txt:
                    print(f"[ASR] {txt}")
                partial_last = ""
            else:
                # 필요하면 부분 출력도 사용 (가시성 ↑)
                pres = json.loads(rec.PartialResult())
                ptxt = (pres.get("partial") or "").strip()
                if ptxt and ptxt != partial_last:
                    print(f"[~] {ptxt}", end="\r", flush=True)
                    partial_last = ptxt

    except KeyboardInterrupt:
        pass
    finally:
        try: p.terminate()
        except: pass
        print("\n[EXIT] bye")

if __name__ == "__main__":
    main()
