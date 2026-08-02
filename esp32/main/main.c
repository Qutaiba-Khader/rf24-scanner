/*
 * rfnames - the companion scanner that puts NAMES on the spectrum.
 *
 * ============================  RECEIVE ONLY  ============================
 * Wi-Fi runs in station mode and never associates; BLE scanning is passive.
 * Nothing is ever transmitted, probed, injected or deauthenticated.
 * =======================================================================
 *
 * WHY THIS EXISTS
 *   The nRF24 scanner measures ENERGY and can never say what something is: a
 *   1-bit detector at a fixed -64 dBm reports "busy", never "who". This board
 *   is blind to energy but reads identity - SSID, BSSID, device name, MAC -
 *   and, unlike the nRF24, reports a real RSSI in dBm.
 *
 *   Together they answer a question neither can alone. Every Wi-Fi channel
 *   maps onto an exact nRF channel span (Wi-Fi ch k centres at 2412+5(k-1)
 *   MHz, +/-10), so energy the nRF24 sees that NO named AP and NO named BLE
 *   device accounts for is, by elimination, a proprietary 2.4 GHz emitter -
 *   an LED controller, a dongle, an RF remote. That is exactly the class this
 *   project hunts.
 *
 *   The one thing to keep straight: a Wi-Fi scan sees BEACONS, not traffic. A
 *   hammering AP and an idle one beacon identically. So this board says WHO is
 *   there; the nRF24 still says HOW MUCH of the air they take.
 *
 * SERIAL PROTOCOL  (USB CDC via CP210x, 115200 8N1, line oriented)
 *
 *   ESP32 -> host
 *     #rfnames <version>            banner, on boot and on '?'
 *     #info wifi=<n> ble=<n> state=<run|halt> period=<ms>
 *     #err <message>
 *     #hb <ms>                      heartbeat once a second while halted
 *     #scan <wifi|ble> start
 *     #scan <wifi|ble> done <count> <ms>
 *     W <rssi> <ch> <bssid> <auth> <ssid>
 *         one access point. rssi is a signed integer in dBm. ch is the Wi-Fi
 *         channel (1-14). bssid is 12 lowercase hex digits, no separators.
 *         auth is 0=open 1=wep 2=wpa 3=wpa2 4=wpa/wpa2 5=ent 6=wpa3 7=wpa2/3.
 *         ssid is the REST OF THE LINE and may contain spaces; it is empty
 *         for a hidden network.
 *     P <rssi> <ch> <mac> <frames> <bytes> <mgmt> <data>
 *         PROMISCUOUS: one TRANSMITTER seen on the air, from its 802.11
 *         header. Unlike a scan this counts real FRAMES and BYTES, so it
 *         measures how much air a device actually uses - which a beacon scan
 *         fundamentally cannot. mac is the transmitter address (addr2), and
 *         its first three bytes are the manufacturer OUI.
 *
 *     B <rssi> <addrtype> <mac> <name>
 *         one BLE advertiser. addrtype 0=public 1=random. name is the rest of
 *         the line and is empty when the advert carries none.
 *
 *   host -> ESP32  (one command per line, LF or CRLF)
 *     ?          banner + info
 *     G          go - scan continuously
 *     H          halt
 *     W          run one Wi-Fi scan now
 *     B          run one BLE scan now
 *     P<ms>      seconds between full cycles, 1000..60000
 *     M<n>       0 = scan (beacons + BLE adverts), 1 = PROMISCUOUS
 *                Promiscuous hops channels 1..13 capturing every 802.11 frame
 *                and tallies frames/bytes per transmitter. It is the only mode
 *                that measures AIRTIME rather than mere presence.
 *                Receive-only: monitor mode is passive by definition.
 *
 * WHAT THE LED MEANS (GPIO2, blue, on board #3)
 *     solid on during a scan, off between - so a board that has stopped
 *     scanning looks different from one that is merely idle.
 *
 * BUILD
 *     . $HOME/esp/esp-idf/export.ps1
 *     idf.py set-target esp32
 *     idf.py build
 *     idf.py -p COM12 flash monitor
 */

