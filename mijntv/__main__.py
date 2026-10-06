import argparse
import getpass
import os
import signal
from pathlib import Path
import threading
import time

from .server import AdminServer, credentials
from .store import Store


def refresh_loop(store, stop):
    recovered = store.cleanup_imports()
    if recovered:
        print(f'Opgeruimde afgebroken imports: {recovered}', flush=True)
    while not stop.is_set():
        source = store.setting('source')
        try:
            age = time.time() - float(store.setting('refreshed_at', '0'))
        except ValueError:
            age = 6 * 60 * 60
        if source and age >= 6 * 60 * 60:
            try:
                store.refresh_source()
            except Exception:
                # Leave working catalog untouched; never log provider credentials.
                print('Automatisch vernieuwen mislukt; de bestaande zenderlijst blijft beschikbaar.', flush=True)
        if stop.wait(6 * 60 * 60):
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
