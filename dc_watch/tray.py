from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class TrayController:
    on_show_status: Callable[[], None]
    on_start: Callable[[], None]
    on_stop: Callable[[], None]
    on_recent: Callable[[], None]
    on_settings: Callable[[], None]
    on_open_logs: Callable[[], None]
    on_install_start: Callable[[], None]
    on_uninstall_start: Callable[[], None]
    on_quit: Callable[[], None]
    status_text: str = "DCWatch 대기 중"

    def run(self) -> bool:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            return False

        image = Image.new("RGB", (64, 64), "#d63b3b")
        draw = ImageDraw.Draw(image)
        draw.rectangle((14, 14, 50, 50), outline="white", width=4)
        menu = pystray.Menu(
            pystray.MenuItem("상태 보기", lambda: self.on_show_status()),
            pystray.MenuItem("감시 시작", lambda: self.on_start()),
            pystray.MenuItem("감시 중지", lambda: self.on_stop()),
            pystray.MenuItem("최근 경보 목록", lambda: self.on_recent()),
            pystray.MenuItem("설정 열기", lambda: self.on_settings()),
            pystray.MenuItem("로그 폴더 열기", lambda: self.on_open_logs()),
            pystray.MenuItem("Windows 시작 시 자동 실행 등록", lambda: self.on_install_start()),
            pystray.MenuItem("Windows 시작 시 자동 실행 해제", lambda: self.on_uninstall_start()),
            pystray.MenuItem("종료", lambda: self.on_quit()),
        )
        icon = pystray.Icon("DCWatch", image, self.status_text, menu)
        icon.run()
        return True