#include <stdio.h>
#include <string.h>
#include <ctype.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "driver/uart.h"

#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_gap_ble_api.h"

#define VERSION        "1.1.0"
#define LED_GPIO       2
#define MAX_AP         32
#define BLE_SCAN_SECS  4
#define PERIOD_MIN_MS  1000
#define PERIOD_MAX_MS  60000

static int  s_period_ms = 6000;
static bool s_running   = true;
static int  s_wifi_n    = 0;
static int  s_ble_n     = 0;
static bool s_promisc   = false;

/* Forward declarations. The promiscuous pass has to drain host commands so a
 * five-second channel walk stays responsive, and the command handler has to be
 * able to start and stop it - so the two refer to each other and one of them
 * must be declared first. */
static void drain_commands(void);
static void prom_start(void);
static void prom_stop(void);

static void led(int on)
{
    gpio_set_level(LED_GPIO, on ? 1 : 0);
}

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

/* ------------------------------------------------------------------ output
 * Everything the host sees goes through here so the line discipline is in one
 * place. ESP-IDF's own logging is turned down to ERROR in sdkconfig, otherwise
 * driver chatter interleaves with data lines and the host parser has to guess.
 */
static void say(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    fflush(stdout);
}

static void banner(void) { say("#rfnames %s\n", VERSION); }

static void info(void)
{
    say("#info wifi=%d ble=%d state=%s period=%d\n",
        s_wifi_n, s_ble_n, s_running ? "run" : "halt", s_period_ms);
}

/* ---------------------------------------------------------------- wifi scan
 * Station mode, never associates. esp_wifi_scan_start with block=true walks
 * every channel and returns beacons; show_hidden is on so an AP that suppresses
 * its SSID still appears - it is exactly as capable of stealing airtime as one
 * that announces itself, and leaving it out would hide a suspect.
 */
static void wifi_scan(void)
{
    say("#scan wifi start\n");
    led(1);
    int64_t t0 = now_ms();

    wifi_scan_config_t cfg = {
        .ssid = NULL, .bssid = NULL, .channel = 0,
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time.active = { .min = 100, .max = 300 },
    };
    /* ACTIVE would transmit probe requests. This tool is receive-only, so the
     * scan is forced passive: listen for beacons, never ask. Costs a little
     * time per channel; costs nothing in integrity. */
    cfg.scan_type = WIFI_SCAN_TYPE_PASSIVE;
    cfg.scan_time.passive = 180;

    esp_err_t e = esp_wifi_scan_start(&cfg, true);
    if (e != ESP_OK) {
        led(0);
        say("#err wifi scan: %s\n", esp_err_to_name(e));
        return;
    }

    uint16_t n = MAX_AP;
    static wifi_ap_record_t recs[MAX_AP];
    e = esp_wifi_scan_get_ap_records(&n, recs);
    if (e != ESP_OK) {
        led(0);
        say("#err wifi records: %s\n", esp_err_to_name(e));
        return;
    }

    for (int i = 0; i < n; i++) {
        const uint8_t *b = recs[i].bssid;
        /* SSID last, because it is the only field that can contain spaces.
         * Control characters are stripped: a crafted SSID must never be able
         * to inject a newline and forge a protocol line on the host. */
        char ssid[33];
        int k = 0;
        for (int j = 0; j < 32 && recs[i].ssid[j]; j++) {
            unsigned char c = recs[i].ssid[j];
            ssid[k++] = (c < 0x20 || c == 0x7f) ? '?' : (char)c;
        }
        ssid[k] = 0;

        say("W %d %d %02x%02x%02x%02x%02x%02x %d %s\n",
            (int)recs[i].rssi, (int)recs[i].primary,
            b[0], b[1], b[2], b[3], b[4], b[5],
            (int)recs[i].authmode, ssid);
    }
    s_wifi_n = n;
    led(0);
    say("#scan wifi done %d %lld\n", (int)n, now_ms() - t0);
}

