import argparse
import logging
import sys
import os

from .paths import data_home


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--smoke-run")
    parser.add_argument("--smoke-result")
    parser.add_argument('--smoke-page',choices=['planner','tasks','favorites','data','help'])
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if (args.smoke_run or args.smoke_result or args.smoke_page) and not args.smoke:
        parser.error('界面验收参数须与 --smoke 一起使用')
    if args.offline:
        os.environ['RAILNOMAD_OFFLINE'] = '1'
    if os.environ.get('RAILNOMAD_OFFLINE') == '1':
        import socket
        def unavailable(*a, **kw):
            raise OSError('当前处于离线模式；本地搜索和浏览不受影响')
        socket.socket.connect = unavailable
        socket.create_connection = unavailable
        socket.getaddrinfo = unavailable
    home = data_home()
    from .public_data import install_public_snapshot
    install_public_snapshot(home)
    logging.basicConfig(filename=home / "logs" / "railnomad.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    if args.worker:
        from .worker import execute
        return execute(args.worker, home)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from .ui.main import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("RailNomad")
    app.setOrganizationName("RailNomad")
    window = MainWindow(home)
    window.suppress_fare_sync=args.smoke
    window.show()
    if args.smoke:
        if args.smoke_page:window.nav.setCurrentRow(['planner','tasks','favorites','data','help'].index(args.smoke_page))
        if args.smoke_run:
            window.results.load(args.smoke_run);window.pages.setCurrentWidget(window.results)
        def capture():
            if args.smoke_page=='help':
                from .ui.widgets import Disclosure
                for section in window.pages.currentWidget().findChildren(Disclosure):
                    if '版权' in section.toggle.text():section.set_expanded(True)
                app.processEvents()
            window.grab().save(str(home / "desktop-smoke.png"))
            if args.smoke_result:
                import json
                from PySide6.QtWidgets import QDialog,QVBoxLayout
                row=window.store.rows('SELECT payload FROM results WHERE id=?',(args.smoke_result,))[0]
                payload=json.loads(row['payload'])
                if payload['run_id']!=args.smoke_run:raise ValueError('验收方案须属于验收任务')
                dialog=QDialog(window);dialog.resize(1120,860);layout=QVBoxLayout(dialog)
                view=window.results.itinerary(payload);layout.addWidget(view);dialog.show();app.processEvents()
                dialog.grab().save(str(home/'itinerary-smoke.png'))
                view.verticalScrollBar().setValue(view.verticalScrollBar().maximum());app.processEvents()
                dialog.grab().save(str(home/'fares-smoke.png'));dialog.close()
        QTimer.singleShot(1000,capture)
        QTimer.singleShot(1800,app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
