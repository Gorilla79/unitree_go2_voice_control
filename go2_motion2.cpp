#include <iostream>
#include <sstream>
#include <string>
#include <csignal>
#include <chrono>
#include <thread>
#include <atomic>

// Unitree SDK2 (Go2 V2.0)
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/go2/sport/sport_client.hpp>

using unitree::robot::go2::SportClient;

static inline void msleep(int ms){ std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }

// ===== 전역 상태 =====
static std::atomic<bool> stop_flag(false);
static std::atomic<bool> special_trigger(false);  // 특수 신호(SIGUSR1 또는 /go)
static std::atomic<bool> pending_standup(false);  // 규칙 A: StandDown 후 대기 → 특수신호 시 StandUp
static std::atomic<bool> pending_risesit(false);  // 규칙 B: Sit 후 대기 → 특수신호 시 RiseSit

// SIGINT: 종료
void on_sigint(int){ stop_flag = true; }
// SIGUSR1: 특수 신호 트리거
void on_sigusr1(int){ special_trigger = true; }

// 안전을 위한 사전 균형 서기(점프류 호출 전 권장)
static inline void pre_balance(SportClient& c){ c.BalanceStand(); msleep(600); }

// 규칙 A/B: 특수 신호 수신 시, 대기 중 자동 동작을 수행
static void process_special_triggers(SportClient& cli){
  if (!special_trigger.exchange(false)) return;

  // Sit 이후 RiseSit가 먼저, 그 다음 StandDown 이후 StandUp 순으로 처리
  if (pending_risesit.exchange(false)) {
    int32_t ret = cli.RiseSit();   // 앉은 자세 복구
    std::cout << "[TRIGGER] RiseSit => ret=" << ret << "\n";
    msleep(400);
  }
  if (pending_standup.exchange(false)) {
    int32_t ret = cli.StandUp();   // 관절잠금 서기
    std::cout << "[TRIGGER] StandUp => ret=" << ret << "\n";
    msleep(400);
  }
}

// 메뉴 항목
struct Item { int id; const char* name; const char* note; };
static const Item MENU[] = {
  { 1,"StandUp",       "관절 잠금 서기" },
  { 2,"StandDown",     "관절 잠금 웅크리기  → (특수 신호 시 StandUp)" },
  { 3,"Sit",           "앉기(특수동작) → (특수 신호 시 RiseSit)" },
  { 4,"RiseSit",       "앉은 자세에서 복구" },
  { 5,"BalanceStand",  "잠금 해제 균형서기" },
  { 6,"RecoveryStand", "넘어짐/웅크림 복구" },
  { 7,"StopMove",      "현재 동작 정지·파라미터 리셋" },
  { 8,"Hello",         "인사" },
  { 9,"Stretch",       "스트레칭" },
  { 10,"Content",      "행복 표현" },
  { 11,"Heart",        "앞발 하트" },
  { 12,"Scrape",       "절/머리숙이기" },
  { 13,"FrontJump",    "전방 점프 (사전 균형서기)" }
};

static void print_menu(){
  std::cout << "\n==== Go2 Motion (q=종료) ====\n";
  for (auto &m: MENU) std::cout << m.id << ". " << m.name << " - " << m.note << "\n";
  std::cout << "-----------------------------\n";
  std::cout << "[특수 신호] ➊ 다른 터미널: kill -USR1 <PID>  ➋ 여기 입력창: /go\n";
  std::cout << "=============================\n";
}

// 단일 동작 실행 함수
static int run_motion_id(SportClient& cli, int id){
  switch(id){
    case 1:  return cli.StandUp();
    case 2:  { int r=cli.StandDown(); if(r==0) pending_standup=true; return r; }
    case 3:  { int r=cli.Sit();      if(r==0) pending_risesit=true; return r; }
    case 4:  return cli.RiseSit();
    case 5:  return cli.BalanceStand();
    case 6:  return cli.RecoveryStand();
    case 7:  return cli.StopMove();
    case 8:  return cli.Hello();
    case 9:  return cli.Stretch();
    case 10: return cli.Content();
    case 11: return cli.Heart();
    case 12: return cli.Scrape();
    case 13: pre_balance(cli); return cli.FrontJump();
    default:
      std::cout << "[WARN] 알 수 없는 번호: " << id << "\n";
      return -1;
  }
}

int main(int argc, char** argv){
  signal(SIGINT,  on_sigint);
  signal(SIGUSR1, on_sigusr1);   // 특수 신호 등록

  if (argc < 2){
    std::cerr << "Usage: " << argv[0] << " <networkInterface> [ids...]\n"
              << "  e.g.) " << argv[0] << " eth0 8\n";
    return 1;
  }
  const std::string ifname = argv[1];

  // Go2 V2.0 권고 초기화: 네트워크 인터페이스 명시
  unitree::robot::ChannelFactory::Instance()->Init(0, ifname);

  SportClient cli;
  cli.SetTimeout(10.0f);
  cli.Init(); // 반환값 없음

  std::cout << "[Safety] 평탄/무인/장애물 없는 환경에서 테스트하세요. 특수 동작은 이전 동작 완료 후 호출 권장.\n";

  // ---------- (1) 비대화식: 추가 인자들로 바로 실행 후 종료 ----------
  if (argc > 2){
    for(int i=2;i<argc;i++){
      try{
        int id = std::stoi(argv[i]);
        int ret = run_motion_id(cli, id);
        std::cout << "[RUN argv] id=" << id << " ret=" << ret << "\n";
        msleep(400);
        process_special_triggers(cli);
      }catch(...){
        std::cout << "[WARN] not an int: " << argv[i] << "\n";
      }
    }
    return 0;
  }

  // ---------- (2) 대화식: 기존 메뉴 ----------
  std::string line;
  while(!stop_flag){
    print_menu();
    std::cout << "> 번호 입력(공백 구분 가능) 또는 /go: ";
    if(!std::getline(std::cin, line)) break;
    if(line=="q" || line=="Q") break;
    if(line=="/go"){ special_trigger = true; }

    // 특수 신호 처리(대기중 자동동작 실행)
    process_special_triggers(cli);
    if (stop_flag) break;

    // 공백 구분으로 여러 번호 실행 가능
    std::istringstream iss(line);
    int id;
    while(iss >> id && !stop_flag){
      int ret = run_motion_id(cli, id);
      if (ret==0) std::cout << "[OK] #" << id << " 성공\n";
      else        std::cout << "[FAIL] #" << id << " ret=" << ret << "\n";
      msleep(500);

      // 각 명령 사이에도 특수 신호를 즉시 반영
      process_special_triggers(cli);
    }
  }

  std::cout << "\n[Done] 종료합니다.\n";
  return 0;
}