/* ----------------------------------------------------------------- ble scan
 * Passive scan: listen for advertisements, never send a scan request. Results
 * arrive through the GAP callback, so the count is tallied there and reported
 * when the controller says the scan window has closed.
 */
static int64_t s_ble_t0;

static void ble_gap_cb(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *p)
{
    switch (event) {
    case ESP_GAP_BLE_SCAN_PARAM_SET_COMPLETE_EVT:
        break;

    case ESP_GAP_BLE_SCAN_RESULT_EVT: {
        struct ble_scan_result_evt_param *r = &p->scan_rst;
        if (r->search_evt == ESP_GAP_SEARCH_INQ_RES_EVT) {
            uint8_t len = 0;
            uint8_t *nm = esp_ble_resolve_adv_data(r->ble_adv,
                              ESP_BLE_AD_TYPE_NAME_CMPL, &len);
            if (!nm) nm = esp_ble_resolve_adv_data(r->ble_adv,
                              ESP_BLE_AD_TYPE_NAME_SHORT, &len);
            char name[32];
            int k = 0;
            for (int j = 0; nm && j < len && k < 31; j++) {
                unsigned char c = nm[j];
                name[k++] = (c < 0x20 || c == 0x7f) ? '?' : (char)c;
            }
            name[k] = 0;

            const uint8_t *a = r->bda;
            say("B %d %d %02x%02x%02x%02x%02x%02x %s\n",
                (int)r->rssi, (int)r->ble_addr_type,
                a[0], a[1], a[2], a[3], a[4], a[5], name);
            s_ble_n++;
        } else if (r->search_evt == ESP_GAP_SEARCH_INQ_CMPL_EVT) {
            led(0);
            say("#scan ble done %d %lld\n", s_ble_n, now_ms() - s_ble_t0);
        }
        break;
    }

    case ESP_GAP_BLE_SCAN_START_COMPLETE_EVT:
        if (p->scan_start_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            led(0);
            say("#err ble scan start %d\n", (int)p->scan_start_cmpl.status);
        }
        break;

    default:
        break;
    }
}

static void ble_scan(void)
{
    say("#scan ble start\n");
    s_ble_n = 0;
    s_ble_t0 = now_ms();
    led(1);
    esp_err_t e = esp_ble_gap_start_scanning(BLE_SCAN_SECS);
    if (e != ESP_OK) {
        led(0);
        say("#err ble scan: %s\n", esp_err_to_name(e));
    }
}

/* ----------------------------------------------------------------- commands
 * Read stdin without blocking. The UART driver is installed so a read can time
 * out; without it, a blocking getchar() would stall the scan loop forever the
 * moment nothing is typed - the same trap that cost the Pico firmware nine
 * releases, in a different language.
 */
static void drain_commands(void)
{
    uint8_t ch;
    static char buf[24];
    static int len = 0;

    while (uart_read_bytes(UART_NUM_0, &ch, 1, 0) == 1) {
        if (ch == '\r' || ch == '\n') {
            if (!len) continue;
            buf[len] = 0;
            char k = toupper((unsigned char)buf[0]);
            if (k == '?')      { banner(); info(); }
            else if (k == 'G') { s_running = true;  info(); }
            else if (k == 'H') { s_running = false; info(); }
            else if (k == 'W') { wifi_scan(); }
            else if (k == 'B') { ble_scan(); }
            else if (k == 'M') {
                int v = atoi(buf + 1);
                if (v == 1 && !s_promisc)      { s_promisc = true;  prom_start(); }
                else if (v == 0 && s_promisc)  { s_promisc = false; prom_stop(); }
                info();
            }
            else if (k == 'P') {
                int v = atoi(buf + 1);
                if (v >= PERIOD_MIN_MS && v <= PERIOD_MAX_MS) { s_period_ms = v; info(); }
                else say("#err period out of range\n");
            }
            else say("#err unknown command %c\n", k);
            len = 0;
        } else if (len < (int)sizeof(buf) - 1) {
            buf[len++] = (char)ch;
        }
    }
}

