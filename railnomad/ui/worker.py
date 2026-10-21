"""One owned child process per operation; no invisible workers after close."""
import json
import sys
import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, Signal, QTimer


class Worker(QObject):
    event = Signal(dict)
    finished = Signal()

    def __init__(self, home, parent=None):
        super().__init__(parent)
        self.home = home
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self.read)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.stopped)
        self.process.errorOccurred.connect(self.failed)
        self.buffer = b''
        self.terminal = False
        self.job = None
        self.offset = 0
        self.poller = QTimer(self)
        self.poller.setInterval(100)
        self.poller.timeout.connect(self.read)

    @property
    def running(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def start(self, operation, **fields):
        if self.running:
            raise ValueError('请先等待当前后台操作完成或取消')
        self.buffer = b''
        self.offset = 0
        self.terminal = False
        self.job = self.home / 'jobs' / (uuid4().hex + '.json')
        self.job.write_text(json.dumps(dict(operation=operation, parent_pid=os.getpid(), **fields), ensure_ascii=False), encoding='utf-8')
        self.poller.start()
        if getattr(sys, 'frozen', False):
            self.process.start(sys.executable, ['--worker', str(self.job)])
        else:
            self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
            self.process.start(sys.executable, ['-m', 'railnomad', '--worker', str(self.job)])

    def cancel(self):
        if self.job:
            Path(str(self.job) + '.cancel').touch()

    def read(self):
        if self.job:
            journal = Path(str(self.job) + '.events')
            if journal.exists():
                with journal.open('rb') as stream:
                    stream.seek(self.offset)
                    self.buffer += stream.read()
                    self.offset = stream.tell()
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if event.get('event') in ('done', 'error', 'cancelled'):
                    self.terminal = True
                self.event.emit(event)
            except (ValueError, UnicodeError):
                self.event.emit({'event': 'error', 'message': '后台消息格式异常，请查看日志'})

    def read_error(self):
        with (self.home / 'logs' / 'worker.log').open('ab') as log:
            log.write(bytes(self.process.readAllStandardError()))

    def failed(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.poller.stop()
            self.terminal = True
            self.event.emit({'event': 'error', 'message': '后台进程无法启动：' + self.process.errorString()})
            self.finished.emit()

    def stopped(self, code, status):
        self.read()
        self.poller.stop()
        if not self.terminal:
            self.event.emit({'event': 'error', 'message': f'后台进程意外退出（{code}），已保存的任务可重试。'})
        self.finished.emit()
