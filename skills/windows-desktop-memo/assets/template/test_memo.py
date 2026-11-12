import ctypes as ct
from ctypes import wintypes as wt
from datetime import date, timedelta
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from PIL import Image

from storage import Store
from app import App, user32


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "data.json"
        self.store = Store(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_restart_preserves_unicode_completed_and_settings(self):
        task = self.store.add("完成论文图表与 Python 练习", "9:05")
        self.store.toggle(task["id"])
        self.store.data["settings"]["opacity"] = 45
        self.store.save()
        reopened = Store(self.path)
        self.assertEqual(reopened.tasks()[0]["text"], task["text"])
        self.assertEqual(reopened.tasks()[0]["time"], "09:05")
        self.assertTrue(reopened.tasks()[0]["done"])
        self.assertEqual(reopened.data["settings"]["opacity"], 45)

    def test_dates_do_not_mix_and_delete_is_reversible(self):
        task = self.store.add("昨天任务", day="2026-11-09")
        self.store.add("今天任务", day="2026-11-10")
        self.assertEqual(len(self.store.tasks("2026-11-10")), 1)
        index, deleted = self.store.delete(task["id"], "2026-11-09")
        self.assertEqual(self.store.tasks("2026-11-09"), [])
        self.store.tasks("2026-11-09").insert(index, deleted)
        self.assertEqual(self.store.tasks("2026-11-09")[0]["text"], "昨天任务")

    def test_invalid_input_does_not_add_tasks(self):
        for text, time in ((" ", ""), ("任务", "25:70"), ("字" * 301, "")):
            with self.assertRaises(ValueError):
                self.store.add(text, time)
        self.assertEqual(self.store.tasks(), [])

    def test_corrupt_primary_recovers_backup_and_keeps_original(self):
        self.store.add("应保留的任务")
        self.store.save()
        self.store.save()
        self.path.write_text("broken data", encoding="utf-8")
        recovered = Store(self.path)
        self.assertEqual(recovered.tasks()[0]["text"], "应保留的任务")
        self.assertTrue(recovered.recovery_message)
        self.assertEqual(len(list(self.path.parent.glob("data-damaged-*"))), 1)
        recovered.save()
        self.assertEqual(Store(self.path).tasks()[0]["text"], "应保留的任务")

    def test_atomic_failure_keeps_last_saved_file(self):
        self.store.add("已保存")
        self.store.save()
        self.store.add("尚未保存")
        with patch("storage.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.save()
        self.assertEqual(len(Store(self.path).tasks()), 1)


class WindowTests(unittest.TestCase):
    def test_smallest_daily_window_survives_save_and_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            self.addCleanup(root.destroy)
            app = App(root, Store(Path(directory) / "data.json"), testing=True)
            app.store.add("今日计划")
            app.open_settings()
            app.appearance_variables["width"].set(220)
            app.appearance_variables["height"].set(220)
            root.update()
            self.assertEqual((root.winfo_width(), root.winfo_height()), (220, 220))
            self.assertFalse(app.week_only)
            self.assertFalse(app.calendar_frame.winfo_ismapped())
            app.appearance_variables["calendar_mode"].set("week")
            root.update()
            self.assertTrue(app.week_only)
            self.assertGreaterEqual(app.canvas.winfo_height(), 40)
            self.assertEqual(len(app.calendar_frame.winfo_children()), 14)
            self.assertTrue(app.task_labels[0].winfo_ismapped())
            app.settings_apply_button.invoke()
            saved = Store(app.store.path).data["settings"]
            self.assertEqual((saved["width"], saved["height"]), (220, 220))
            app.set_mode(True)
            root.update()
            self.assertGreaterEqual(root.winfo_width(), 340)
            self.assertGreaterEqual(root.winfo_height(), 530)
            self.assertTrue(app.entry.winfo_ismapped())
            self.assertTrue(app.undo_button.winfo_ismapped())
            self.assertGreater(app.canvas.winfo_height(), 80)
            self.assertEqual(app.store.data["settings"]["width"], 220)
            app.daily()
            root.update()
            self.assertEqual((root.winfo_width(), root.winfo_height()), (220, 220))
            app.open_settings()
            app.appearance_variables["width"].set(400)
            app.appearance_variables["height"].set(660)
            root.update()
            self.assertTrue(app.week_only)
            app.appearance_variables["calendar_mode"].set("date")
            root.update()
            self.assertFalse(app.week_only)
            self.assertFalse(app.calendar_frame.winfo_ismapped())
            self.assertTrue(app.daily_canvas.winfo_ismapped())

    def test_modes_actions_day_rollover_and_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            self.addCleanup(root.destroy)
            app = App(root, Store(Path(directory) / "data.json"), testing=True)
            root.update()
            hwnd = app.hwnd()
            style = user32.GetWindowLongW(hwnd, -20)
            self.assertTrue(style & 0x20, "daily must pass mouse clicks through")
            self.assertTrue(style & 0x08000000, "daily must not take focus")
            self.assertAlmostEqual(root.attributes("-alpha"), .72, places=2)
            app.set_mode(True)
            root.update()
            self.assertFalse(user32.GetWindowLongW(hwnd, -20) & 0x20)
            self.assertEqual(root.attributes("-alpha"), 1.0)
            self.assertTrue(app.entry.winfo_ismapped())
            self.assertGreater(app.canvas.winfo_height(), 80)
            # Query the Windows hit-test result at a point inside the calendar.
            # This verifies OS routing, not just the extended style flag.
            user32.WindowFromPoint.argtypes = [wt.POINT]
            user32.WindowFromPoint.restype = wt.HWND
            point = wt.POINT(root.winfo_x() + 50, root.winfo_y() + 70)
            hit = user32.WindowFromPoint(point)
            self.assertEqual(user32.GetAncestor(hit, 2), hwnd)
            app.daily()
            root.update()
            hit = user32.WindowFromPoint(point)
            self.assertNotEqual(user32.GetAncestor(hit, 2), hwnd,
                                "Windows should route hits to the window underneath")
            app.set_mode(True)
            root.update()
            app.entry.insert(0, "测试计划：添加、完成、撤回及删除")
            app.time_entry.insert(0, "14:30")
            app.add_task()
            task_id = app.store.tasks()[0]["id"]
            app.toggle_task(task_id)
            self.assertTrue(app.store.tasks()[0]["done"])
            app.toggle_task(task_id)
            self.assertFalse(app.store.tasks()[0]["done"])
            app.delete_task(task_id)
            self.assertEqual(app.store.tasks(), [])
            app.undo_delete()
            self.assertEqual(len(app.store.tasks()), 1)
            for i in range(25):
                app.store.add(f"滚动测试计划 {i}，包含中文的长内容" * 2)
            app.render_tasks()
            root.update()
            self.assertGreater(app.task_frame.winfo_reqheight(), app.canvas.winfo_height())
            tomorrow = date.today() + timedelta(days=1)
            with patch("app.date") as mocked:
                mocked.today.return_value = tomorrow
                app.sync_day()
                self.assertEqual(app.current_day, tomorrow)
                self.assertEqual(len(app.store.tasks(tomorrow.isoformat())), 0)
                self.assertEqual(len(app.store.tasks(date.today().isoformat())), 26)
            app.sync_day()
            app.store.data["settings"].update(width=360, height=600, opacity=45)
            app.apply_size()
            app.daily()
            root.update()
            self.assertAlmostEqual(root.attributes("-alpha"), .45, places=2)
            self.assertEqual(root.winfo_width(), 360)
            self.assertFalse(app.entry.winfo_ismapped())
            area = app.work_area()
            self.assertEqual(root.winfo_x() + root.winfo_width(), area.right - 16)
            self.assertEqual(root.winfo_y() + root.winfo_height(), area.bottom - 16)

    def test_appearance_live_preview_cancel_and_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            self.addCleanup(root.destroy)
            app = App(root, Store(Path(directory) / "data.json"), testing=True)
            root.update()
            app.save()
            original = app.store.data["settings"].copy()
            app.set_mode(True)
            app.open_settings()
            app.appearance_variables["opacity"].set(40)
            app.appearance_variables["width"].set(520)
            app.appearance_variables["height"].set(720)
            root.update()
            self.assertAlmostEqual(root.attributes("-alpha"), .40, places=2)
            self.assertEqual((root.winfo_width(), root.winfo_height()), (520, 720))
            self.assertTrue(app.daily_canvas.winfo_ismapped())
            self.assertEqual(app.settings_window.attributes("-alpha"), 1.0)
            self.assertLess(app.settings_window.winfo_x() + app.settings_window.winfo_width(), root.winfo_x())
            self.assertEqual(app.store.data["settings"], original)
            self.assertEqual(Store(app.store.path).data["settings"], original)
            app.settings_cancel_button.invoke()
            root.update()
            self.assertTrue(app.editing)
            self.assertEqual(root.attributes("-alpha"), 1.0)
            self.assertEqual(root.winfo_width(), original["width"])
            app.open_settings()
            app.appearance_variables["opacity"].set(55)
            app.appearance_variables["width"].set(480)
            with patch.object(app, "set_startup") as startup:
                app.settings_apply_button.invoke()
                startup.assert_not_called()
            root.update()
            self.assertFalse(app.editing)
            self.assertAlmostEqual(root.attributes("-alpha"), .55, places=2)
            self.assertEqual(root.winfo_width(), 480)
            saved = Store(app.store.path).data["settings"]
            self.assertEqual((saved["opacity"], saved["width"]), (55, 480))
            # A global mode switch also discards an uncommitted preview.
            app.open_settings()
            app.appearance_variables["width"].set(600)
            app.set_mode(True)
            root.update()
            self.assertEqual(root.winfo_width(), 480)
            self.assertIsNone(app.settings_window)

    def test_wallpaper_preview_cancel_copy_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            self.addCleanup(root.destroy)
            source = Path(directory) / "背景测试.png"
            Image.new("RGB", (600, 400), "#2475B5").save(source)
            store = Store(Path(directory) / "data" / "data.json")
            app = App(root, store, testing=True)
            app.save()
            app.open_settings()
            app.set_wallpaper_preview(str(source))
            root.update()
            self.assertIsNotNone(app.daily_photo)
            self.assertTrue(any(app.daily_canvas.type(item) == "image" for item in app.daily_canvas.find_all()))
            self.assertEqual(store.data["settings"]["wallpaper"], "")
            app.appearance_variables["width"].set(300)
            root.update()
            self.assertEqual(app.daily_photo.width(), 298)
            app.settings_cancel_button.invoke()
            root.update()
            self.assertIsNone(app.daily_photo)
            app.open_settings()
            app.set_wallpaper_preview(str(source))
            app.settings_apply_button.invoke()
            root.update()
            saved = Path(Store(store.path).data["settings"]["wallpaper"])
            self.assertTrue(saved.is_file())
            self.assertNotEqual(source, saved)
            source.rename(source.with_suffix(".moved.png"))
            from wallpaper import read_wallpaper
            self.assertEqual(read_wallpaper(saved).size, (600, 400))
            bad = Path(directory) / "invalid.jpg"
            bad.write_text("not an image", encoding="utf-8")
            app.open_settings()
            with self.assertRaises(OSError):
                app.set_wallpaper_preview(str(bad))
            self.assertEqual(app.appearance_variables["wallpaper"].get(), str(saved))
            app.appearance_variables["wallpaper"].set("")
            root.update()
            self.assertIsNone(app.daily_photo)
            app.settings_apply_button.invoke()
            self.assertEqual(Store(store.path).data["settings"]["wallpaper"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
