import flet as ft
from datetime import datetime
import os
import shutil
import sys
import ctypes
from ctypes import wintypes
import win32con
import win32gui
import win32api
import json
import configparser
from PIL import Image, ImageDraw, ImageFont
import time
import threading
from functools import partial

from file_utils import clear_folder
from process_cards import ImageProcessor
from image_utils import create_overlay_images

# ===============================
# OVERLAY WORKER
# ===============================
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 1)
    ]

class OverlayWorker(threading.Thread):
    HOTKEY_PREV_ID = 1
    HOTKEY_NEXT_ID = 2
    MOD_ALT = 0x0001
    VK_Z = 0x5A
    VK_X = 0x58

    def __init__(self, config, add_log, on_finish=None):
        super().__init__(daemon=True)
        self.config = config
        self.add_log = add_log
        self.on_finish = on_finish
        self.running = True
        self.hwnd_main = None
        self.cur_index = 0
        self.pil_cache = []
        self.image_paths = []

        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32

    def wnd_proc_factory(self):
        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_DESTROY:
                win32gui.PostQuitMessage(0)
                return 0
            elif msg == win32con.WM_HOTKEY:
                hot_id = wparam & 0xffff
                if hot_id == self.HOTKEY_NEXT_ID:
                    self.show_next()
                elif hot_id == self.HOTKEY_PREV_ID:
                    self.show_prev()
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
        return wnd_proc

    def create_layered_window(self, width, height):
        hInstance = win32api.GetModuleHandle(None)
        className = "OverlayWindow"

        wndClass = win32gui.WNDCLASS()
        wndClass.lpfnWndProc = self.wnd_proc_factory()
        wndClass.hInstance = hInstance
        wndClass.lpszClassName = className

        try:
            atom = win32gui.RegisterClass(wndClass)
        except win32gui.error as e:
            if e.winerror == 1410:
                atom = className
            else:
                raise

        exStyle = win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
        style = win32con.WS_POPUP

        hwnd = win32gui.CreateWindowEx(
            exStyle, atom, "Overlay", style,
            0, 0, width, height,
            0, 0, hInstance, None
        )

        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        return hwnd


    def create_dib_from_pil(self, pil_img):
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        if pil_img.size != (self.screen_width, self.screen_height):
            pil_img = pil_img.resize((self.screen_width, self.screen_height), Image.LANCZOS)

        raw = pil_img.tobytes("raw", "BGRA")
        width, height = pil_img.size
        bmi = BITMAPINFO()  
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER) 
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = win32con.BI_RGB
        bmi.bmiHeader.biSizeImage = 0

        hdc = self.user32.GetDC(0)
        memdc = self.gdi32.CreateCompatibleDC(hdc)
        ppvBits = ctypes.c_void_p()
        hbitmap = self.gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), win32con.DIB_RGB_COLORS, ctypes.byref(ppvBits), None, 0)
        ctypes.memmove(ppvBits, raw, len(raw))
        oldbmp = self.gdi32.SelectObject(memdc, hbitmap)
        return hbitmap, memdc, hdc, oldbmp

    def update_window_bitmap(self, hwnd, pil_image):
        hbitmap, memdc, hdc_screen, oldbmp = self.create_dib_from_pil(pil_image)
        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [
                ("BlendOp", ctypes.c_ubyte),
                ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)
            ]
        blend = BLENDFUNCTION()
        blend.BlendOp = 0
        blend.BlendFlags = 0
        blend.SourceConstantAlpha = 255
        blend.AlphaFormat = 1
        screen_dc = self.user32.GetDC(0)
        pt_src = wintypes.POINT(0, 0)
        size = wintypes.SIZE(self.screen_width, self.screen_height)
        pt_dest = wintypes.POINT(0, 0)
        self.user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(pt_dest), ctypes.byref(size),
                                        memdc, ctypes.byref(pt_src), 0, ctypes.byref(blend), 0x02)
        self.gdi32.SelectObject(memdc, oldbmp)
        self.gdi32.DeleteObject(hbitmap)
        self.gdi32.DeleteDC(memdc)
        self.user32.ReleaseDC(0, screen_dc)

    def show_index(self, i):
        if not self.pil_cache:
            return
        self.cur_index = i % len(self.pil_cache)
        self.update_window_bitmap(self.hwnd_main, self.pil_cache[self.cur_index])
        self.add_log(f"📸 Menampilkan gambar {self.cur_index + 1}/{len(self.pil_cache)}")

    def show_next(self):
        self.show_index(self.cur_index + 1)

    def show_prev(self):
        self.show_index(self.cur_index - 1)

    def register_hotkeys(self):
        if not self.user32.RegisterHotKey(self.hwnd_main, self.HOTKEY_NEXT_ID, self.MOD_ALT, self.VK_X):
            self.add_log("⚠️ Gagal register hotkey Alt+X")
        else:
            self.add_log("✅ Hotkey Alt+X aktif (Next)")
        if not self.user32.RegisterHotKey(self.hwnd_main, self.HOTKEY_PREV_ID, self.MOD_ALT, self.VK_Z):
            self.add_log("⚠️ Gagal register hotkey Alt+Z")
        else:
            self.add_log("✅ Hotkey Alt+Z aktif (Prev)")

    def stop(self):
        self.running = False
        try:
            self.user32.UnregisterHotKey(self.hwnd_main, self.HOTKEY_NEXT_ID)
            self.user32.UnregisterHotKey(self.hwnd_main, self.HOTKEY_PREV_ID)
            win32gui.PostQuitMessage(0)
        except Exception:
            pass
        self.add_log("🛑 Overlay dihentikan dan hotkey dilepas.")

    def cleanup(self):
        try:
            clear_folder(self.config['folder']['process'])
            clear_folder(self.config['folder']['output'])
        except Exception:
            pass
        self.add_log("🧹 Cleanup selesai.")
    
    def _load_offsets(self, json_file):
        import json
        with open(json_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def run(self):
        try:
            input_folder = self.config['folder']['input']
            process_folder = self.config['folder']['process']
            output_folder = self.config['folder']['output']
            screen_width = int(self.config['resolusi']['lebar'])
            screen_height = int(self.config['resolusi']['tinggi'])
            json_file = self.config['json']['offset']

            process = ImageProcessor(input_folder, process_folder, 52)
            self.add_log("Proses 1 : Cropping...")
            process.process_cropping(self._load_offsets(json_file))
            self.add_log("Proses 2 : Matching...")
            matched_pairs = process.process_matching(0.9)
            self.add_log("Proses 3 : Overlaying...")
            create_overlay_images(
                matched_pairs,
                self._load_offsets(json_file),
                output_folder,
                screen_width,
                screen_height,
                int(self.config['border']['x1']),
                int(self.config['border']['y1']),
                int(self.config['border']['x2']),
                int(self.config['border']['y2']),
                52
            )

            self.screen_width = int(self.config['resolusi']['lebar'])
            self.screen_height = int(self.config['resolusi']['tinggi'])
            output_folder = self.config['folder']['output']

            self.image_paths = sorted([os.path.join(output_folder, f) for f in os.listdir(output_folder) if f.lower().endswith(".png")])
            for p in self.image_paths:
                pil = Image.open(p).convert("RGBA")
                self.pil_cache.append(pil)

            if not self.pil_cache:
                self.add_log("❌ Tidak ada gambar overlay ditemukan")
                return

            self.hwnd_main = self.create_layered_window(self.screen_width, self.screen_height)
            self.register_hotkeys()
            self.show_index(0)

            while self.running:
                win32gui.PumpWaitingMessages()
                time.sleep(0.1)

        except Exception as e:
            self.add_log(f"❌ ERROR: {e}")
        finally:
            self.cleanup()
            if self.on_finish:
                self.on_finish()

is_running = False
current_task = None

def main(page: ft.Page):
    page.title = "Console Display"
    page.window.width = 800
    page.window.height = 350
    page.padding = 10
    page.theme_mode = "dark"

    timestamp_cb = ft.Checkbox(label="Show Timestamp", value=True)
    autoscroll_cb = ft.Checkbox(label="Autoscroll", value=True)
    status_text = ft.Text("Status: -", color=ft.Colors.CYAN, size=13)
    log_list = ft.ListView(expand=True, spacing=4, padding=ft.padding.symmetric(vertical=8, horizontal=8))
    logs = []
    MAX_LINES = 1000

    button_style = dict(color="white", height=35, expand=True)
    refresh_button = ft.ElevatedButton("🔄", bgcolor=ft.Colors.SECONDARY, **button_style)
    button_config = ft.ElevatedButton("Create&Check Config", bgcolor=ft.Colors.BLUE, **button_style)
    button_start = ft.ElevatedButton("Start Memory", bgcolor=ft.Colors.SECONDARY, **button_style)
    button_stop = ft.ElevatedButton("Stop Memory", bgcolor=ft.Colors.SECONDARY, **button_style)
    button_erase = ft.IconButton(icon=ft.Icons.DELETE_FOREVER_ROUNDED, tooltip="Clear Logs")

    overlay_thread = {"worker": None}

    def switch_button(value: int):
        if value == 2:
            button_start.disabled = True
            button_start.bgcolor = ft.Colors.SECONDARY
            button_stop.disabled = False
            button_stop.bgcolor = ft.Colors.RED
        elif value == 1:
            button_start.disabled = False
            button_start.bgcolor = ft.Colors.GREEN
            button_stop.disabled = True
            button_stop.bgcolor = ft.Colors.SECONDARY
        else:
            button_start.disabled = True
            button_start.bgcolor = ft.Colors.SECONDARY
            button_stop.disabled = True
            button_stop.bgcolor = ft.Colors.SECONDARY
        page.update()
        
    def load_image_paths(folder: str):
        paths = []
        i = 1
        while True:
            p = os.path.join(folder, f"Screenshot_{i}.png")
            if os.path.exists(p):
                paths.append(p)
                i += 1
            else:
                break
        if not paths:
            if not os.path.isdir(folder):
                return []
            files = sorted([f for f in os.listdir(folder) if f.lower().endswith(".png")])
            paths = [os.path.join(folder, f) for f in files]
        return paths

    def add_log(text: str, color=ft.Colors.WHITE):
        if timestamp_cb.value:
            text = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        logs.append(text)
        log_list.auto_scroll = autoscroll_cb.value
        log_list.controls.append(
            ft.Text(text, style=ft.TextStyle(font_family="Consolas", size=13), color=color)
        )
        if len(logs) > MAX_LINES:
            del logs[0: len(logs) - MAX_LINES]
            del log_list.controls[0: len(log_list.controls) - MAX_LINES]
        page.update()

    def check_file():
        file_config = "config.ini"
        file_json = "positions.json"
        found = True

        if not os.path.exists(file_config):
            add_log("File 'config.ini' tidak ditemukan. Silahkan tekan tombol 'Create Config'.", color=ft.Colors.RED)
            found = False
        if not os.path.exists(file_json):
            add_log("File 'positions.json' tidak ditemukan. Silahkan tekan tombol 'Create Config'.", color=ft.Colors.RED)
            found = False
        return found

    def check_config():
        config = configparser.ConfigParser()
        config.read('config.ini')

        struktur_wajib = {
            'resolusi': ['lebar', 'tinggi'],
            'json': ['offset'],
            'folder': ['input', 'process', 'output'],
            'border': ['x1', 'y1', 'x2', 'y2']
        }

        for section, keys in struktur_wajib.items():
            if section not in config:
                print(f"❌ Bagian [{section}] tidak ditemukan.")
                return False
            for key in keys:
                if key not in config[section] or not config[section][key].strip():
                    add_log(f"❌ Nilai '{key}' pada [{section}] kosong atau tidak ada.", color=ft.Colors.RED)
                    return False

        try:
            lebar = int(config['resolusi']['lebar'])
            tinggi = int(config['resolusi']['tinggi'])
            if lebar <= 0 or tinggi <= 0:
                add_log("❌ resolusi harus bernilai positif.", color=ft.Colors.RED)
                return False
        except ValueError:
            add_log("❌ resolusi.lebar dan resolusi.tinggi harus berupa angka.", color=ft.Colors.RED)
            return False

        offset_path = config['json']['offset']
        if not os.path.isfile(offset_path):
            add_log(f"❌ File JSON '{offset_path}' tidak ditemukan.", color=ft.Colors.RED)
            return False

        for key in ['input', 'process', 'output']:
            folder_path = config['folder'][key]
            if not os.path.isdir(folder_path):
                add_log(f"❌ Folder '{folder_path}' untuk [{key}] tidak ditemukan.", color=ft.Colors.RED)
                return False

        try:
            x1 = int(config['border']['x1'])
            y1 = int(config['border']['y1'])
            x2 = int(config['border']['x2'])
            y2 = int(config['border']['y2'])
        except ValueError:
            add_log("❌ Semua nilai di [border] harus berupa angka.", color=ft.Colors.RED)
            return False
    
        try:
            with open(offset_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            add_log(f"❌ File JSON tidak valid: {e}", color=ft.Colors.RED)
            return False
        except Exception as e:
            add_log(f"❌ Gagal membaca file: {e}", color=ft.Colors.RED)
            return False

        if not isinstance(data, dict):
            add_log("❌ Struktur JSON harus berupa objek (dictionary).", color=ft.Colors.RED)
            return False

        for key, value in data.items():
            try:
                int(key)
            except ValueError:
                add_log(f"❌ Key '{key}' bukan angka valid.", color=ft.Colors.RED)
                return False

            if not isinstance(value, dict):
                add_log(f"❌ Nilai untuk key '{key}' bukan objek (dict).", color=ft.Colors.RED)
                return False

            if 'x' not in value or 'y' not in value:
                add_log(f"❌ Key '{key}' tidak memiliki 'x' dan 'y'.", color=ft.Colors.RED)
                return False

            try:
                int(value['x'])
                int(value['y'])
            except ValueError:
                add_log(f"❌ Nilai x/y untuk key '{key}' harus berupa angka.", color=ft.Colors.RED)
                return False

        add_log("✅ File JSON valid dan lengkap.", color=ft.Colors.GREEN)

        return True

    def refresh_clicked(e=None):
        add_log("Button -> 'Refresh button' ditekan", color=ft.Colors.YELLOW)
        if check_file():
            if check_config():
                add_log("File 'config.ini' dan 'positions.json' ditemukan. Siap menjalankan program", color=ft.Colors.YELLOW)
                status_text.value = "Status: Configuration File Found"
                status_text.color = ft.Colors.GREEN
                refresh_button.bgcolor = ft.Colors.GREEN
                switch_button(1)
        else:
            status_text.value = "Status: Configuration File not Found"
            status_text.color = ft.Colors.RED
            refresh_button.bgcolor = ft.Colors.RED
            switch_button(0)
        page.update()

    def create_folder(path_folder: str):
        try:
            os.makedirs(path_folder, exist_ok=True)
            add_log(f"Folder '{path_folder}' siap digunakan.", color=ft.Colors.YELLOW)
        except Exception as e:
            add_log(f"Gagal membuat folder '{path_folder}': {e}", color=ft.Colors.RED)
        return path_folder

    def create_config(config_path='config.ini'):
        game_width = 800
        game_height = 600
        cols = 6
        rows = 5
        jarak_x = 64
        jarak_y = 60

        user32 = ctypes.windll.user32
        screen_width = user32.GetSystemMetrics(0)
        screen_height = user32.GetSystemMetrics(1)

        offset_x = (screen_width - game_width) // 2
        offset_y = (screen_height - game_height) // 2 + 5

        tikum_x = offset_x + 44
        tikum_y = offset_y + 183

        arr_x = [jarak_x * n + tikum_x for n in range(cols)]
        arr_y = [jarak_y * n + tikum_y for n in range(rows)]

        positions = {}
        no = 1
        for i in range(rows):
            for j in range(cols):
                positions[no] = {"x": arr_x[j], "y": arr_y[i]}
                no += 1

        with open("positions.json", "w") as f:
            json.dump(positions, f, indent=2)

        input_path = create_folder("input")
        process_path =  create_folder("process")
        output_path = create_folder("output")
        
        if not input_path.endswith("\\"):
            input_path += "\\"
        if not process_path.endswith("\\"):
            process_path += "\\"
        if not output_path.endswith("\\"):
            output_path += "\\"

        with open("config.ini", "w") as config:
            config.write("[resolusi]\n")
            config.write(f"lebar={screen_width}\n")
            config.write(f"tinggi={screen_height}\n\n")

            config.write("[json]\n")
            config.write("offset=positions.json\n\n")

            config.write("[folder]\n")
            config.write(f"input={input_path}\n")
            config.write(f"process={process_path}\n")
            config.write(f"output={output_path}\n\n")

            x1 = positions[1]["x"] - 14
            y1 = positions[1]["y"] - 8
            x2 = positions[30]["x"] + 66
            y2 = positions[30]["y"] + 60

            config.write("[border]\n")
            config.write(f"x1={x1}\n")
            config.write(f"y1={y1}\n")
            config.write(f"x2={x2}\n")
            config.write(f"y2={y2}\n")

        add_log("✅ File 'positions.json' dan 'config.ini' berhasil disimpan.", color=ft.Colors.YELLOW)
        add_log("Silahkan jalankan ulang program.", color=ft.Colors.YELLOW)

    def on_b1(e): 
        add_log("Button -> 'Create config' ditekan", color=ft.Colors.BLUE)
        if check_file():
            time.sleep(2)
            refresh_clicked()
        else:
            create_config()
            time.sleep(2)
            refresh_clicked()

    def on_b2(e): 
        if overlay_thread["worker"] and overlay_thread["worker"].is_alive():
            add_log("⚠️ Overlay sudah berjalan.")
            return
        config = load_config("config.ini")
        worker = OverlayWorker(config, add_log, on_finish=lambda: switch_button(1))
        overlay_thread["worker"] = worker
        worker.start()
        switch_button(2)
        
    def on_b3(e): 
        worker = overlay_thread["worker"]
        if worker and worker.is_alive():
            worker.stop()
            add_log("⛔ Program Dihentikan, dan Screenshot dihapus.")
            on_clear(None)
        switch_button(1)
    
    def load_config(config_path='config.ini'):
        config = configparser.ConfigParser()
        config.read(config_path)
        return config
    
    def on_clear(e):
        config = load_config('config.ini') 
        input_folder = config['folder']['input']
        process_folder = config['folder']['process']
        output_folder = config['folder']['output']

        folders = [input_folder, process_folder, output_folder]

        for folder in folders:
            if not os.path.exists(folder):
                continue

            for item in os.listdir(folder):
                item_path = os.path.join(folder, item)
                try:
                    if os.path.isfile(item_path) or os.path.islink(item_path):
                        os.remove(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as err:
                    print(f"Gagal menghapus {item_path}: {err}")
        
        switch_button(1)
        
        add_log("Seluruh isi folder berhasil dihapus tanpa menghapus folder utamanya.", color=ft.Colors.YELLOW)
 
    refresh_row = ft.Row(
        [
            refresh_button,
            status_text
        ],
        spacing=15,
        alignment=ft.MainAxisAlignment.START
    )

    controls_top = ft.Row([timestamp_cb, autoscroll_cb, refresh_row], spacing=30)

    refresh_button.on_click = refresh_clicked
    button_config.on_click = on_b1
    button_start.on_click = on_b2
    button_stop.on_click = on_b3

    button_erase.on_click = on_clear

    button_start.disabled = True
    button_stop.disabled = True

    buttons_row = ft.Row([button_config, button_start, button_stop, button_erase], alignment=ft.MainAxisAlignment.SPACE_EVENLY, spacing=15 )

    page.add(controls_top, log_list, buttons_row)
    page.update()
    
    refresh_clicked()

ft.app(target=main)
