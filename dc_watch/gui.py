from __future__ import annotations

import os
import logging
import threading
import time
import webbrowser
from pathlib import Path

from .autostart import install_autostart, uninstall_autostart
from .cli import remember_file, remember_post, run_watch
from .config import AppConfig, ensure_user_files, load_config, save_config
from .db import Database
from .image_scan import reset_nudenet_detector
from .local_popup import enqueue_parented_popup, show_test_popup
from .logging_setup import setup_logging
from .models import Alert
from .optional_deps import OptionalDependencyInstallResult, install_nudenet, is_nudenet_available

log = logging.getLogger(__name__)


class DCWatchApp:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("DCWatch")
        self.root.geometry("760x620")
        self.config = load_config()
        ensure_user_files(self.config)
        setup_logging(self.config)
        self.db = Database(self.config.database_file)
        self.db.migrate()
        self.stop_event: threading.Event | None = None
        self.worker: threading.Thread | None = None
        self.status_var = tk.StringVar(value="대기 중")
        self.vars: dict[str, tk.Variable] = {}
        self._build()

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.mainloop()

    def _build(self) -> None:
        tk = self.tk
        ttk = self.ttk
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)

        status = ttk.Label(container, textvariable=self.status_var)
        status.pack(anchor="w", pady=(0, 8))

        settings = ttk.LabelFrame(container, text="설정", padding=10)
        settings.pack(fill="both", expand=True)
        self._add_entry(settings, "gallery_id", "갤러리 ID")
        self._add_choice(settings, "gallery_type", "갤러리 타입", ["minor", "major"])
        self._add_entry(settings, "poll_seconds", "폴링 간격 초", int)
        self._add_entry(settings, "jitter_seconds", "큐 처리 간격 초", int)
        self._add_entry(settings, "pages_to_scan", "스캔 페이지 수", int)
        self._add_check(settings, "scan_existing_on_first_run", "첫 실행 시 기존 글 검사")
        self._add_entry(settings, "phash_strict_threshold", "pHash strict threshold", int)
        self._add_entry(settings, "phash_threshold", "pHash threshold", int)
        self._add_check(settings, "enable_tile_phash", "크롭 대응 타일 pHash 사용")
        self._add_entry(settings, "tile_phash_grid_size", "타일 pHash grid size", int)
        self._add_entry(settings, "tile_phash_threshold", "타일 pHash threshold", int)
        self._add_entry(settings, "tile_phash_min_matches", "타일 pHash 최소 매칭 수", int)
        self._add_check(settings, "tile_phash_single_match_is_weak", "단일 타일 pHash는 보조 신호")
        self._add_check(settings, "enable_crop_resistant_hash", "crop-resistant hash")
        self._add_entry(settings, "crop_hash_region_cutoff", "crop hash region cutoff", int)
        self._add_entry(settings, "crop_hash_hamming_cutoff", "crop hash hamming cutoff", int)
        self._add_check(settings, "enable_orb_matching", "OpenCV ORB matching")
        self._add_entry(settings, "orb_max_features", "ORB max features", int)
        self._add_entry(settings, "orb_distance_threshold", "ORB distance threshold", int)
        self._add_entry(settings, "orb_min_matches", "ORB min matches", int)
        self._add_check(settings, "enable_nudenet", "NudeNet/CNN nudity detection")
        self._add_entry(settings, "nude_score_threshold", "NudeNet score threshold", float)
        self._add_check(settings, "alert_on_analysis_failure", "분석 실패도 warning 팝업")
        self._add_check(settings, "high_popup_enabled", "high 위험도 팝업 사용")
        self._add_check(settings, "warning_popup_enabled", "warning 위험도 팝업 사용")
        self._add_check(settings, "topmost_popup", "항상 위 팝업")
        self._add_check(settings, "suppress_image_urls_in_logs", "이미지 URL 로그 숨김")
        self.vars["windows_autostart"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings, text="Windows 시작 시 자동 실행 여부(아래 버튼으로 변경)", variable=self.vars["windows_autostart"]).pack(anchor="w", pady=1)

        buttons = ttk.Frame(container)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="테스트 팝업", command=show_test_popup).pack(side="left")
        ttk.Button(buttons, text="감시 시작", command=self.start_watch).pack(side="left", padx=4)
        ttk.Button(buttons, text="감시 중지", command=self.stop_watch).pack(side="left", padx=4)
        ttk.Button(buttons, text="최근 경보 보기", command=self.show_recent_alerts).pack(side="left", padx=4)
        ttk.Button(buttons, text="로컬 이미지 해시 DB 등록", command=self.remember_local_image_file).pack(side="left", padx=4)
        ttk.Button(buttons, text="bad hash DB 관리", command=self.show_bad_hash_manager).pack(side="left", padx=4)
        ttk.Button(buttons, text="NudeNet 설치/확인", command=self.check_or_install_nudenet).pack(side="left", padx=4)
        ttk.Button(buttons, text="로그 폴더 열기", command=self.open_log_folder).pack(side="left", padx=4)
        ttk.Button(buttons, text="자동 실행 등록", command=self.install_start).pack(side="left", padx=4)
        ttk.Button(buttons, text="자동 실행 해제", command=self.uninstall_start).pack(side="left", padx=4)
        ttk.Button(buttons, text="설정 저장", command=self.save_settings).pack(side="right")

    def _add_entry(self, parent, name: str, label: str, cast=str) -> None:
        row = self.ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        self.ttk.Label(row, text=label, width=24).pack(side="left")
        value = getattr(self.config, name)
        var = self.tk.StringVar(value=str(value))
        self.vars[name] = var
        entry = self.ttk.Entry(row, textvariable=var)
        entry.pack(side="left", fill="x", expand=True)

    def _add_choice(self, parent, name: str, label: str, values: list[str]) -> None:
        row = self.ttk.Frame(parent)
        row.pack(fill="x", pady=2)
        self.ttk.Label(row, text=label, width=24).pack(side="left")
        var = self.tk.StringVar(value=str(getattr(self.config, name)))
        self.vars[name] = var
        self.ttk.Combobox(row, textvariable=var, values=values, state="readonly").pack(side="left", fill="x", expand=True)

    def _add_check(self, parent, name: str, label: str) -> None:
        var = self.tk.BooleanVar(value=bool(getattr(self.config, name)))
        self.vars[name] = var
        self.ttk.Checkbutton(parent, text=label, variable=var).pack(anchor="w", pady=1)

    def save_settings(self) -> None:
        current = self.config
        updated = AppConfig(
            gallery_id=str(self.vars["gallery_id"].get()),
            gallery_type=str(self.vars["gallery_type"].get()),
            poll_seconds=int(str(self.vars["poll_seconds"].get())),
            jitter_seconds=int(str(self.vars["jitter_seconds"].get())),
            pages_to_scan=int(str(self.vars["pages_to_scan"].get())),
            scan_existing_on_first_run=bool(self.vars["scan_existing_on_first_run"].get()),
            phash_strict_threshold=int(str(self.vars["phash_strict_threshold"].get())),
            phash_threshold=int(str(self.vars["phash_threshold"].get())),
            enable_tile_phash=bool(self.vars["enable_tile_phash"].get()),
            tile_phash_grid_size=int(str(self.vars["tile_phash_grid_size"].get())),
            tile_phash_threshold=int(str(self.vars["tile_phash_threshold"].get())),
            tile_phash_min_matches=int(str(self.vars["tile_phash_min_matches"].get())),
            tile_phash_single_match_is_weak=bool(self.vars["tile_phash_single_match_is_weak"].get()),
            enable_crop_resistant_hash=bool(self.vars["enable_crop_resistant_hash"].get()),
            crop_hash_region_cutoff=int(str(self.vars["crop_hash_region_cutoff"].get())),
            crop_hash_hamming_cutoff=int(str(self.vars["crop_hash_hamming_cutoff"].get())),
            enable_orb_matching=bool(self.vars["enable_orb_matching"].get()),
            orb_max_features=int(str(self.vars["orb_max_features"].get())),
            orb_distance_threshold=int(str(self.vars["orb_distance_threshold"].get())),
            orb_min_matches=int(str(self.vars["orb_min_matches"].get())),
            enable_nudenet=bool(self.vars["enable_nudenet"].get()),
            nude_score_threshold=float(str(self.vars["nude_score_threshold"].get())),
            alert_on_analysis_failure=bool(self.vars["alert_on_analysis_failure"].get()),
            high_popup_enabled=bool(self.vars["high_popup_enabled"].get()),
            warning_popup_enabled=bool(self.vars["warning_popup_enabled"].get()),
            topmost_popup=bool(self.vars["topmost_popup"].get()),
            suppress_image_urls_in_logs=bool(self.vars["suppress_image_urls_in_logs"].get()),
            database_path=current.database_path,
            log_level=current.log_level,
            use_appdata_dir=current.use_appdata_dir,
        )
        updated._base_dir = current._base_dir
        updated._appdata_dir = current._appdata_dir
        self.config = updated
        ensure_user_files(self.config)
        save_config(self.config)
        self.db = Database(self.config.database_file)
        self.db.migrate()
        self.status_var.set("설정 저장됨")
        if self.config.enable_nudenet and not is_nudenet_available():
            self.status_var.set("NudeNet 설치 필요")
            self.root.after(0, self.check_or_install_nudenet)

    def start_watch(self) -> None:
        if self.worker and self.worker.is_alive():
            self.status_var.set("이미 감시 중")
            return
        self.save_settings()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(
            target=run_watch,
            kwargs={
                "config": self.config,
                "db": self.db,
                "once": False,
                "stop_event": self.stop_event,
                "popup_handler": self._schedule_popup,
                "status_handler": self._set_status_threadsafe,
            },
            daemon=True,
        )
        self.worker.start()
        self.status_var.set("감시 시작됨")

    def stop_watch(self) -> None:
        if self.stop_event:
            self.stop_event.set()
        self.status_var.set("감시 중지 요청됨")

    def show_recent_alerts(self) -> None:
        tk = self.tk
        ttk = self.ttk
        win = tk.Toplevel(self.root)
        win.title("최근 경보")
        win.geometry("860x420")
        columns = ("post_no", "title", "risk", "reasons", "time", "ack")
        tree = ttk.Treeview(win, columns=columns, show="headings")
        headings = {
            "post_no": "글번호",
            "title": "제목",
            "risk": "위험도",
            "reasons": "위험 사유 요약",
            "time": "경보 시각",
            "ack": "확인 여부",
        }
        for column, text in headings.items():
            tree.heading(column, text=text)
            tree.column(column, width=120 if column != "title" else 260)
        alerts = self.db.recent_alerts(100)
        by_post = {alert.post_no: alert for alert in alerts}
        for alert in alerts:
            tree.insert(
                "",
                "end",
                iid=alert.post_no,
                values=(
                    alert.post_no,
                    alert.title,
                    alert.risk,
                    "; ".join(alert.reasons[:2]),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(alert.alerted_at or 0)),
                    "확인" if alert.acknowledged_at else "미확인",
                ),
            )
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=8, pady=(0, 8))

        def selected() -> Alert | None:
            item = tree.focus()
            return by_post.get(item)

        def open_post() -> None:
            alert = selected()
            if alert:
                webbrowser.open(alert.url)

        def acknowledge() -> None:
            alert = selected()
            if alert:
                self.db.acknowledge_alert(alert.post_no)
                win.destroy()
                self.show_recent_alerts()

        def remember() -> None:
            alert = selected()
            if alert:
                self._confirm_and_remember_alert(alert)

        ttk.Button(buttons, text="게시글 열기", command=open_post).pack(side="left")
        ttk.Button(buttons, text="확인 처리", command=acknowledge).pack(side="left", padx=4)
        ttk.Button(buttons, text="확정 테러 해시 DB에 등록", command=remember).pack(side="left", padx=4)

    def show_bad_hash_manager(self) -> None:
        from tkinter import messagebox

        tk = self.tk
        ttk = self.ttk
        win = tk.Toplevel(self.root)
        win.title("bad hash DB 관리")
        win.geometry("920x420")
        columns = ("id", "label", "variant", "source_post_no", "source_file", "tiles", "crop", "orb", "added_at")
        tree = ttk.Treeview(win, columns=columns, show="headings")
        headings = {
            "id": "id",
            "label": "label",
            "variant": "variant",
            "source_post_no": "source post",
            "source_file": "source file",
            "tiles": "tile hashes",
            "crop": "crop hash",
            "orb": "ORB",
            "added_at": "added at",
        }
        widths = {
            "id": 70,
            "label": 220,
            "variant": 150,
            "source_post_no": 120,
            "source_file": 220,
            "tiles": 90,
            "crop": 80,
            "orb": 80,
            "added_at": 160,
        }
        for column, text in headings.items():
            tree.heading(column, text=text)
            tree.column(column, width=widths[column])
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        def reload_rows() -> None:
            for item in tree.get_children():
                tree.delete(item)
            for bad_hash in self.db.get_bad_hashes():
                if bad_hash.id is None:
                    continue
                source_file = Path(bad_hash.source_file).name if bad_hash.source_file else ""
                added_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(bad_hash.added_at or 0))
                tree.insert(
                    "",
                    "end",
                    iid=str(bad_hash.id),
                    values=(
                        bad_hash.id,
                        bad_hash.label,
                        bad_hash.variant,
                        bad_hash.source_post_no or "",
                        source_file,
                        len(bad_hash.tile_phashes),
                        "yes" if bad_hash.crop_hash else "no",
                        "yes" if bad_hash.orb_descriptor else "no",
                        added_at,
                    ),
                )

        def selected_id() -> int | None:
            item = tree.focus()
            if not item:
                return None
            try:
                return int(item)
            except ValueError:
                return None

        def delete_selected() -> None:
            bad_hash_id = selected_id()
            if bad_hash_id is None:
                messagebox.showinfo("bad hash DB 관리", "삭제할 항목을 선택하세요.", parent=win)
                return
            ok = messagebox.askyesno(
                "bad hash 삭제",
                f"선택한 bad hash id={bad_hash_id} 항목을 삭제할까요?\n\n연결된 타일 pHash도 함께 삭제됩니다.",
                parent=win,
            )
            if not ok:
                return
            deleted = self.db.delete_bad_hash(bad_hash_id)
            if deleted:
                self.status_var.set(f"bad hash 삭제 완료: id={bad_hash_id}")
                reload_rows()
            else:
                messagebox.showwarning("bad hash 삭제", "이미 삭제되었거나 찾을 수 없는 항목입니다.", parent=win)

        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons, text="선택 삭제", command=delete_selected).pack(side="left")
        ttk.Button(buttons, text="새로고침", command=reload_rows).pack(side="left", padx=4)
        ttk.Button(buttons, text="닫기", command=win.destroy).pack(side="right")
        reload_rows()

    def check_or_install_nudenet(self) -> None:
        from tkinter import messagebox

        if is_nudenet_available():
            self.vars["enable_nudenet"].set(True)
            self.config.enable_nudenet = True
            save_config(self.config)
            self.status_var.set("NudeNet 사용 가능")
            messagebox.showinfo("NudeNet 설치/확인", "NudeNet이 이미 사용 가능합니다.", parent=self.root)
            return

        ok = messagebox.askyesno(
            "NudeNet 설치",
            "NudeNet이 현재 실행 환경에 없습니다.\n\n"
            "개발 모드에서는 현재 Python 환경에 nudenet>=3.4 설치를 시도합니다.\n"
            "PyInstaller EXE에서는 샌드박스를 깨지 않기 위해 외부 Python이나 사용자 폴더에 설치하지 않습니다.\n\n"
            "지금 설치/확인을 진행할까요?",
            parent=self.root,
        )
        if not ok:
            self._disable_nudenet_setting()
            self.status_var.set("NudeNet 설치 취소됨")
            return

        self.status_var.set("NudeNet 설치/확인 중...")
        threading.Thread(target=self._install_nudenet_worker, daemon=True).start()

    def _install_nudenet_worker(self) -> None:
        result = install_nudenet()
        if result.installed:
            reset_nudenet_detector()
        self.root.after(0, lambda: self._show_nudenet_install_result(result))

    def _show_nudenet_install_result(self, result: OptionalDependencyInstallResult) -> None:
        from tkinter import messagebox

        if result.installed:
            self.vars["enable_nudenet"].set(True)
            self.config.enable_nudenet = True
            save_config(self.config)
            self.status_var.set("NudeNet 설치/확인 완료")
            messagebox.showinfo("NudeNet 설치/확인", result.message, parent=self.root)
            return

        self._disable_nudenet_setting()
        self.status_var.set("NudeNet 사용 불가")
        detail = result.message
        if result.stderr:
            detail += "\n\n" + result.stderr[-1200:]
        messagebox.showwarning("NudeNet 설치/확인 실패", detail, parent=self.root)

    def _disable_nudenet_setting(self) -> None:
        self.vars["enable_nudenet"].set(False)
        self.config.enable_nudenet = False
        save_config(self.config)

    def open_log_folder(self) -> None:
        self.config.log_dir.mkdir(parents=True, exist_ok=True)
        path = str(self.config.log_dir)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(Path(path).as_uri())

    def remember_local_image_file(self) -> None:
        from tkinter import filedialog, messagebox, simpledialog

        file_path = filedialog.askopenfilename(
            parent=self.root,
            title="로컬 이미지 파일 선택",
            filetypes=[
                ("Image files", "*.jpg *.jpeg *.png *.webp *.gif *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return
        path = Path(file_path)
        label = simpledialog.askstring(
            "해시 DB 라벨",
            "이 이미지 해시에 붙일 라벨을 입력하세요.",
            initialvalue=f"known_attack_{int(time.time())}",
            parent=self.root,
        )
        if not label:
            return
        ok = messagebox.askyesno(
            "로컬 이미지 해시 DB 등록",
            f"선택한 파일의 SHA-256/pHash를 bad hash DB에 등록할까요?\n\n파일명: {path.name}\n라벨: {label}\n\n이미지는 화면에 표시하지 않습니다.",
            parent=self.root,
        )
        if not ok:
            return
        try:
            bad_ids = remember_file(self.db, path, label, self.config)
        except Exception as exc:
            messagebox.showerror("등록 실패", f"이미지 해시 등록 실패: {exc.__class__.__name__}", parent=self.root)
            self.status_var.set(f"로컬 이미지 해시 DB 등록 실패: {exc.__class__.__name__}")
            return
        self.status_var.set(f"로컬 이미지 해시 DB 등록 완료: {len(bad_ids)}개 variant")
        messagebox.showinfo("등록 완료", f"bad hash DB에 등록했습니다.\n\nvariant records={len(bad_ids)}", parent=self.root)

    def on_close(self) -> None:
        self.stop_watch()
        self.root.destroy()

    def _schedule_popup(self, alert: Alert) -> None:
        topmost = self.config.topmost_popup or alert.risk == "high"
        self.root.after(0, lambda: self._show_alert_popup(alert, topmost))

    def _show_alert_popup(self, alert: Alert, topmost: bool) -> None:
        try:
            enqueue_parented_popup(self.root, alert, topmost=topmost, on_remember_bad_hash=self._remember_alert_confirmed)
            log.info("scheduled local popup for post %s", alert.post_no)
        except Exception as exc:
            log.exception("failed to schedule local popup for post %s", alert.post_no)
            self.status_var.set(f"popup failed: {exc.__class__.__name__}")

    def _confirm_and_remember_alert(self, alert: Alert) -> None:
        from tkinter import messagebox

        ok = messagebox.askyesno(
            "확정 테러 해시 DB 등록",
            "사용자가 직접 확인한 글의 저장된 이미지 해시를 확정 테러 해시 DB에 등록할까요?",
            parent=self.root,
        )
        if ok:
            self._remember_alert_confirmed(alert)

    def _remember_alert_confirmed(self, alert: Alert) -> None:
        label = f"confirmed_{alert.post_no}_{int(time.time())}"
        ids = remember_post(self.db, alert.post_no, label, self.config)
        self.status_var.set(f"해시 DB 등록: {len(ids)}개")

    def _set_status_threadsafe(self, message: str) -> None:
        self.root.after(0, lambda: self.status_var.set(message))

    def install_start(self) -> None:
        from tkinter import messagebox

        result = install_autostart()
        self.vars["windows_autostart"].set(result.returncode == 0)
        text = result.stdout or result.stderr or "완료"
        messagebox.showinfo("자동 실행 등록", text, parent=self.root)

    def uninstall_start(self) -> None:
        from tkinter import messagebox

        result = uninstall_autostart()
        self.vars["windows_autostart"].set(False)
        text = result.stdout or result.stderr or "완료"
        messagebox.showinfo("자동 실행 해제", text, parent=self.root)


def main() -> None:
    DCWatchApp().run()


if __name__ == "__main__":  # pragma: no cover
    main()
