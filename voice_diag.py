# voice_diag.py
import os, sys, json, subprocess, time, traceback

VOSK_MODEL_DIR = os.environ.get("VOSK_MODEL_DIR", "/models/vosk-ko")
MIC_DEVICE     = os.environ.get("MIC_DEVICE", "plughw:0,0")  # 필요시 변경

print("[INFO] Python:", sys.executable)
print("[INFO] Python ver:", sys.version)
print("[INFO] VOSK_MODEL_DIR:", VOSK_MODEL_DIR)
print("[INFO] MIC_DEVICE:", MIC_DEVICE)

try:
    import vosk
    print("[OK] vosk import")
except Exception as e:
    print("[ERR] import vosk failed:", repr(e))
    sys.exit(2)

if not os.path.isdir(VOSK_MODEL_DIR):
    print("[ERR] model dir not found:", VOSK_MODEL_DIR)
    sys.exit(2)

try:
    model = vosk.Model(VOSK_MODEL_DIR)
    rec   = vosk.KaldiRecognizer(model, 16000)
    print("[OK] vosk model loaded")
except Exception as e:
    print("[ERR] vosk load failed:", repr(e))
    sys.exit(2)

cmd = ["arecord", "-D", MIC_DEVICE, "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
print("[INFO] arecord cmd:", " ".join(cmd))

try:
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
except Exception as e:
    print("[ERR] arecord spawn failed:", repr(e))
    sys.exit(2)

print("[READY] 말해보세요… (Ctrl+C 종료)")
try:
    while True:
        data = p.stdout.read(3200)  # 약 0.1초
        if not data:
            time.sleep(0.02)
            continue
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            txt = (res.get("text") or "").strip()
            if txt:
                print("[ASR]", txt)
        # partial은 시끄러우면 생략
except KeyboardInterrupt:
    pass
except Exception:
    print("[ERR] loop crashed:\n", traceback.format_exc())
finally:
    try: p.terminate()
    except: pass
print("[EXIT]")
