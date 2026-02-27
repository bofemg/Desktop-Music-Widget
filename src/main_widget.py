import sys
import os
import time
import requests
import pythoncom
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QHBoxLayout, 
                             QVBoxLayout, QPushButton, QFrame, QGraphicsDropShadowEffect, QTextEdit, QScrollArea)
from PyQt6.QtCore import Qt, QTimer, QSize, QPoint, QPropertyAnimation, QEasingCurve, QThread, pyqtSignal, QRect, QRectF
from PyQt6.QtGui import QFont, QColor, QPixmap, QPainter, QPainterPath, QIcon, QGuiApplication, QTransform, QBrush, QPen, QFontMetrics
from pywinauto import Desktop
import win32api
import win32con
import json
import base64
from pywinauto import Application

# 解决你截图中的 DPI 报错
os.environ["QT_FONT_DPI"] = "96"

class CoverLoaderThread(QThread):
    cover_loaded = pyqtSignal(str, int) # 信号：返回图片路径, 时长(秒)

    def __init__(self, keyword, cache_dir):
        super().__init__()
        self.keyword = keyword
        self.cache_dir = cache_dir

    def run(self):
        try:
            # 1. 检查本地缓存
            # 简单的文件命名规则：keyword的hash或者直接替换非法字符
            safe_name = "".join([c for c in self.keyword if c.isalnum() or c in (' ', '-', '_')]).strip()
            if not safe_name:
                safe_name = "temp_cover"
            cache_path = os.path.join(self.cache_dir, f"{safe_name}.jpg")
            
            # Duration needs to be fetched anyway if not cached or separate
            # For simplicity, let's just try to fetch duration from API even if cover is cached?
            # Or assume 0 if cached. But we need duration for progress bar.
            # Let's fetch API info first.
            
            # 2. 网络搜索
            url = f"http://songsearch.kugou.com/song_search_v2?keyword={self.keyword}&page=1&pagesize=1&platform=WebFilter"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Referer": "https://www.kugou.com/"
            }
            
            resp = requests.get(url, headers=headers, timeout=5)
            data = resp.json()
            
            img_url = None
            duration = 0
            
            if data['status'] == 1 and data['data']['lists']:
                song_info = data['data']['lists'][0]
                file_hash = song_info['FileHash']
                album_id = song_info.get('AlbumID', '')
                duration = song_info.get('Duration', 0)
                
                # Check cache here, if exists and we have duration, we can return
                if os.path.exists(cache_path):
                    self.cover_loaded.emit(cache_path, duration)
                    return

                # 获取详情
                detail_url = f"https://wwwapi.kugou.com/yy/index.php?r=play/getdata&hash={file_hash}&mid=1&album_id={album_id}"
                cookies = {"kg_mid": "2333", "kg_dfid": "2333"}
                
                resp_detail = requests.get(detail_url, headers=headers, cookies=cookies, timeout=5)
                detail_data = resp_detail.json()
                
                if detail_data.get('status') == 1:
                    img_url = detail_data['data']['img']
                else:
                    # 备选方案：直接从搜索结果里找 Image 字段
                    if song_info.get('Image'):
                        img_url = song_info['Image']
            
            if img_url:
                # 3. 下载图片
                img_url = img_url.replace('{size}', '400') # 确保图片大小适配
                img_resp = requests.get(img_url, timeout=10)
                if img_resp.status_code == 200:
                    with open(cache_path, 'wb') as f:
                        f.write(img_resp.content)
                    self.cover_loaded.emit(cache_path, duration)
                    self.clean_cache()
            elif os.path.exists(cache_path):
                 # If download failed but cache exists (should be handled above, but just in case)
                 self.cover_loaded.emit(cache_path, duration)
            else:
                 # No cover found, but maybe duration found
                 self.cover_loaded.emit("", duration)
                    
        except Exception as e:
            print(f"Cover load failed: {e}")
            # Emit failures
            self.cover_loaded.emit("", 0)

    def clean_cache(self):
        try:
            # 1. 获取所有jpg文件
            files = []
            if os.path.exists(self.cache_dir):
                for entry in os.scandir(self.cache_dir):
                    if entry.is_file() and entry.name.endswith('.jpg'):
                        files.append(entry.path)
            
            # 2. 如果文件数量超过50个，按时间排序删除旧的
            if len(files) > 50:
                # 按修改时间排序，从小到大（旧到新）
                files.sort(key=os.path.getmtime)
                
                # 需要删除的文件数量
                to_delete = len(files) - 50
                for i in range(to_delete):
                    try:
                        os.remove(files[i])
                        print(f"Deleted old cache: {files[i]}")
                    except OSError as e:
                        print(f"Error deleting {files[i]}: {e}")
                        
        except Exception as e:
            print(f"Cache clean failed: {e}")

