import os
import sys
import time
import threading
import subprocess
from getpass import getpass

BIN_DIR  = "/home/unitree/unitree_sdk2-main/build/bin"
BIN_PATH = os.path.join(BIN_DIR, "go2_motion2")
IFACE    = "eth0"   # 네트워크 인터페이스명

class Go2MotionController:
    def __init__(self, iface=IFACE, bin_path=BIN_PATH, sudo_pw=None):
        self.iface = iface
        self.bin_path = bin_path
        self.proc = None
        self._out_thread = None
        self._running = False
        self.sudo_pw = "123"

    def start(self):
        if not os.path.isfile(self.bin_path):
            raise FileNotFoundError(f"BIN not found: {self.bin_path}")

        # sudo -S 로 표준입력으로 비밀번호 전달
        cmd = ["sudo", "-S", "-E", self.bin_path, self.iface]
        print(f"[INFO] launch: {' '.join(cmd)}  (첫 실행만 sudo 인증 필요)")

        # stdout/stderr를 읽어 화면에 실시간 미러링
        self.proc = subprocess.Popen(
            cmd,
            cwd=BIN_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # 비번이 없다면 사용자에게 한 번 물어봄
        if self.sudo_pw is None:
            self.sudo_pw = getpass("[SUDO] password: ")

        # sudo 비번 전달
        try:
            self.proc.stdin.write(self.sudo_pw + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            print(f"[ERR] failed to send sudo password: {e}")
            self.stop()
            raise

        # 출력 미러링 스레드 시작
        self._running = True
        self._out_thread = threading.Thread(target=self._pump_stdout, daemon=True)
        self._out_thread.start()

        # 프로그램이 메뉴를 띄울 시간을 잠깐 줌
        time.sleep(1.0)
        print("[READY] 번호를 보낼 준비 완료.")

    def _pump_stdout(self):
        try:
            for line in self.proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
        except Exception as e:
            if self._running:
                print(f"[WARN] stdout pump error: {e}")

    def send_id(self, motion_id: int):
        """메뉴 번호(정수)를 실행. 예: 8(Hello), 3(Sit) 등"""
        if self.proc is None or self.proc.poll() is not None:
            print("[ERR] process not running.")
            return
        s = f"{int(motion_id)}\n"
        self.proc.stdin.write(s)
        self.proc.stdin.flush()
        print(f"[SEND] {motion_id}")

    def special_go(self):
        """특수 신호(/go) 전달 (StandDown→StandUp, Sit→RiseSit 트리거)"""
        if self.proc is None or self.proc.poll() is not None:
            print("[ERR] process not running.")
            return
        self.proc.stdin.write("/go\n")
        self.proc.stdin.flush()
        print("[SEND] /go")

    def stop(self):
        """메뉴 종료(q) 후 프로세스 종료"""
        self._running = False
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.stdin.write("q\n")
                    self.proc.stdin.flush()
                except Exception:
                    pass
                # 종료 대기
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.terminate()
        finally:
            self.proc = None
            print("[DONE] stopped.")

def demo():
    ctrl = Go2MotionController()
    ctrl.start()

    # 예시: 8=Hello, 3=Sit, (특수신호) /go → RiseSit, 1=StandUp
    time.sleep(2.0); ctrl.send_id(8)     # Hello
    time.sleep(3.0); ctrl.send_id(3)     # Sit
    time.sleep(2.0); ctrl.special_go()   # /go → RiseSit
    time.sleep(3.0); ctrl.send_id(1)     # StandUp

    # 종료
    time.sleep(2.0)
    ctrl.stop()

if __name__ == "__main__":
    demo()