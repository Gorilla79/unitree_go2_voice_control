#include <iostream>
#include <string>
#include <sstream>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cmath>

// Unitree SDK headers (경로는 프로젝트 include에 이미 잡혀 있어야 함)
#include <unitree/robot/channel/channel_factory.hpp>
#include "unitree/robot/go2/sport/sport_client.hpp"

// 간단 JSON 파서(최소한) — 외부 의존 회피
static bool parse_json_kv(const std::string& s, const std::string& key, std::string& out) {
    // "key":"value" 또는 "key":value (숫자)
    auto pos = s.find("\""+key+"\"");
    if (pos == std::string::npos) return false;
    pos = s.find(':', pos);
    if (pos == std::string::npos) return false;
    // 공백 스킵
    while (pos < s.size() && (s[pos]==':' || isspace((unsigned char)s[pos]))) pos++;
    if (pos>=s.size()) return false;
    if (s[pos]=='\"') {
        size_t p2 = s.find('"', pos+1);
        if (p2==std::string::npos) return false;
        out = s.substr(pos+1, p2-(pos+1));
        return true;
    } else {
        // 숫자/토큰
        size_t p2 = pos;
        while (p2<s.size() && (isdigit((unsigned char)s[p2])||s[p2]=='-'||s[p2]=='+'||s[p2]=='.'||s[p2]=='e'||s[p2]=='E')) p2++;
        out = s.substr(pos, p2-pos);
        return true;
    }
}

static bool parse_json_num(const std::string& s, const std::string& key, double& val) {
    std::string tmp;
    if (!parse_json_kv(s, key, tmp)) return false;
    try {
        val = std::stod(tmp);
        return true;
    } catch (...) { return false; }
}

static void usage() {
    std::cerr <<
    "Usage:\n"
    "  sudo -n -E ./go2_action_server [iface]\n"
    "  # 이후 stdin에 JSON 한 줄씩:\n"
    "  # {\"action\":\"stand\"}\n"
    "  # {\"action\":\"move\",\"vx\":0.3,\"vy\":0.0,\"vyaw\":0.0}\n";
}

int main(int argc, char** argv) {
    std::string iface = "eth0";
    if (argc >= 2) iface = argv[1];

    try {
        // DDS / 통신 초기화
        unitree::robot::ChannelFactory::Instance()->Init(0, iface);

        // 최신 네임스페이스의 SportClient
        unitree::robot::go2::SportClient sport;

        std::ios::sync_with_stdio(false);
        std::cin.tie(nullptr);

        std::string line;
        while (std::getline(std::cin, line)) {
            if (line.empty()) continue;

            std::string act;
            if (!parse_json_kv(line, "action", act)) {
                std::cout << "{\"ok\":false,\"error\":\"no action\"}\n" << std::flush;
                continue;
            }

            if (act=="quit" || act=="exit") {
                std::cout << "{\"ok\":true,\"action\":\"quit\"}\n" << std::flush;
                break;
            }
            else if (act=="sit") {
                sport.Sit();
                std::cout << "{\"ok\":true,\"action\":\"sit\"}\n" << std::flush;
            }
            else if (act=="stand") {
                sport.RiseSit();
                std::cout << "{\"ok\":true,\"action\":\"stand\"}\n" << std::flush;
            }
            else if (act=="hello") {
                sport.Hello();
                std::cout << "{\"ok\":true,\"action\":\"hello\"}\n" << std::flush;
            }
            else if (act=="heart") {
                sport.Heart();
                std::cout << "{\"ok\":true,\"action\":\"heart\"}\n" << std::flush;
            }
            else if (act=="bow") {
                sport.Scrape();
                std::cout << "{\"ok\":true,\"action\":\"bow\"}\n" << std::flush;
            }
            else if (act=="stop") {
                sport.StopMove();
                std::cout << "{\"ok\":true,\"action\":\"stop\"}\n" << std::flush;
            }
            else if (act=="move") {
                double vx=0.0, vy=0.0, vyaw=0.0;
                parse_json_num(line, "vx", vx);
                parse_json_num(line, "vy", vy);
                parse_json_num(line, "vyaw", vyaw);

                // 안전 클램프(teleop와 유사)
                vx   = std::max(-1.0, std::min(1.0, vx));
                vyaw = std::max(-2.0, std::min(2.0, vyaw));

                sport.Move(vx, 0.0 /*vy는 미사용*/, vyaw);
                std::cout << "{\"ok\":true,\"action\":\"move\",\"vx\":" << vx
                          << ",\"vy\":0.0,\"vyaw\":" << vyaw << "}\n" << std::flush;
            }
            else {
                std::cout << "{\"ok\":false,\"error\":\"unknown action\"}\n" << std::flush;
            }
        }
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "[ERR] exception: " << e.what() << std::endl;
        return 10;
    } catch (...) {
        std::cerr << "[ERR] unknown exception\n";
        return 11;
    }
}
