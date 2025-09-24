#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, json, math, subprocess, threading
import numpy as np
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

# ====== 환경 ======
VOSK_MODEL_DIR = os.environ.get("VOSK_MODEL_DIR", "/models/vosk-ko")
MIC_DEVICE     = os.environ.get("MIC_DEVICE", "plughw:0,0")  # arecord 권장(이미 검증)
GO2_IFACE      = os.environ.get("GO2_IFACE", "eth0")

HOME = os.path.expanduser("~")
LIB1 = f"{HOME}/unitree_sdk2-main/lib/aarch64"
LIB2 = f"{HOME}/unitree_sdk2-main/thirdparty/lib/aarch64"
LDVAL = f"{LIB1}:{LIB2}:{os.environ.get('LD_LIBRARY_PATH','')}"

BIN_TWIST = "/home/unitree/unitree_sdk2-main/build/bin/go2_action_server"  # 위 C++ 산출물
BIN_TW_WRAP = "/home/unitree/unitree_sdk2-main/build/bin/go2_twist_wrapper"  # 기존 teleop 래퍼(참조용)

# ====== 의도/문법 ======
COMMAND_GRAMMAR = json.dumps([
    "앉아","앉자","앉아줘","앉아줘요",
    "일어서","일어나","서라","서줘","일어서줘",
    "앞으로","뒤로",
    "앞으로 1미터","앞으로 2미터","앞으로 3미터",
    "뒤로 1미터","뒤로 2미터","뒤로 3미터",
    "인사","안녕","하트","멈춰","정지","스톱"
])

KNUM = {"영":0,"공":0,"하나":1,"한":1,"둘":2,"두":2,"셋":3,"세":3,"넷":4,"네":4,"다섯":5,"여섯":6,"일곱":7,"여덟":8,"아홉":9,"열":10}

def extract_distance_m(text: str):
    t = text.replace(" ","")
    m = re.search(r'(\d+(?:\.\d+)?)\s*(m|미터)', t)
    if m: return float(m.group(1))
    if "미터" in t or "m" in t:
        for k,v in KNUM.items():
            if k in t: return float(v)
    return None

def parse_intent(txt: str):
    t = txt.replace(" ","")
    if any(k in t for k in ["멈춰","정지","스톱"]): return ("stop",{})
    if any(k in t for k in ["앉","앉자","앉아","앉아줘","앉아줘요","앉히","앉기"]): return ("sit",{})
    if any(k in t for k in ["일어서","일어나","서라","서줘","일어서줘","서"]): return ("stand",{})
    if any(k in t for k in ["인사","안녕"]): return ("hello",{})
    if "하트" in t: return ("heart",{})
    if "앞" in t: return ("move",{"dir":+1})
    if "뒤" in t: return ("move",{"dir":-1})
    return (None,{})

# ====== sudo 실행기 ======
def run_go2_voice_twist(*args, timeout=4.0):
    """
    sudo -n -E BIN_TWIST [iface] args...
    - visudo:
      Defaults:unitree env_keep += "LD_LIBRARY_PATH"
      unitree ALL=(ALL) NOPASSWD: /home/unitree/unitree_sdk2-main/build/bin/go2_action_server
    """
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = LDVAL

    # NOPASSWD + env_keep 된 경우
    cmd = ["sudo","-n","-E", BIN_TWIST, GO2_IFACE] + [str(a) for a in args]
    try:
        out = subprocess.check_output(cmd, env=env, timeout=timeout, text=True, stderr=subprocess.STDOUT)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output
    except subprocess.CalledProcessError:
        return 1, ""
    except Exception as e:
        # 폴백: /usr/bin/env로 LD 주입 (sudoers에 /usr/bin/env 허용 시)
        try:
            cmd2 = ["sudo","/usr/bin/env", f"LD_LIBRARY_PATH={LDVAL}", BIN_TWIST, GO2_IFACE] + [str(a) for a in args]
            out = subprocess.check_output(cmd2, timeout=timeout, text=True, stderr=subprocess.STDOUT)
            return 0, out
        except Exception as e2:
            return 1, str(e2)

# ====== ASR (arecord → Vosk) ======
def asr_loop(on_text):
    try:
        import vosk
    except ImportError:
        print("[ERR] pip install vosk", file=sys.stderr); sys.exit(2)
    if not os.path.isdir(VOSK_MODEL_DIR):
        print(f"[ERR] VOSK 모델 없음: {VOSK_MODEL_DIR}", file=sys.stderr); sys.exit(2)

    model = vosk.Model(VOSK_MODEL_DIR)
    rec   = vosk.KaldiRecognizer(model, 16000)  # grammar 바이어스
    cmd = ["arecord","-D",MIC_DEVICE,"-f","S16_LE","-r","16000","-c","1","-t","raw"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            data = p.stdout.read(3200)  # 100ms
            if not data: time.sleep(0.01); continue
            if rec.AcceptWaveform(data):
                res = json.loads(rec.Result()); txt = (res.get("text") or "").strip()
                if txt: on_text(txt)
            # partial은 생략(필요시 추가)
    except KeyboardInterrupt:
        pass
    finally:
        try: p.terminate()
        except: pass

# ====== ROS2 노드: Twist 퍼블리셔 ======
class VoiceTeleop(Node):
    def __init__(self):
        super().__init__("voice_teleop")
        self.pub = self.create_publisher(Twist, "/unitree_go2/cmd_vel", 10)
        self.max_v = 0.4
        self.max_w = 0.6
        self.default_speed = 0.3  # m/s
        self.rate = self.create_rate(15)

    def publish_move(self, dir_sign=+1, meters=None, speed=None):
        """
        teleop와 동일 경로: Twist를 잠시 출판 → go2_twist_bridge → go2_twist_wrapper 호출
        """
        v = float(speed if speed is not None else self.default_speed) * float(dir_sign)
        v = max(-self.max_v, min(self.max_v, v))
        dur = 1.0
        if meters is not None:
            # 간단거리 모델: t = d / v
            meters = float(abs(meters))
            dur = max(0.2, meters / max(0.05, abs(v)))
        t0 = time.time()
        while rclpy.ok() and (time.time() - t0) < dur:
            msg = Twist()
            msg.linear.x = v
            msg.angular.z = 0.0
            self.pub.publish(msg)
            time.sleep(1.0/15.0)
        # 정지 펄스
        stop = Twist(); self.pub.publish(stop)

    def do_action(self, action: str):
        rc, out = run_go2_voice_twist(action)
        self.get_logger().info(f"[action:{action}] rc={rc} out={out.strip()[:120]}")

# ====== 메인 ======
def main():
    # teleop 브리지 방식: /unitree_go2/cmd_vel → go2_twist_wrapper 실행
    # (기존 코드 참고: 토픽 수신 시 서브프로세스로 1회 호출)  :contentReference[oaicite:1]{index=1}
    rclpy.init()
    node = VoiceTeleop()

    def on_text(txt: str):
        print("[ASR]", txt)
        intent, payload = parse_intent(txt)
        dist = extract_distance_m(txt)
        if intent is None:
            print("[NLP] no match"); return
        if intent == "move":
            node.publish_move(dir_sign=payload.get("dir", +1), meters=dist, speed=None)
        elif intent in ["sit","stand","hello","heart","stop"]:
            node.do_action(intent)
        else:
            print("[NLP] unhandled:", intent)

    try:
        th = threading.Thread(target=asr_loop, args=(on_text,), daemon=True)
        th.start()
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