/* ------------------------------------------------------------ promiscuous
 * The scan above sees BEACONS. A beacon says an access point exists; it says
 * nothing about how much air anyone is using, because a saturating AP and an
 * idle one both beacon about ten times a second.
 *
 * Monitor mode fixes exactly that. Every 802.11 frame on the tuned channel is
 * handed to a callback, so real DATA frames get counted - and the transmitter
 * address (addr2) identifies the device, not just the network. Its first three
 * bytes are the manufacturer OUI, which is often enough to recognise a device
 * that never announces a name.
 *
 * Still receive-only: monitor mode is passive by definition. Nothing is
 * transmitted, and no frame is ever injected or replayed.
 */
#define PROM_MAX     48
#define PROM_DWELL   400        /* ms per channel; 13 channels = ~5 s a pass */
#define PROM_CH_LO   1
#define PROM_CH_HI   13

typedef struct {
    uint8_t  mac[6];
    int8_t   rssi;              /* strongest seen */
    uint8_t  ch;
    uint32_t frames;
    uint32_t bytes;
    uint16_t mgmt;
    uint16_t data;
} prom_t;

static prom_t s_prom[PROM_MAX];
static int    s_prom_n;
static portMUX_TYPE s_prom_mux = portMUX_INITIALIZER_UNLOCKED;

static void prom_reset(void)
{
    portENTER_CRITICAL(&s_prom_mux);
    s_prom_n = 0;
    memset(s_prom, 0, sizeof(s_prom));
    portEXIT_CRITICAL(&s_prom_mux);
}

/* Runs in the Wi-Fi driver's context, once per frame. It must stay short and
 * must not print: at a few thousand frames a second, a printf here would starve
 * the driver and drop most of what we are trying to count. Tally only; report
 * from the main loop. */
static void IRAM_ATTR prom_cb(void *buf, wifi_promiscuous_pkt_type_t type)
{
    if (type != WIFI_PKT_MGMT && type != WIFI_PKT_DATA) return;

    const wifi_promiscuous_pkt_t *p = (wifi_promiscuous_pkt_t *)buf;
    if (p->rx_ctrl.sig_len < 16) return;          /* too short to hold addr2 */

    /* 802.11 header: bytes 4-9 are addr1, 10-15 addr2. addr2 is the
     * TRANSMITTER - the device actually using the air, which is the whole
     * point of doing this rather than reading beacons. */
    const uint8_t *a2 = p->payload + 10;
    if (a2[0] & 0x01) return;                     /* group/broadcast - not a real device */

    portENTER_CRITICAL_ISR(&s_prom_mux);
    int slot = -1;
    for (int i = 0; i < s_prom_n; i++) {
        if (memcmp(s_prom[i].mac, a2, 6) == 0) { slot = i; break; }
    }
    if (slot < 0) {
        if (s_prom_n >= PROM_MAX) { portEXIT_CRITICAL_ISR(&s_prom_mux); return; }
        slot = s_prom_n++;
        memcpy(s_prom[slot].mac, a2, 6);
        s_prom[slot].rssi = -127;
    }
    prom_t *e = &s_prom[slot];
    if (p->rx_ctrl.rssi > e->rssi) e->rssi = p->rx_ctrl.rssi;
    e->ch = p->rx_ctrl.channel;
    e->frames++;
    e->bytes += p->rx_ctrl.sig_len;
    if (type == WIFI_PKT_MGMT) e->mgmt++; else e->data++;
    portEXIT_CRITICAL_ISR(&s_prom_mux);
}

static void prom_start(void)
{
    /* Scanning and monitor mode both own the radio. Stopping the scan side
     * first avoids the two fighting over channel selection. */
    esp_wifi_scan_stop();
    wifi_promiscuous_filter_t f = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA,
    };
    esp_wifi_set_promiscuous_filter(&f);
    esp_wifi_set_promiscuous_rx_cb(prom_cb);
    esp_err_t e = esp_wifi_set_promiscuous(true);
    if (e != ESP_OK) { say("#err promiscuous: %s\n", esp_err_to_name(e)); return; }
    prom_reset();
    say("#info promiscuous on\n");
}

