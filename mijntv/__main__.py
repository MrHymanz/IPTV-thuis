import argparse
import logging
import getpass
import os
import signal
from pathlib import Path
import threading
import time

from .server import AdminServer, credentials
from .maintenance import BACKGROUND_STARTUP_DELAY
from .store import Store
from .diagnostics import configure_logging


PLAYLIST_REFRESH_SECONDS = 6 * 60 * 60
PLAYLIST_RETRY_SECONDS = 15 * 60


def refresh_loop(store, stop):
    if stop.wait(BACKGROUND_STARTUP_DELAY):
        return
    try:
        store.optimize_catalog()
    except Exception:
        logging.getLogger('mijntv.stream').info('catalog_maintenance_failed')
    recovered = store.cleanup_imports()
    if recovered:
        print(f'Opgeruimde afgebroken imports: {recovered}', flush=True)
    retry_at, previous_source = 0, None
    while not stop.is_set():
        source = store.setting('source')
        if source != previous_source:
            retry_at, previous_source = 0, source
        try:
            age = time.time() - float(store.setting('refreshed_at', '0'))
        except ValueError:
            age = PLAYLIST_REFRESH_SECONDS
        if source and age >= PLAYLIST_REFRESH_SECONDS and time.monotonic() >= retry_at:
            try:
                logging.getLogger('mijntv.stream').info('playlist_refresh_start')
                count = store.refresh_source()
                retry_at = 0
                print(f'Zenderlijst automatisch vernieuwd: {count} zenders.', flush=True)
                logging.getLogger('mijntv.stream').info('playlist_refresh_complete count=%s', count)
            except Exception:
                # Leave working catalog untouched; never log provider credentials.
                logging.getLogger('mijntv.stream').info('playlist_refresh_failed')
                retry_at = time.monotonic() + PLAYLIST_RETRY_SECONDS
                print('Automatisch vernieuwen mislukt; de bestaande zenderlijst blijft beschikbaar. Nieuwe poging over 15 minuten.', flush=True)
        if stop.wait(60):
            return


def main():
    parser = argparse.ArgumentParser(description='IPTV thuis — eenvoudige televisie met webbeheer')
    parser.add_argument('--data-dir', default=str(Path.home()/'.local/share/iptv-thuis'))
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--admin-only', action='store_true', help='Alleen het webbeheer starten, zonder tv-scherm')
    parser.add_argument('--windowed', action='store_true')
    parser.add_argument('--reset-password', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.data_dir)
    configure_logging(args.data_dir)
    password = None
    if args.reset_password:
        password = getpass.getpass('Nieuw beheerwachtwoord (minimaal 12 tekens): ')
        if password != getpass.getpass('Herhaal wachtwoord: '):
            parser.error('De wachtwoorden verschillen.')
    try:
        auth, generated = credentials(args.data_dir, password)
        if generated:
            # Keep first-run access recoverable after an unattended restart.
            Path(args.data_dir, 'first-login.txt').write_text(generated)
        bootstrap_path = Path(args.data_dir, 'first-login.txt')
        bootstrap = bootstrap_path.read_text().strip() if bootstrap_path.exists() else None
        server = AdminServer((args.host, args.port), store, auth, Path(__file__).parent/'web')
    except (ValueError, OSError) as error:
        parser.error(str(error))
    server.display.native = not args.admin_only
    tv = None
    if not args.admin_only:
        try:
            from .tv import TV
            server.display.apply()
            tv = TV(store, args.port, bootstrap, fullscreen=not args.windowed, recordings=server.recordings)
            # Complete the initial Tk mapping before starting background workers.
            tv.root.update()
        except ImportError:
            parser.error('Installeer python3-tk voor het tv-scherm, of gebruik --admin-only.')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=refresh_loop, args=(store, stop), daemon=True).start()
    threading.Thread(target=server.epg.run, args=(stop,), daemon=True).start()
    recorder_thread = threading.Thread(target=server.recordings.run, args=(stop,), daemon=True)
    recorder_thread.start()
    print(f'Webbeheer: http://<IP-adres-van-deze-pc>:{args.port}/admin — gebruikersnaam admin', flush=True)
    if bootstrap and not store.favorites():
        print(f'Eerste beheerwachtwoord: {bootstrap}', flush=True)
    try:
        if args.admin_only:
            while not stop.wait(1):
                pass
        else:
            while not stop.is_set():
                tv.run()
                if not tv.restart_requested:
                    break
                server.display.apply()
                tv = TV(store, args.port, bootstrap, fullscreen=not args.windowed, recordings=server.recordings)
                tv.root.update()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        recorder_thread.join(timeout=12)
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