class LyricsLoaderThread(QThread):
    lyrics_loaded = pyqtSignal(list) # 返回歌词内容 [(time_ms, text), ...]
    
    def __init__(self, keyword):
        super().__init__()
        self.keyword = keyword
        
    def run(self):
        try:
            # 1. Search Song to get Hash
            url = f"http://songsearch.kugou.com/song_search_v2?keyword={self.keyword}&page=1&pagesize=1&platform=WebFilter"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Referer": "https://www.kugou.com/"
            }
            
            resp = requests.get(url, headers=headers, timeout=5)
            data = resp.json()
            
            file_hash = None
            if data['status'] == 1 and data['data']['lists']:
                song_info = data['data']['lists'][0]
                file_hash = song_info['FileHash']
            
            if not file_hash:
                self.lyrics_loaded.emit([])
                return

            # 2. Search Lyrics
            lrc_search_url = f"http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword={self.keyword}&duration=&hash={file_hash}&album_audio_id="
            resp_lrc_search = requests.get(lrc_search_url, headers=headers, timeout=5)
            lrc_search_data = resp_lrc_search.json()
            
            if lrc_search_data['status'] == 200 and lrc_search_data['candidates']:
                candidate = lrc_search_data['candidates'][0]
                lrc_id = candidate['id']
                accesskey = candidate['accesskey']
                
                # 3. Download Lyrics
                lrc_download_url = f"http://lyrics.kugou.com/download?ver=1&client=pc&id={lrc_id}&accesskey={accesskey}&fmt=lrc&charset=utf8"
                resp_lrc_download = requests.get(lrc_download_url, headers=headers, timeout=5)
                lrc_download_data = resp_lrc_download.json()
                
                if lrc_download_data['status'] == 200:
                    content = lrc_download_data['content']
                    decoded_content = base64.b64decode(content).decode('utf-8')
                    parsed_lrc = self.parse_lrc(decoded_content)
                    self.lyrics_loaded.emit(parsed_lrc)
                else:
                    self.lyrics_loaded.emit([])
            else:
                self.lyrics_loaded.emit([])
                
        except Exception as e:
            print(f"Lyrics load failed: {e}")
            self.lyrics_loaded.emit([])

    def parse_lrc(self, content):
        lines = []
        offset = 0 # 毫秒
        
        for line in content.splitlines():
            line = line.strip()
            if not line: continue
            
            # 解析 [offset:1000]
            if line.startswith('[offset:'):
                try:
                    offset_val = int(line[8:-1])
                    offset = offset_val
                except:
                    pass
                continue
            
            # Find all timestamps [mm:ss.xx]
            import re
            times = re.findall(r'\[(\d{2}):(\d{2})\.(\d{2,3})\]', line)
            if not times: continue
            
            text = re.sub(r'\[.*?\]', '', line).strip()
            
            for m, s, ms in times:
                total_ms = int(m) * 60000 + int(s) * 1000 + int(ms.ljust(3, '0')[:3])
                # 应用 offset (正数延迟，负数提前)
                # 通常 offset 是针对时间标签的偏移，如果 offset=1000，表示整体延迟 1s
                # 但具体正负含义有的播放器不同，先按标准处理
                # Winamp: offset value is in milliseconds. Positive shifts time down (later).
                # 所以我们要在显示时，把时间标签加上 offset? 或者减去?
                # 如果 offset=500，表示 00:01 实际上是 00:01.500
                # 所以我们储存的时候加上 offset
                lines.append((total_ms + offset, text))
        
        lines.sort(key=lambda x: x[0])
        return lines

import win32gui

class SongInfoMonitorThread(QThread):
    info_changed = pyqtSignal(str, str, str) # song_name, artist_name, full_key (None if not found)
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.last_key = None
        self.missed_count = 0
        
    def run(self):
        while self.running:
            try:
                # 使用 win32gui 直接枚举窗口，比 pywinauto 更轻量且无需 COM 初始化
                # 也不容易卡死
                
                # 定义回调
                def callback(hwnd, ctx):
                    try:
                        # 移除 IsWindowVisible 检查，以便在最小化/隐藏到托盘时也能获取标题
                        # 但为了避免获取到无关窗口，我们需要更严格的标题过滤
                        # if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        if "酷狗音乐" in title and "桌面歌词" not in title:
                            # 进一步过滤：通常主窗口的类名可能是特定的，或者标题不为空
                            # 这里简单通过标题长度过滤
                            if len(title) > 0: 
                                ctx['title'] = title
                                return False 
                    except:
                        pass
                    return True

                ctx = {}
                try:
                    win32gui.EnumWindows(callback, ctx)
                except Exception:
                    pass # 忽略停止遍历引发的异常
                
                if 'title' in ctx:
                    self.missed_count = 0
                    title = ctx['title']
                    clean_info = title.replace("- 酷狗音乐", "").strip()
                    
                    if " - " in clean_info:
                        parts = clean_info.split(" - ", 1)
                        artist_text = parts[0].strip()
                        song_text = parts[1].strip()
                    else:
                        # 如果只有 "酷狗音乐"，可能处于初始状态或某些特殊状态
                        # 如果之前有信息，尝试保留（也许是最小化时标题变了？）
                        # 但如果真的是停止播放了，标题通常也会变回默认
                        # 我们这里假设如果标题不含 "-"，就是没在播放具体歌曲
                        artist_text = "酷狗音乐"
                        song_text = clean_info
                    
                    # 检查变更
                    if clean_info != self.last_key:
                        self.last_key = clean_info
                        self.info_changed.emit(song_text, artist_text, clean_info)
                    
                else:
                    # 未找到窗口
                    self.missed_count += 1
                    # 连续 3 次（约 3 秒）没找到才认为是真的关闭了
                    if self.missed_count > 3:
                        if self.last_key is not None:
                            self.last_key = None
                            self.info_changed.emit("酷狗休息中...", "Waiting...", "")
                        
            except Exception as e:
                print(f"Song info monitor error: {e}")
                
            time.sleep(1.0)

    def stop(self):
        self.running = False
        self.wait()

