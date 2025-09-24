# unitree_go2_voice_control

'pactl list short sources' 명령어를 통해 장치 채널 확인

'parec -d alsa_input.usb-Jieli_Technology_USB_Composite_Device_433130323234342E-00.mono-fallback   --format=s16le --rate=16000 --channels=1 > /tmp/test.raw'를 통해 마이크 모듈 정상 동작 확인
- 채널 1을 사용했음

'aplay -f S16_LE -r 16000 -c 1 /tmp/test.raw' 명령을 통해 마이크로 녹음한 소리 확인(소리가 들리면 정상 동작)

'MIC_DEVICE=pulse python voice_please.py'
- 마이크 자동 활성화 + 음성 제어 코드 실행

1. voice_please2.py는 최초 정상 동작을 확인한 코드
2. voice_please.py는 기능 고도화(사용 권장)
3. go2_motion.cpp는 최초의 동작 확인 코드
4. go2_motion2.cpp는 입력 신호 변환 성공 코드(사용 권장)
5. 그 외의 코드는 기능을 확인하기 위한 코드로 안될 시 사용해보는 것을 추천
