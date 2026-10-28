#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include <unistd.h>
#include <pthread.h>
#include "soem/ethercat.h"

static char IOmap[4096];
static int running = 1;
static int last_wkc = 0;

void* pdo_thread(void* arg) {
    while (running) {
        ec_send_processdata();
        last_wkc = ec_receive_processdata(EC_TIMEOUTRET);
        usleep(1000);
    }
    return NULL;
}

int main(int argc, char* argv[]) {
    const char* ifname = "eth0";
    if (argc > 1) ifname = argv[1];

    printf("[+] 初始化網路介面: %s\n", ifname);
    if (ec_init(ifname) <= 0) {
        printf("[-] 無法初始化網路介面 %s\n", ifname);
        return -1;
    }

    if (ec_config_init(FALSE) <= 0) {
        printf("[-] 找不到 EtherCAT 從站！\n");
        ec_close();
        return -1;
    }

    printf("[+] 找到 %d 個從站，從站名稱: %s\n", ec_slavecount, ec_slave[1].name);
    uint16_t cfg_adr = ec_slave[1].configadr;
    printf("[+] 從站 Configured Address: 0x%04X\n", cfg_adr);

    // 配置 IO 映射
    ec_config_map(&IOmap);

    // 啟動 1ms PDO 發送執行緒 (SM-Synchron)
    pthread_t th;
    pthread_create(&th, NULL, pdo_thread, NULL);

    // 推進至 SAFEOP
    ec_slave[1].state = EC_STATE_SAFE_OP;
    ec_writestate(1);
    ec_statecheck(1, EC_STATE_SAFE_OP, 50000);
    usleep(500000);

    // 推進至 OP
    ec_slave[1].state = EC_STATE_OPERATIONAL;
    ec_writestate(1);
    ec_statecheck(1, EC_STATE_OPERATIONAL, 50000);
    printf("[+] 當前從站狀態: 0x%02X (0x08 代表 OP)\n", ec_slave[1].state);
    usleep(500000);

    printf("\n======================================================================\n");
    printf("[*] 正在透過原生 C FPRD 讀取 ESC 暫存器 (OP 狀態下, 1ms PDO 持續收發):\n");
    printf("======================================================================\n");

    uint16_t al_status = 0;
    uint16_t al_status_code = 0;
    uint16_t al_event_mask = 0;
    uint16_t al_event_req = 0;

    // 1. 讀取 AL 狀態
    ec_FPRD(cfg_adr, 0x0130, sizeof(al_status), &al_status, EC_TIMEOUTRET);
    ec_FPRD(cfg_adr, 0x0134, sizeof(al_status_code), &al_status_code, EC_TIMEOUTRET);
    printf("1. AL 狀態暫存器:\n");
    printf("   0x0130 AL Status        : 0x%04X\n", al_status);
    printf("   0x0134 AL Status Code   : 0x%04X\n", al_status_code);

    // 2. 讀取 PDI 中斷事件暫存器
    ec_FPRD(cfg_adr, 0x0204, sizeof(al_event_mask), &al_event_mask, EC_TIMEOUTRET);
    ec_FPRD(cfg_adr, 0x0220, sizeof(al_event_req), &al_event_req, EC_TIMEOUTRET);
    printf("\n2. PDI 中斷事件暫存器 (核心判斷依據):\n");
    printf("   0x0204 AL Event Mask    : 0x%04X\n", al_event_mask);
    printf("   0x0220 AL Event Request : 0x%04X\n", al_event_req);

    int sm2_in_req  = (al_event_req  & 0x0100) ? 1 : 0;
    int sm2_in_mask = (al_event_mask & 0x0100) ? 1 : 0;
    printf("   >> SM2 Event (Bit 8) in AL Event Request : %s (ESC 是否產生 SM2 接收事件)\n", sm2_in_req ? "TRUE (1)" : "FALSE (0)");
    printf("   >> SM2 Event (Bit 8) in AL Event Mask    : %s (PDI 中斷是否允許 SM2 通過)\n", sm2_in_mask ? "TRUE (1)" : "FALSE (0)");

    // 3. 讀取 SM2 物理暫存器
    uint16_t sm2_addr = 0, sm2_len = 0;
    uint8_t sm2_ctrl = 0, sm2_stat = 0, sm2_act = 0, sm2_pdi = 0;

    ec_FPRD(cfg_adr, 0x0810, sizeof(sm2_addr), &sm2_addr, EC_TIMEOUTRET);
    ec_FPRD(cfg_adr, 0x0812, sizeof(sm2_len), &sm2_len, EC_TIMEOUTRET);
    ec_FPRD(cfg_adr, 0x0814, sizeof(sm2_ctrl), &sm2_ctrl, EC_TIMEOUTRET);
    ec_FPRD(cfg_adr, 0x0815, sizeof(sm2_stat), &sm2_stat, EC_TIMEOUTRET);
    ec_FPRD(cfg_adr, 0x0816, sizeof(sm2_act), &sm2_act, EC_TIMEOUTRET);
    ec_FPRD(cfg_adr, 0x0817, sizeof(sm2_pdi), &sm2_pdi, EC_TIMEOUTRET);

    printf("\n3. SM2 (Outputs) 物理暫存器 (0x0810 ~ 0x0817):\n");
    printf("   0x0810 Physical Start Address : 0x%04X\n", sm2_addr);
    printf("   0x0812 Length                 : %u Bytes\n", sm2_len);
    printf("   0x0814 Control Register       : 0x%02X\n", sm2_ctrl);
    printf("   0x0815 Status Register        : 0x%02X\n", sm2_stat);
    printf("   0x0816 Activate Register      : 0x%02X\n", sm2_act);
    printf("   0x0817 PDI Control Register   : 0x%02X\n", sm2_pdi);
    printf("   >> 當前週期通訊 WKC = %d\n", last_wkc);

    running = 0;
    pthread_join(th, NULL);

    ec_slave[1].state = EC_STATE_INIT;
    ec_writestate(1);
    ec_close();
    printf("\n[*] 讀取完畢。\n");
    return 0;
}