class ProgressMonitorThread(QThread):
    progress_changed = pyqtSignal(int) # Current MS
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.slider_wrapper = None
        self.app = None
        self.pending_seek_ms = None
        self.consecutive_errors = 0
        
    def request_seek(self, ms):
        self.pending_seek_ms = ms

    def run(self):
        pythoncom.CoInitialize() # 初始化 COM
        print("ProgressMonitorThread started.")
        while self.running:
            try:
                if not self.slider_wrapper:
                    self.connect_kugou()
                
                if self.slider_wrapper:
                    # Check for seek request
                    if self.pending_seek_ms is not None:
                        try:
                            # Kugou slider unit is centiseconds (10ms)
                            target_val = self.pending_seek_ms / 10.0
                            
                            # Use cached wrapper
                            pattern = self.slider_wrapper.iface_range_value
                            pattern.SetValue(target_val)
                            print(f"Seeked to {target_val}")
                            self.consecutive_errors = 0
                        except Exception as e:
                            print(f"Seek failed: {e}")
                            # Force reconnect on error
                            self.slider_wrapper = None
                        finally:
                            self.pending_seek_ms = None
                            time.sleep(0.2)
                            continue

                    try:
                        # Use cached wrapper
                        pattern = self.slider_wrapper.iface_range_value
                        val = pattern.CurrentValue
                        current_ms = int(val * 10) # Convert cs to ms
                        self.progress_changed.emit(current_ms)
                        self.consecutive_errors = 0
                    except Exception as e:
                        self.consecutive_errors += 1
                        # print(f"Error reading slider (count={self.consecutive_errors}): {e}")
                        if self.consecutive_errors > 3:
                            # Reconnect if failed multiple times
                            self.slider_wrapper = None
                            self.consecutive_errors = 0
                else:
                    # Try to reconnect occasionally
                    if self.consecutive_errors > 5: # Faster retry
                        self.consecutive_errors = 0 
                    else:
                        self.consecutive_errors += 1
                
            except Exception as e:
                print(f"Monitor loop error: {e}")
                
            time.sleep(0.1) # 100ms
        pythoncom.CoUninitialize() # 释放 COM

    def connect_kugou(self):
        try:
            # 使用 win32gui 查找窗口句柄，比 UIA 遍历快且稳
            hwnd = 0
            def callback(h, ctx):
                if win32gui.IsWindowVisible(h):
                    title = win32gui.GetWindowText(h)
                    if "酷狗音乐" in title and "桌面歌词" not in title:
                        ctx['hwnd'] = h
                        return False
                return True
            
            ctx = {}
            try:
                win32gui.EnumWindows(callback, ctx)
            except:
                pass
                
            if 'hwnd' in ctx:
                hwnd = ctx['hwnd']
                try:
                    self.app = Application(backend="uia").connect(handle=hwnd)
                    dlg = self.app.window(handle=hwnd)
                    # 尝试查找进度条
                    self.kugou_slider = dlg.child_window(title="进度", control_type="Slider")
                    if self.kugou_slider.exists(timeout=1):
                        self.slider_wrapper = self.kugou_slider.wrapper_object()
                        # print("Connected to '进度' slider.")
                        self.consecutive_errors = 0
                    else:
                        # print("Slider '进度' not found (maybe minimized?).")
                        self.slider_wrapper = None
                except Exception as e:
                    # print(f"Failed to access slider: {e}")
                    self.slider_wrapper = None
            else:
                self.slider_wrapper = None
        except Exception as e:
            # print(f"Connect failed: {e}")
            self.slider_wrapper = None
            
    def stop(self):
        self.running = False
        self.wait()

class ScrollingLyricsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lyrics = [] # [(time, text), ...]
        self.current_time = 0
        self.current_index = -1
        self.font_normal = QFont("Microsoft YaHei", 10)
        self.font_highlight = QFont("Microsoft YaHei", 12, QFont.Weight.Bold)
        
        # Scrolling properties
        self.scroll_y = 0.0
        self.target_scroll_y = 0.0
        self.line_layouts = [] # [(y, height), ...]
        
        # Animation
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(16) # ~60 FPS
        self.anim_timer.timeout.connect(self.update_animation)
        
        # Easing properties (Spring-like effect)
        self.velocity = 0.0
        self.friction = 0.85 # 阻尼
        self.spring = 0.1 # 弹性系数
        
        # Active line effect
        self.active_scale = 1.0
        self.target_active_scale = 1.0
        
    def set_lyrics(self, lyrics):
        self.lyrics = lyrics
        self.current_time = 0
        self.current_index = -1
        self.scroll_y = 0
        self.target_scroll_y = 0
        self.recalculate_layout()
        self.update()
        
    def resizeEvent(self, event):
        self.recalculate_layout()
        self.update_target_scroll()
        super().resizeEvent(event)

    def recalculate_layout(self):
        self.line_layouts = []
        if not self.lyrics:
            return
            
        painter = QPainter(self) 
        fm_normal = QFontMetrics(self.font_normal)
        fm_highlight = QFontMetrics(self.font_highlight)
        
        width = self.width() - 40 # More padding
        y = 0
        
        for i, (t, text) in enumerate(self.lyrics):
            # Assume highlight font for layout calculation to avoid jitter
            # Or better, calculate max possible height?
            # Let's use highlight font metrics for spacing to be safe
            rect = fm_highlight.boundingRect(QRect(0, 0, width, 0), Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter, text)
            height = rect.height() + 20 # More spacing
            
            self.line_layouts.append((y, height))
            y += height

    def set_time(self, ms):
        self.current_time = ms
        new_index = -1
        for i, (t, text) in enumerate(self.lyrics):
            if ms >= t:
                new_index = i
            else:
                break
        
        if new_index != self.current_index:
            self.current_index = new_index
            self.update_target_scroll()
            self.active_scale = 0.8 # Reset scale for pop effect
            self.target_active_scale = 1.0
            
            if not self.anim_timer.isActive():
                self.anim_timer.start()

    def update_target_scroll(self):
        if self.current_index >= 0 and self.current_index < len(self.line_layouts):
            y, h = self.line_layouts[self.current_index]
            center_y = self.height() / 2
            self.target_scroll_y = y + (h / 2) - center_y
        else:
            self.target_scroll_y = 0

    def update_animation(self):
        # 1. Scroll Physics (Spring)
        displacement = self.target_scroll_y - self.scroll_y
        force = displacement * self.spring
        self.velocity += force
        self.velocity *= self.friction
        self.scroll_y += self.velocity
        
        # Stop condition for scroll
        is_scroll_done = abs(self.velocity) < 0.1 and abs(displacement) < 0.5
        if is_scroll_done:
            self.scroll_y = self.target_scroll_y
            self.velocity = 0
            
        # 2. Scale Animation (Linear interpolation)
        diff_scale = self.target_active_scale - self.active_scale
        if abs(diff_scale) > 0.01:
            self.active_scale += diff_scale * 0.2
        else:
            self.active_scale = self.target_active_scale
            
        self.update()
        
        if is_scroll_done and abs(diff_scale) < 0.01:
            self.anim_timer.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        
        if not self.lyrics:
            painter.setPen(QColor(200, 200, 200))
            painter.setFont(self.font_normal)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无歌词 / 纯音乐")
            return

        if not self.line_layouts:
            self.recalculate_layout()

        width = self.width() - 40
        start_y = -self.scroll_y
        center_x = self.width() / 2
        
        for i, (t, text) in enumerate(self.lyrics):
            if i >= len(self.line_layouts): break
            
            ly, lh = self.line_layouts[i]
            
            # Base Y position
            y = start_y + ly
            
            # Optimization
            if y + lh < -50 or y > self.height() + 50:
                continue
            
            # Draw
            painter.save()
            
            # Calculate dynamic opacity based on distance from center
            center_dist = abs((y + lh/2) - (self.height()/2))
            max_dist = self.height() / 2
            opacity = 1.0 - min(1.0, (center_dist / max_dist) * 0.8) # Min opacity 0.2
            
            if i == self.current_index:
                # Active Line
                scale = self.active_scale
                color = QColor("#FFD700")
                font = self.font_highlight
                opacity = 1.0 # Always full opacity for active
            else:
                # Inactive Line
                scale = 1.0
                color = QColor(255, 255, 255, int(255 * opacity * 0.6))
                font = self.font_normal
            
            # Apply transformations
            rect_center_y = y + lh/2
            painter.translate(center_x, rect_center_y)
            painter.scale(scale, scale)
            painter.translate(-center_x, -rect_center_y)
            
            painter.setFont(font)
            painter.setPen(color)
            
            # Draw text
            rect = QRect(20, int(y), width, int(lh))
            painter.drawText(rect, Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter, text)
            
            painter.restore()