static void prom_stop(void)
{
    esp_wifi_set_promiscuous(false);
    esp_wifi_set_promiscuous_rx_cb(NULL);
    led(0);
    say("#info promiscuous off\n");
}

/* One full pass: dwell on each channel in turn, then report the tally. */
static void prom_pass(void)
{
    say("#scan promisc start\n");
    int64_t t0 = now_ms();
    prom_reset();

    for (int ch = PROM_CH_LO; ch <= PROM_CH_HI; ch++) {
        esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
        led(1);
        vTaskDelay(pdMS_TO_TICKS(PROM_DWELL));
        led(0);
        /* Commands stay responsive during a 5-second pass. */
        drain_commands();
        if (!s_promisc) return;
    }

    static prom_t snap[PROM_MAX];
    int n;
    portENTER_CRITICAL(&s_prom_mux);
    n = s_prom_n;
    memcpy(snap, s_prom, sizeof(prom_t) * (n > 0 ? n : 0));
    portEXIT_CRITICAL(&s_prom_mux);

    uint32_t total = 0;
    for (int i = 0; i < n; i++) total += snap[i].bytes;

    for (int i = 0; i < n; i++) {
        const uint8_t *m = snap[i].mac;
        say("P %d %d %02x%02x%02x%02x%02x%02x %lu %lu %u %u\n",
            (int)snap[i].rssi, (int)snap[i].ch,
            m[0], m[1], m[2], m[3], m[4], m[5],
            (unsigned long)snap[i].frames, (unsigned long)snap[i].bytes,
            (unsigned)snap[i].mgmt, (unsigned)snap[i].data);
    }
    say("#scan promisc done %d %lld %lu\n", n, now_ms() - t0, (unsigned long)total);
}

void app_main(void)
{
    esp_log_level_set("*", ESP_LOG_ERROR);

    gpio_config_t io = {
        .pin_bit_mask = 1ULL << LED_GPIO,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&io);
    led(0);

    uart_driver_install(UART_NUM_0, 256, 0, 0, NULL, 0);

    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t wcfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wcfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());

    /* BLE only. Classic is released so its controller memory goes back to the
     * heap - Wi-Fi and Bluedroid together are tight on a 4 MB / 320 KB part. */
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));
    esp_bt_controller_config_t bcfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&bcfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_BLE));
    ESP_ERROR_CHECK(esp_bluedroid_init());
    ESP_ERROR_CHECK(esp_bluedroid_enable());
    ESP_ERROR_CHECK(esp_ble_gap_register_callback(ble_gap_cb));

    static esp_ble_scan_params_t sp = {
        .scan_type          = BLE_SCAN_TYPE_PASSIVE,
        .own_addr_type      = BLE_ADDR_TYPE_PUBLIC,
        .scan_filter_policy = BLE_SCAN_FILTER_ALLOW_ALL,
        .scan_interval      = 0x50,
        .scan_window        = 0x30,
        .scan_duplicate     = BLE_SCAN_DUPLICATE_DISABLE,
    };
    ESP_ERROR_CHECK(esp_ble_gap_set_scan_params(&sp));

    banner();
    info();

    int64_t next = 0, hb = 0;
    while (1) {
        drain_commands();
        int64_t t = now_ms();

        if (s_running && s_promisc) {
            prom_pass();
            next = now_ms() + 200;
            continue;
        }
        if (s_running && t >= next) {
            wifi_scan();
            vTaskDelay(pdMS_TO_TICKS(150));
            ble_scan();
            /* Let the BLE window close before the next cycle is due, so the
             * two scans never overlap and fight for the radio. */
            next = now_ms() + s_period_ms + BLE_SCAN_SECS * 1000;
        }
        if (!s_running && t - hb >= 1000) {
            hb = t;
            say("#hb %lld\n", t);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
