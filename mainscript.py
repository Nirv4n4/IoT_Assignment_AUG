# ESP32 HUZZAH32 + Nokia 5110 (PCD8544)
# ThingSpeak polling (fields 1 & 2) with HTTPS→HTTP fallback.
# Shows Goal / Actual on the LCD, compact formatting, left-margin text.

import time, network
from machine import Pin, SPI
import pcd8544                 # driver with FRAMEBUF class
import usocket as socket       # MicroPython socket
import ssl                     # MicroPython TLS

# ---------- Wi-Fi credentials ----------
WIFI_SSID = "Moussa's Pixel 9"
WIFI_PASS = "12345678"

# ---------- ThingSpeak (Read API, plaintext endpoints) ----------
CHANNEL_ID   = "3030971"
READ_API_KEY = "8Z6NGH0JTGQRRS58"
HOST         = "api.thingspeak.com"
PATH_GOAL    = "/channels/{}/fields/1/last.txt?api_key={}".format(CHANNEL_ID, READ_API_KEY)
PATH_ACTUAL  = "/channels/{}/fields/2/last.txt?api_key={}".format(CHANNEL_ID, READ_API_KEY)

POLL_SECS    = 20      # Respect free-plan limits (>= 15 s)
NET_TIMEOUT  = 12      # Socket timeout (s)
RETRIES      = 3       # Attempts for HTTPS, then HTTP

# ---------- LCD setup (PCD8544) ----------
# HUZZAH32 VSPI: SCK=GPIO5, MOSI=GPIO18 (no MISO needed)
spi = SPI(2, baudrate=200000, polarity=0, phase=0, sck=Pin(5), mosi=Pin(18))
dc  = Pin(17)
cs  = Pin(15)
rst = Pin(27)

# Use framebuffer variant so we can draw text easily
lcd = pcd8544.PCD8544_FRAMEBUF(spi, cs, dc, rst)
lcd.fill(0); lcd.show()

def lcd_message(lines):
    """
    Draw up to 5 text lines with a 1-pixel left margin to avoid the last-column glitch.
    """
    lcd.fill(0)
    y = 0
    for line in lines[:5]:
        lcd.text(line, 1, y, 1)  # x=1 left margin
        y += 10
    lcd.show()

# ---------- Wi-Fi ----------
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        lcd_message(["Wi-Fi...", WIFI_SSID])
        wlan.connect(WIFI_SSID, WIFI_PASS)
        t0 = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), t0) > 25000:
                lcd_message(["Wi-Fi FAIL"])
                raise RuntimeError("Wi-Fi timeout")
            time.sleep(0.3)
    ip = wlan.ifconfig()[0]
    print("Wi-Fi:", wlan.ifconfig())
    lcd_message(["Wi-Fi OK", ip])

# ---------- Minimal HTTP client ----------
def _readline(sock):
    """Read a CRLF-terminated line from sock."""
    line = b""
    while True:
        ch = sock.read(1)
        if not ch: break
        line += ch
        if line.endswith(b"\r\n"): break
    return line

def _read_headers(sock):
    """
    Returns (content_length, chunked) after reading status + headers.
    """
    _ = _readline(sock)  # status line
    content_length = None
    chunked = False
    while True:
        hdr = _readline(sock)
        if not hdr or hdr == b"\r\n":
            break
        h = hdr.decode().strip().lower()
        if h.startswith("content-length:"):
            try:
                content_length = int(h.split(":",1)[1].strip())
            except:
                pass
        if h.startswith("transfer-encoding:") and "chunked" in h:
            chunked = True
    return content_length, chunked

def _read_body(sock, content_length, chunked):
    """
    Read response body (handles both Content-Length and chunked).
    """
    body = b""
    if chunked:
        while True:
            szline = _readline(sock)
            if not szline:
                break
            try:
                sz = int(szline.strip(), 16)
            except:
                break
            if sz == 0:
                _ = _readline(sock)  # trailing CRLF
                break
            part = b""
            while len(part) < sz:
                r = sock.read(sz - len(part))
                if not r: break
                part += r
            body += part
            _ = _readline(sock)      # CRLF
    elif content_length is not None:
        while len(body) < content_length:
            r = sock.read(content_length - len(body))
            if not r: break
            body += r
    else:
        # Read until close
        while True:
            r = sock.read(512)
            if not r: break
            body += r
    return body

def _http_get_text(host, port, path, use_tls, timeout):
    """
    GET a URL and return the body as decoded text.
    Tries TLS if use_tls=True (MicroPython's ssl.wrap_socket without kwargs).
    """
    addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0][-1]
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(addr)
        if use_tls:
            s = ssl.wrap_socket(s)   # MicroPython: no server_hostname kwarg
        req = "GET {} HTTP/1.1\r\nHost: {}\r\nUser-Agent: uPy\r\nConnection: close\r\n\r\n".format(path, host)
        s.write(req.encode())
        cl, ch = _read_headers(s)
        body = _read_body(s, cl, ch)
    finally:
        try: s.close()
        except: pass
    return body.decode().strip()

def get_text_with_fallback(path):
    """
    Try HTTPS (443) RETRIES times, then HTTP (80) RETRIES times.
    """
    for attempt in range(1, RETRIES+1):
        try:
            return _http_get_text(HOST, 443, path, True, NET_TIMEOUT)
        except Exception as e:
            print("HTTPS try", attempt, "failed:", e)
            lcd_message(["HTTPS err", "try {}/{}".format(attempt, RETRIES)])
            time.sleep(1)
    for attempt in range(1, RETRIES+1):
        try:
            return _http_get_text(HOST, 80, path, False, NET_TIMEOUT)
        except Exception as e:
            print("HTTP try", attempt, "failed:", e)
            lcd_message(["HTTP err", "try {}/{}".format(attempt, RETRIES)])
            time.sleep(1)
    raise RuntimeError("All HTTP(S) attempts failed")

def fmt_compact(txt):
    """
    Format a numeric string to <= 2 decimals and strip trailing zeros/dot.
    If parsing fails, return original text.
    """
    try:
        f = float(txt)
        s = "{:.2f}".format(f).rstrip("0").rstrip(".")
        return s
    except:
        return txt

# ---------- Main ----------
wifi_connect()
while True:
    try:
        goal_txt   = get_text_with_fallback(PATH_GOAL)
        actual_txt = get_text_with_fallback(PATH_ACTUAL)
        goal_str   = fmt_compact(goal_txt)
        actual_str = fmt_compact(actual_txt)

        print("Goal:", goal_str, "Actual:", actual_str)
        lcd_message([
            "Goal:   " + goal_str + " cm",
            "Actual: " + actual_str + " cm"
        ])
    except Exception as e:
        print("Fetch error:", e)
        lcd_message(["Fetch error", str(e)[:14]])

    time.sleep(POLL_SECS)