class SvgButton(QPushButton):
    def __init__(self, path_data, size=24, parent=None):
        super().__init__(parent)
        self.path_data = path_data
        self.icon_size = size
        self.setFixedSize(size + 10, size + 10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hovered = False
        
        self.setStyleSheet("border: none; background: transparent;")

    def set_path(self, path_data):
        self.path_data = path_data
        self.update()

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Color: White by default, Green on hover
        color = QColor("#1db954") if self.hovered else QColor("white")
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        
        # Draw SVG Path
        svg_xml = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{color.name()}">
            <path d="{self.path_data}" />
        </svg>'''
        
        from PyQt6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(bytes(svg_xml, 'utf-8'))
        
        # Center the icon
        target_rect = QRectF((self.width() - self.icon_size)/2, (self.height() - self.icon_size)/2, self.icon_size, self.icon_size)
        renderer.render(painter, target_rect)

class ModernProgressBar(QWidget):
    seek_requested = pyqtSignal(float) # 0.0 - 1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(12) # Reduced height
        self.progress = 0.0 # 0.0 to 1.0
        self.hovered = False
        self.is_dragging = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
    def set_progress(self, value):
        if not self.is_dragging:
            self.progress = max(0.0, min(1.0, value))
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = True
            self.update_progress_from_event(event)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            self.update_progress_from_event(event)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_dragging and event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False
            self.update_progress_from_event(event)
            self.seek_requested.emit(self.progress)
            event.accept()

    def update_progress_from_event(self, event):
        width = self.width()
        if width > 0:
            x = event.pos().x()
            self.progress = max(0.0, min(1.0, x / width))
            self.update()

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)
        
    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Dimensions
        h = 4
        y = (self.height() - h) / 2
        w = self.width()
        
        # Background (Grey)
        painter.setBrush(QColor("#5e5e5e"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(0, y, w, h), 2, 2)
        
        # Foreground (Green)
        fill_w = w * self.progress
        painter.setBrush(QColor("#1db954"))
        painter.drawRoundedRect(QRectF(0, y, fill_w, h), 2, 2)
        
        # Handle (White Circle) - only show on hover or dragging
        if self.hovered or self.is_dragging:
            painter.setBrush(QColor("white"))
            handle_size = 10
            # Center handle on the end of the green bar
            handle_x = min(max(fill_w, handle_size/2), w - handle_size/2)
            painter.drawEllipse(QPoint(int(handle_x), int(self.height()/2)), handle_size//2, handle_size//2)

class RotatingAlbum(QWidget):
    def __init__(self, size=90, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.pixmap = None
        self.angle = 0
        
        # 旋转定时器
        self.rotate_timer = QTimer(self)
        self.rotate_timer.setInterval(30) # 约 33 FPS
        self.rotate_timer.timeout.connect(self.rotate)
        self.is_rotating = False

    def set_image(self, image_path):
        size = self.width()
        
        # 加载并处理图片为圆形
        target = QPixmap(size, size)
        target.fill(Qt.GlobalColor.transparent)
        
        p = QPixmap(image_path)
        if p.isNull():
            # 加载失败绘制灰色圆
            painter = QPainter(target)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(60, 60, 60))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(0, 0, size, size)
            painter.end()
        else:
            # 裁剪为圆形
            p = p.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            
            painter = QPainter(target)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            path = QPainterPath()
            path.addEllipse(0, 0, size, size)
            painter.setClipPath(path)
            
            painter.drawPixmap(0, 0, p)
            
            # 绘制中心黑胶孔效果
            # 外圈黑色半透明
            painter.setClipPath(QPainterPath()) # 清除裁剪
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(Qt.PenStyle.NoPen)
            
            # 中心黑洞
            hole_size = int(size * 0.15)
            center = size // 2
            painter.setBrush(QColor(20, 20, 20))
            painter.drawEllipse(center - hole_size//2, center - hole_size//2, hole_size, hole_size)
            
            painter.end()
            
        self.pixmap = target
        self.update()

    def start_rotation(self):
        if not self.is_rotating:
            self.rotate_timer.start()
            self.is_rotating = True

    def stop_rotation(self):
        if self.is_rotating:
            self.rotate_timer.stop()
            self.is_rotating = False

    def rotate(self):
        self.angle = (self.angle + 1) % 360
        self.update()

    def paintEvent(self, event):
        if not self.pixmap:
            return
            
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        # 移动坐标系到中心 -> 旋转 -> 移回
        center_x = self.width() / 2
        center_y = self.height() / 2
        
        painter.translate(center_x, center_y)
        painter.rotate(self.angle)
        painter.translate(-center_x, -center_y)
        
        painter.drawPixmap(0, 0, self.pixmap)

class MusicFloatWindow(QWidget):
    def __init__(self):
        super().__init__()
        # 窗口属性：无边框、置顶、工具窗口
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | 
                            Qt.WindowType.WindowStaysOnTopHint | 
                            Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.current_duration = 0 # seconds
        self.current_progress_ms = 0 # ms
        
        self.current_song_key = None # 用于去重，防止重复下载
        self.cover_loader = None
        self.lyrics_loader = None
        self.is_lyrics_visible = False
        self.progress_monitor = ProgressMonitorThread()
        self.progress_monitor.progress_changed.connect(self.update_lyrics_progress)
        self.progress_monitor.start()

        # Determine paths for packaged executable vs script
        if getattr(sys, 'frozen', False):
            # Running as compiled executable
            self.app_path = os.path.dirname(sys.executable)
            self.resource_path = sys._MEIPASS
        else:
            # Running as script
            self.app_path = os.path.dirname(os.path.abspath(__file__))
            self.resource_path = self.app_path

        self.cache_dir = os.path.join(self.app_path, "covers")
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
            
        # 默认封面路径 (Bundled resource)
        self.default_cover_path = os.path.join(self.resource_path, "default_cover.png")
        
        self.initUI()
        
        # 歌曲信息监控线程
        self.info_monitor = SongInfoMonitorThread()
        self.info_monitor.info_changed.connect(self.on_info_changed)
        self.info_monitor.start()
        
        self.old_pos = None

        # 边缘吸附相关属性
        self.dock_direction = None # 'left', 'right', 'top'
        self.is_docked = False
        self.screen_margin = 20 # 距离边缘多少像素触发吸附
        self.dock_protrusion = 20 # 吸附后露出的像素宽度
        
        # 自动收回定时器
        self.autohide_timer = QTimer(self)
        self.autohide_timer.setInterval(500) # 鼠标离开 0.5 秒后自动收回
        self.autohide_timer.setSingleShot(True)
        self.autohide_timer.timeout.connect(self.dock_window)

        # 动画对象
        self.animation = QPropertyAnimation(self, b"pos")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # 记录按下时的位置，用于判断是点击还是拖动
        self.press_pos = None

    def initUI(self):
        self.setFixedSize(320, 160) # Shorten width (was 400), Increase height (was 140)
        
        # 主容器 Frame
        self.main_frame = QFrame(self)
        self.main_frame.setGeometry(10, 10, 300, 140)  # 留出阴影空间
        # Spotify Dark Theme
        self.main_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(25, 20, 20, 150);
                border-radius: 10px;
                border: 1px solid rgba(255, 255, 255, 10);
            }
        """)
        
        # 添加阴影效果
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 4)
        self.main_frame.setGraphicsEffect(shadow)

        # 主布局
        main_layout = QVBoxLayout(self.main_frame)
        main_layout.setContentsMargins(15, 15, 15, 10)
        main_layout.setSpacing(5)

        # Top Section: Cover + Info + Controls
        top_layout = QHBoxLayout()
        top_layout.setSpacing(15)

        # 1. 左侧封面 (RotatingAlbum)
        self.album_cover = RotatingAlbum(80, self.main_frame)
        # 初始使用默认封面
        self.album_cover.set_image(self.default_cover_path)
        # 默认开始旋转
        self.album_cover.start_rotation()
        
        # 2. 右侧信息区
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        info_layout.setContentsMargins(0, 5, 0, 5)
        
        # 歌名
        self.song_label = QLabel("正在寻找酷狗...", self.main_frame)
        self.song_label.setStyleSheet("""
            color: white; 
            font-family: "Microsoft YaHei"; 
            font-size: 14px; 
            font-weight: bold; 
            background: transparent;
            border: none;
        """)
        
        # 歌手（副标题）
        self.artist_label = QLabel("Waiting...", self.main_frame)
        self.artist_label.setStyleSheet("""
            color: #b3b3b3; 
            font-family: "Microsoft YaHei"; 
            font-size: 11px; 
            font-weight: bold;
            background: transparent;
            border: none;
        """)

        # 控制按钮栏
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setSpacing(10)
        
        # Icons Paths
        # Prev: |<
        icon_prev = "M6 6h2v12H6zm3.5 6l8.5 6V6z" 
        # Play: >
        self.icon_play_path = "M8 5v14l11-7z"
        # Pause: ||
        self.icon_pause_path = "M6 19h4V5H6v14zm8-14v14h4V5h-4z"
        # Next: >|
        icon_next = "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"
        # Lyrics: Quote icon
        icon_lyrics = "M6 17h3l2-4V7H5v6h3zm8 0h3l2-4V7h-6v6h3z"

        self.btn_prev = SvgButton(icon_prev, 18, self.main_frame)
        self.btn_play = SvgButton(self.icon_play_path, 22, self.main_frame) # Play/Pause toggles icon
        self.btn_next = SvgButton(icon_next, 18, self.main_frame)
        self.btn_lyrics = SvgButton(icon_lyrics, 16, self.main_frame)
        
        self.is_playing = False # Track playing state
        
        # 绑定按钮事件
        self.btn_prev.clicked.connect(lambda: self.control_music("prev"))
        self.btn_play.clicked.connect(lambda: self.control_music("play_pause"))
        self.btn_next.clicked.connect(lambda: self.control_music("next"))
        self.btn_lyrics.clicked.connect(self.toggle_lyrics_view)

        ctrl_layout.addWidget(self.btn_prev)
        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addWidget(self.btn_next)
        ctrl_layout.addWidget(self.btn_lyrics)
        ctrl_layout.addStretch() # 靠左对齐

        # 组装右侧
        info_layout.addWidget(self.song_label)
        info_layout.addWidget(self.artist_label)
        info_layout.addStretch()
        info_layout.addLayout(ctrl_layout)
        
        top_layout.addWidget(self.album_cover)
        top_layout.addLayout(info_layout)
        
        # Bottom Section: Progress Bar + Time
        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(8)
        
        self.time_now_label = QLabel("0:00", self.main_frame)
        self.time_now_label.setStyleSheet("color: white; font-size: 10px; background: transparent;")
        
        self.progress_bar = ModernProgressBar(self.main_frame)
        self.progress_bar.seek_requested.connect(self.on_seek_requested)
        
        self.time_total_label = QLabel("0:00", self.main_frame)
        self.time_total_label.setStyleSheet("color: white; font-size: 10px; background: transparent;")
        
        progress_layout.addWidget(self.time_now_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.time_total_label)
        
        # Add to main layout
        main_layout.addLayout(top_layout)
        main_layout.addLayout(progress_layout)
        
        # 初始化歌词界面
        self.init_lyrics_ui()
        
        # 关闭按钮（右上角悬浮）
        self.close_btn = QPushButton("×", self.main_frame)
        self.close_btn.setGeometry(280, 5, 20, 20)
        self.close_btn.setStyleSheet("""
            QPushButton {
                color: rgba(255, 255, 255, 100);
                background: transparent;
                font-size: 18px;
                border: none;
                font-weight: bold;
            }
            QPushButton:hover {
                color: white;
            }
        """)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)

    def init_lyrics_ui(self):
        # 歌词面板
        self.lyrics_frame = QFrame(self)
        self.lyrics_frame.setGeometry(10, 150, 300, 260) # 在主面板下方 (Height 260 unchanged)
        self.lyrics_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(25, 20, 20, 150);
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                border-left: 1px solid rgba(255, 255, 255, 10);
                border-right: 1px solid rgba(255, 255, 255, 10);
                border-bottom: 1px solid rgba(255, 255, 255, 10);
            }
        """)
        
        # 歌词显示区域
        layout = QVBoxLayout(self.lyrics_frame)
        self.lyrics_widget = ScrollingLyricsWidget(self.lyrics_frame)
        layout.addWidget(self.lyrics_widget)
        self.lyrics_frame.hide() # 默认隐藏

    def toggle_lyrics_view(self):
        if self.is_lyrics_visible:
            # 收起
            self.lyrics_frame.hide()
            self.setFixedSize(320, 160)
            self.is_lyrics_visible = False
        else:
            # 展开
            self.setFixedSize(320, 420) # 160 + 260
            self.lyrics_frame.show()
            self.is_lyrics_visible = True
            
            # 如果没有歌词，加载歌词
            if not self.lyrics_widget.lyrics and self.current_song_key:
                self.load_lyrics(self.current_song_key)

    def load_lyrics(self, keyword):
        # self.lyrics_text.setText("正在搜索歌词...")
        # 重置状态
        self.lyrics_widget.set_lyrics([])
        
        if self.lyrics_loader and self.lyrics_loader.isRunning():
            self.lyrics_loader.terminate()
            self.lyrics_loader.wait()
            
        # 提取搜索关键词（去除 " - " 分隔符）
        if " - " in keyword:
            search_key = keyword.replace(" - ", " ")
        else:
            search_key = keyword
            
        self.lyrics_loader = LyricsLoaderThread(search_key)
        self.lyrics_loader.lyrics_loaded.connect(self.on_lyrics_loaded)
        self.lyrics_loader.start()
        
    def on_lyrics_loaded(self, lyrics_data):
        self.lyrics_widget.set_lyrics(lyrics_data)
        
    def update_lyrics_progress(self, ms):
        if self.current_duration > 0:
            progress = ms / (self.current_duration * 1000)
            self.progress_bar.set_progress(progress)
            
            # Update time label
            s_total = ms // 1000
            m = s_total // 60
            s = s_total % 60
            self.time_now_label.setText(f"{m}:{s:02d}")
        
        if self.is_lyrics_visible:
            # 补偿系统延迟 (UIA 读取耗时 + 网络歌词偏移)
            # 经验值：UIA 读取约 50-100ms，显示刷新约 16ms
            # 另外，如果歌词偏慢，需要加正数；偏快减负数
            latency_compensation = 200 
            self.lyrics_widget.set_time(ms + latency_compensation)

    def on_seek_requested(self, progress):
        # 1. Update internal state immediately
        if self.current_duration > 0:
            target_ms = int(progress * self.current_duration * 1000)
            self.current_progress_ms = target_ms
            
            # Update labels
            s_total = target_ms // 1000
            m = s_total // 60
            s = s_total % 60
            self.time_now_label.setText(f"{m}:{s:02d}")
            
            # 2. Control Kugou
            self.progress_monitor.request_seek(target_ms)

    def control_music(self, action):
        """模拟多媒体按键控制音乐"""
        if action == "prev":
            vk_code = win32con.VK_MEDIA_PREV_TRACK
        elif action == "next":
            vk_code = win32con.VK_MEDIA_NEXT_TRACK
        elif action == "play_pause":
            vk_code = win32con.VK_MEDIA_PLAY_PAUSE
            # Toggle Icon
            self.is_playing = not self.is_playing
            if self.is_playing:
                self.btn_play.set_path(self.icon_pause_path)
            else:
                self.btn_play.set_path(self.icon_play_path)
        else:
            return

        # 模拟按键按下和释放
        # keybd_event(virtual_key_code, scan_code, flags, extra_info)
        win32api.keybd_event(vk_code, 0, 0, 0)
        win32api.keybd_event(vk_code, 0, win32con.KEYEVENTF_KEYUP, 0)
        print(f"Sent command: {action}")

    def on_info_changed(self, song_text, artist_text, full_key):
        self.song_label.setText(song_text)
        self.artist_label.setText(artist_text)
        
        # 状态变更
        if not full_key:
            # 酷狗休息中
            self.album_cover.stop_rotation()
            if self.current_song_key is not None:
                 self.current_song_key = None
                 self.album_cover.set_image(self.default_cover_path)
            
            # Reset play button to Play state
            if self.is_playing:
                self.is_playing = False
                self.btn_play.set_path(self.icon_play_path)
            return

        # 找到歌曲，开始旋转
        self.album_cover.start_rotation()
        
        # 只要检测到歌曲信息（说明在播放或者暂停显示歌名），我们默认认为是播放状态
        # 实际上酷狗暂停时标题可能不会变，或者变成 "酷狗音乐"
        # 但如果能获取到 "歌手 - 歌名"，说明至少有歌曲加载
        # 为了体验更好，我们在切歌时重置为播放状态图标（暂停图标）
        
        # 检查是否切换了歌曲
        if full_key != self.current_song_key:
            self.current_song_key = full_key
            self.start_load_cover(full_key)
            self.lyrics_widget.set_lyrics([]) # 清除旧歌词
            if self.is_lyrics_visible:
                self.load_lyrics(full_key) # 如果面板打开，自动加载新歌词
            
            # 切歌时，通常是开始播放新歌，所以更新图标为暂停（表示正在播放）
            if not self.is_playing:
                self.is_playing = True
                self.btn_play.set_path(self.icon_pause_path)

    def update_music_info(self):
        # 此函数已废弃，逻辑移至 SongInfoMonitorThread 和 on_info_changed
        pass
            
    def start_load_cover(self, keyword):
        # 如果上一个还在跑，先关掉
        if self.cover_loader and self.cover_loader.isRunning():
            self.cover_loader.terminate()
            self.cover_loader.wait()
        
        self.cover_loader = CoverLoaderThread(keyword, self.cache_dir)
        self.cover_loader.cover_loaded.connect(self.on_cover_loaded)
        self.cover_loader.start()

    def on_cover_loaded(self, path, duration):
        # 确保UI线程更新
        self.album_cover.set_image(path)
        self.current_duration = duration
        # Update duration label
        if duration > 0:
            m = duration // 60
            s = duration % 60
            self.time_total_label.setText(f"{m}:{s:02d}")
        else:
            self.time_total_label.setText("0:00")

    # 鼠标拖动逻辑
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()
            self.press_pos = event.globalPosition().toPoint()
            
            # 立即停止动画，防止与拖动冲突
            if self.animation.state() == QPropertyAnimation.State.Running:
                self.animation.stop()
            
            # 注意：这里不再调用 undock_window，避免按下瞬间就开始动画导致的“颤抖”
                
    def mouseMoveEvent(self, event):
        if self.old_pos and event.buttons() == Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.old_pos
            
            # 如果是拖动操作（即位置发生了变化），立即清除吸附状态
            if self.is_docked:
                self.is_docked = False
                self.dock_direction = None
                self.autohide_timer.stop() # 停止自动收回
            
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event):
        # 判断是点击还是拖动
        is_click = False
        if self.press_pos:
            release_pos = event.globalPosition().toPoint()
            dist = (release_pos - self.press_pos).manhattanLength()
            if dist < 5: # 移动距离小于 5 像素视为点击
                is_click = True
        
        self.old_pos = None
        self.press_pos = None
        
        if is_click:
            # 如果是点击，且处于停靠模式（或在边缘），则弹出
            if self.is_docked:
                self.undock_window()
        else:
            # 如果是拖动结束，检测是否需要吸附
            self.check_docking()

    def enterEvent(self, event):
        # 鼠标进入，如果是停靠状态，则弹出
        if self.is_docked:
            self.undock_window()
        # 只要鼠标在窗口内，就停止自动收回计时器
        self.autohide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        # 鼠标离开，如果之前是停靠方向的，则启动定时器准备收回
        if self.dock_direction:
            self.autohide_timer.start()
        super().leaveEvent(event)

    def check_docking(self):
        screen = self.screen().availableGeometry()
        x = self.x()
        y = self.y()
        w = self.width()
        
        # 检测左边缘
        if x < self.screen_margin:
            self.dock_direction = 'left'
            self.dock_window()
        # 检测右边缘
        elif x + w > screen.width() - self.screen_margin:
            self.dock_direction = 'right'
            self.dock_window()
        # 检测上边缘
        elif y < self.screen_margin:
            self.dock_direction = 'top'
            self.dock_window()
        else:
            self.dock_direction = None
            self.is_docked = False

    def dock_window(self):
        if not self.dock_direction:
            return
            
        screen = self.screen().availableGeometry()
        target_pos = QPoint(self.x(), self.y())
        
        # 实际上我们这里 main_frame 有 10px 的 margin，所以真实边缘要考虑进去
        # 这里简化处理，直接移动窗口
        
        if self.dock_direction == 'left':
            # 收起到左边，只露出一部分
            # self.width() 包含了阴影区域，这里让窗口大部分移出屏幕
            target_pos.setX(-self.width() + self.dock_protrusion + 20) # +20 是为了补偿 main_frame 的左边距
        elif self.dock_direction == 'right':
            # 收起到右边
            target_pos.setX(screen.width() - self.dock_protrusion - 20)
        elif self.dock_direction == 'top':
            # 收起到上边
            target_pos.setY(-self.height() + self.dock_protrusion + 20)

        self.animation.setStartValue(self.pos())
        self.animation.setEndValue(target_pos)
        self.animation.start()
        self.is_docked = True

    def undock_window(self):
        if not self.dock_direction:
            return

        screen = self.screen().availableGeometry()
        target_pos = QPoint(self.x(), self.y())

        if self.dock_direction == 'left':
            # 弹出，贴着左边缘
            target_pos.setX(-10) # 微微调整以隐藏左侧阴影空隙，或者设为0
        elif self.dock_direction == 'right':
            # 弹出，贴着右边缘
            target_pos.setX(screen.width() - self.width() + 10)
        elif self.dock_direction == 'top':
            # 弹出，贴着上边缘
            target_pos.setY(-10)

        self.animation.setStartValue(self.pos())
        self.animation.setEndValue(target_pos)
        self.animation.start()
        # 注意：这里不把 is_docked 设为 False，也不清除 dock_direction
        # 这样 leaveEvent 才能知道要不要缩回去
        # is_docked = True 表示它属于“停靠模式”，只是当前可能暂时弹出了

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MusicFloatWindow()
    window.show()
    sys.exit(app.exec())
