"""Desktop interaction, real service wiring and thread boundaries."""

import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from cleanroom import gui


def wait_result(runner):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = runner.poll()
        if result is not None:
            return result
        time.sleep(0.005)
    pytest.fail("后台任务未返回结果")


def test_runner_keeps_work_off_main_thread_and_prevents_duplicate_jobs():
    runner = gui.TaskRunner()
    release = threading.Event()
    main_thread = threading.get_ident()

    def work():
        assert release.wait(2)
        return threading.get_ident()

    assert runner.submit(work)
    assert runner.busy
    assert not runner.submit(lambda: None)
    assert runner.poll() is None
    release.set()
    success, worker_thread = wait_result(runner)
    assert success and worker_thread != main_thread
    assert not runner.busy


def test_runner_preserves_service_error_and_can_retry():
    runner = gui.TaskRunner()

    def fail():
        raise ValueError("禁止覆盖输入或已有文件")

    assert runner.submit(fail)
    assert wait_result(runner) == (False, "禁止覆盖输入或已有文件")
    assert runner.submit(lambda: {"ok": True})
    assert wait_result(runner) == (True, {"ok": True})


@pytest.fixture
def desktop(monkeypatch):
    try:
        root = gui.tk.Tk()
    except gui.tk.TclError as exc:
        pytest.skip(f"没有可用的 Tk 显示环境：{exc}")
    root.withdraw()
    errors = Mock()
    monkeypatch.setattr(gui.messagebox, "showerror", errors)
    app = gui.DocumentApp(root)
    yield app, errors
    root.destroy()


def finish(app):
    deadline = time.monotonic() + 4
    while app.runner.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    assert not app.runner.busy


def test_desktop_defaults_and_required_paths(desktop):
    app, errors = desktop
    assert app.mode.get() == "preserve"
    assert app.remove_invisibles.get()
    assert not app.allow_lossy.get()
    app.inspect()
    assert "输入" in errors.call_args.args[1]
    app.source.set("example.txt")
    app.process()
    assert "输出" in errors.call_args.args[1]
    assert not app.runner.busy


def test_desktop_inspects_and_cleans_real_file(desktop, tmp_path):
    app, errors = desktop
    source = tmp_path / "来源.txt"
    output = tmp_path / "输出.txt"
    source.write_text("示例\u200b文字。", encoding="utf-8")
    app.source.set(str(source))
    app.output.set(str(output))
    app.inspect_button.invoke()
    assert app.inspect_button.instate(["disabled"])
    finish(app)
    assert "selected_count" in app.results.get("1.0", "end")
    assert "检查完成" in app.status.get()
    app.process_button.invoke()
    finish(app)
    assert output.read_text(encoding="utf-8") == "示例文字。"
    assert Path(str(output) + ".audit.json").exists()
    assert "已导出" in app.status.get()
    assert app.process_button.instate(["!disabled"])
    errors.assert_not_called()


def test_desktop_shows_core_overwrite_error_without_changing_file(desktop, tmp_path):
    app, errors = desktop
    source = tmp_path / "input.txt"
    output = tmp_path / "exists.txt"
    source.write_text("original", encoding="utf-8")
    output.write_text("keep this", encoding="utf-8")
    app.source.set(str(source))
    app.output.set(str(output))
    app.process()
    finish(app)
    assert "禁止覆盖" in errors.call_args.args[1]
    assert output.read_text(encoding="utf-8") == "keep this"
    assert "失败" in app.status.get()


def test_desktop_snapshots_options_before_background_work(desktop, monkeypatch):
    app, _ = desktop
    seen = []
    main_thread = threading.get_ident()

    def fake_clean(source, output, **kwargs):
        seen.append((source, output, kwargs, threading.get_ident()))
        return {"verification": {"expected_text_matches": True}}

    monkeypatch.setattr(gui, "clean_document", fake_clean)
    app.source.set("input.docx")
    app.output.set("output.txt")
    app.mode.set("rebuild")
    app.remove_invisibles.set(False)
    app.allow_lossy.set(True)
    app.process()
    finish(app)
    assert seen[0][:3] == (
        Path("input.docx"), Path("output.txt"),
        {"mode": "rebuild", "remove_invisibles": False, "allow_lossy": True},
    )
    assert seen[0][3] != main_thread


def test_browse_uses_input_extension_and_does_not_promise_overwrite(desktop, monkeypatch):
    app, _ = desktop
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **kwargs: "C:/docs/paper.pdf")
    app.choose_source()
    assert Path(app.output.get()).name == "paper.cleaned.pdf"
    save = Mock(return_value="C:/docs/result.txt")
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", save)
    app.mode.set("rebuild")
    app.choose_output()
    assert app.output.get() == "C:/docs/result.txt"
    assert save.call_args.kwargs["confirmoverwrite"] is False


def test_close_while_busy_keeps_window_alive(desktop, monkeypatch):
    app, _ = desktop
    warning = Mock()
    destroy = Mock()
    monkeypatch.setattr(gui.messagebox, "showwarning", warning)
    monkeypatch.setattr(app.root, "destroy", destroy)
    app.runner.submit(lambda: None)
    app.close()
    warning.assert_called_once()
    destroy.assert_not_called()
    wait_result(app.runner)
    app.close()
    destroy.assert_called_once()


def test_launch_builds_app_and_enters_event_loop(monkeypatch):
    root = Mock()
    app = Mock()
    monkeypatch.setattr(gui.tk, "Tk", Mock(return_value=root))
    monkeypatch.setattr(gui, "DocumentApp", app)
    gui.launch()
    app.assert_called_once_with(root)
    root.mainloop.assert_called_once()
