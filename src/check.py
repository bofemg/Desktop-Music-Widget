import win32gui
import time

def get_kugou_info_from_handle():
    def callback(hwnd, titles):
        if win32gui.IsWindowVisible(hwnd) or not win32gui.IsWindowVisible(hwnd): # 无论是否可见都检查
            title = win32gui.GetWindowText(hwnd)
            if "酷狗音乐" in title:
                titles.append(title)
        return True

    titles = []
    win32gui.EnumWindows(callback, titles)
    
    if titles:
        # 过滤掉空的或者只有“酷狗音乐”四个字的
        song_info = [t for t in titles if "-" in t]
        if song_info:
            clean_title = song_info[0].replace("- 酷狗音乐", "").strip()
            print(f"🎵 深度捕获成功: {clean_title}")
        else:
            print("📻 酷狗已最小化，但当前似乎没有播放歌曲")
    else:
        print("🚫 未检测到酷狗运行")

if __name__ == "__main__":
    while True:
        get_kugou_info_from_handle()
        time.sleep(1)