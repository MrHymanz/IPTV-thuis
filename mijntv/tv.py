"""Large-button TV interface for an X11 Linux session."""
import math
import io
import threading
from pathlib import Path
import queue
import socket
import time
import tkinter as tk

from .player import Player
from .logos import LogoCache
from .display import Display

BG, TILE, WHITE, MUTED, AMBER = '#0c1722', '#1c2c3a', '#f5f7fa', '#b6c7d4', '#ffda83'


class TV:
    def __init__(self, store, port, bootstrap=None, fullscreen=True, recordings=None):
        self.store, self.port, self.bootstrap = store, port, bootstrap
        self.recordings = recordings
        self.panel_mode = None
        self.panel_selected = 0
        self.header_selected = None
        self.playing_recording = None
        self.display_settings = Display(store).settings()
        self.restart_requested = False
        self.volume = max(0, min(100, int(store.setting('volume', '100'))))
        self.muted = False
        self.volume_timer = None
        self.root = tk.Tk()
        self.root.title('IPTV thuis')
        self.root.configure(bg=BG)
        mode = Display(store, native=True).query() if fullscreen else None
        dimensions = mode['current'].split('x') if mode and mode['current'] else None
        self.screen_width = int(dimensions[0]) if dimensions else self.root.winfo_screenwidth()
        self.screen_height = int(dimensions[1]) if dimensions else self.root.winfo_screenheight()
        self.root.tk.call('tk', 'scaling', 96/72)
        if fullscreen:
            self.root.configure(cursor='none')
        self.root.geometry(f'{self.screen_width}x{self.screen_height}' if fullscreen else '1280x720')
        self.root.attributes('-fullscreen', fullscreen)
        self.root.update_idletasks()
        screen_height = self.screen_height if fullscreen else self.root.winfo_height()
        self.scale = max(.65, screen_height * (1-self.display_settings['margin']/50) / 900)
        self.logo_images, self.logo_pending, self.logo_retry = {}, set(), {}
        self.logo_requests, self.logo_results = queue.Queue(), queue.Queue()
        self.logo_cache = LogoCache(Path(store.path).parent)
        threading.Thread(target=self.load_logos, daemon=True).start()
        self.channels, self.selected, self.page = [], 0, 0
        self.watching, self.player, self.pending_zap = False, None, None
        self.message_until = 0
        self.content = tk.Frame(self.root, bg=BG)
        margin = self.display_settings['margin'] / 100
        self.content.place(relx=margin, rely=margin, relwidth=1-2*margin, relheight=1-2*margin)
        self.video = tk.Frame(self.content, bg='black')
        self.video.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.video.update_idletasks()
        self.home = tk.Frame(self.content, bg=BG)
        self.home.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.home.lift()
        header = tk.Frame(self.home, bg=BG)
        header.pack(fill='x', padx=60, pady=(35, 20))
        self.icon(header, 'tv', 50).pack(side='left', padx=(0, 15))
        self.label(header, 'IPTV thuis', 25, bold=True).pack(side='left')
        self.clock = self.label(header, '', 23, color=MUTED)
        self.clock.pack(side='right')
        storage = tk.Frame(header, bg=BG)
        storage.pack(side='right', padx=(0, 35))
        self.icon(storage, 'disk', 36).pack(side='left', padx=(0, 10))
        self.storage_label = self.label(storage, '', 17, color=MUTED)
        self.storage_label.pack(side='left')
        self.home_actions = tk.Frame(self.home, bg=BG)
        self.home_actions.pack(anchor='e', padx=60)
        self.library_button = self.small_button(self.home_actions, 'Opnames', lambda: self.open_panel('recordings'))
        self.library_button.pack(side='left', padx=8)
        self.guide_button = self.small_button(self.home_actions, 'Programmagids / opnemen', lambda: self.open_panel('guide'))
        self.guide_button.pack(side='left', padx=8)
        self.label(self.home, 'Kies een zender', 42, bold=True).pack(anchor='w', padx=60)
        self.subtitle = self.label(self.home, 'Selecteer een zender en druk op OK.', 17, color=MUTED)
        self.subtitle.pack(anchor='w', padx=60, pady=(10, 25))
        self.tiles = tk.Frame(self.home, bg=BG)
        self.tiles.pack(fill='both', expand=True, padx=50)
        self.tiles.grid_propagate(False)
        for col in range(5):
            self.tiles.columnconfigure(col, weight=1, uniform='channels')
        for row in range(2):
            self.tiles.rowconfigure(row, weight=1, uniform='rows')
        self.buttons = []
        for i in range(10):
            b = tk.Button(self.tiles, bg=TILE, fg=WHITE, activebackground='#304a5a', activeforeground=WHITE,
                          font=('DejaVu Sans', int(24*self.scale), 'bold'), wraplength=int(210*self.scale),
                          bd=0, highlightthickness=4, highlightbackground=TILE, highlightcolor=AMBER,
                          takefocus=False, command=lambda i=i: self.choose(self.page*10+i))
            b.grid(row=i//5, column=i%5, sticky='nsew', padx=10, pady=10)
            self.buttons.append(b)
        paging = tk.Frame(self.home, bg=BG)
        paging.pack(pady=20)
        self.previous = self.small_button(paging, '← Vorige', lambda: self.change_page(-1))
        self.previous.pack(side='left', padx=20)
        self.page_label = self.label(paging, '', 20, color=MUTED)
        self.page_label.pack(side='left', padx=20)
        self.next = self.small_button(paging, 'Volgende →', lambda: self.change_page(1))
        self.next.pack(side='left', padx=20)
        self.label(self.home, 'Pijltjes: kiezen    •    OK: kijken    •    Tijdens kijken: pijltjes of CH+/CH− om te zappen', 17, color=MUTED).pack(pady=(0, 25))
        self.setup = self.label(self.home, '', 20, color=AMBER)
        self.banner = tk.Frame(self.content, bg=BG)
        banner_text = tk.Frame(self.banner, bg=BG)
        banner_text.pack(side='left', padx=30, pady=14)
        self.banner_label = self.label(banner_text, '', 24, bold=True)
        self.banner_label.pack(anchor='w')
        self.banner_programme = self.label(banner_text, '', 17, color=MUTED)
        self.banner_programme.pack(anchor='w', pady=(7, 0))
        self.small_button(self.banner, '← Zenderlijst', self.go_home).pack(side='right', padx=30, pady=14)
        self.error = self.label(self.content, '', 25, color=AMBER)
        self.volume_label = self.label(self.content, '', 25, bold=True)
        self.panel = tk.Frame(self.content, bg=BG)
        self.panel_title = self.label(self.panel, '', 32, bold=True)
        self.panel_title.pack(anchor='w', padx=60, pady=(30, 10))
        self.panel_hint = self.label(self.panel, '', 17, color=MUTED)
        self.panel_hint.pack(anchor='w', padx=60, pady=(0, 15))
        self.panel_rows = tk.Frame(self.panel, bg=BG)
        self.panel_rows.pack(fill='both', expand=True, padx=60)
        self.small_button(self.panel, '← Terug naar zenders', self.go_home).pack(pady=20)
        self.small_button(self.banner, '● Opnemen', self.record_current).pack(side='right', padx=10, pady=14)
        self.root.bind('<Key>', self.key)
        self.root.bind('<F11>', self.toggle_fullscreen)
        self.root.bind('<Control-q>', lambda e: self.root.destroy())
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)
        self.refresh()
        self.tick()
        self.update_storage()
        self.root.after(100, self.poll_logos)
        self.root.after(100, self.poll_player)
        self.root.after(200, self.present)
        self.root.after(2000, self.check_display)

    def check_display(self):
        if Display(self.store).settings() != self.display_settings:
            self.restart_requested = True
            if self.player:
                self.player.close()
            self.root.destroy()
            return
        self.root.after(2000, self.check_display)

    def present(self):
        # Map and focus after the window manager has processed the first layout.
        self.root.deiconify()
        self.root.lift()
        self.root.update_idletasks()
        self.root.focus_force()

    def icon(self, parent, kind, size):
        scale = size * self.scale / 50
        canvas = tk.Canvas(parent, width=50*scale, height=45*scale, bg=BG,
                           highlightthickness=0)
        def line(*points):
            canvas.create_line(*[point*scale for point in points], fill=WHITE,
                               width=max(2, 2.5*scale), capstyle='round', joinstyle='round')
        if kind == 'tv':
            canvas.create_rectangle(4*scale, 11*scale, 46*scale, 35*scale,
                                    outline=WHITE, width=max(2, 2.5*scale))
            line(15, 2, 25, 11, 35, 2)
            line(25, 35, 25, 42)
            line(16, 42, 34, 42)
        else:
            canvas.create_rectangle(5*scale, 8*scale, 45*scale, 38*scale,
                                    outline=MUTED, width=max(2, 2.5*scale))
            canvas.create_oval(12*scale, 13*scale, 38*scale, 27*scale,
                               outline=MUTED, width=max(2, 2*scale))
            line(10, 32, 29, 32)
            canvas.create_oval(35*scale, 30*scale, 39*scale, 34*scale,
                               outline='', fill=AMBER)
        return canvas

    def update_storage(self):
        status = self.recordings.storage() if self.recordings else {}
        text = f'{status["free"] / 1_000_000_000:.0f} GB vrij' if status.get('available') else 'Opslag niet bereikbaar'
        self.storage_label.configure(text=text)
        self.root.after(30000, self.update_storage)

    def load_logos(self):
        try:
            from PIL import Image, ImageDraw, ImageOps
        except ImportError:
            return  # The text-only interface remains usable without Pillow.
        width, height = int(180*self.scale), int(85*self.scale)
        while True:
            url = self.logo_requests.get()
            if url is None:
                return
            try:
                data, _ = self.logo_cache.get(url)
                with Image.open(io.BytesIO(data)) as source:
                    if source.width > 4096 or source.height > 4096:
                        raise ValueError('Logo te groot.')
                    logo = ImageOps.contain(source.convert('RGBA'), (width-24, height-20))
                badge = Image.new('RGBA', (width, height))
                ImageDraw.Draw(badge).rounded_rectangle((0, 0, width-1, height-1),
                                                        radius=12, fill='white')
                badge.alpha_composite(logo, ((width-logo.width)//2, (height-logo.height)//2))
                self.logo_results.put((url, badge))
            except Exception:
                self.logo_results.put((url, None))

    def poll_logos(self):
        changed = False
        while True:
            try:
                url, badge = self.logo_results.get_nowait()
            except queue.Empty:
                break
            self.logo_pending.discard(url)
            if badge is None:
                self.logo_retry[url] = time.monotonic() + 300
            else:
                from PIL import ImageTk
                self.logo_images[url] = ImageTk.PhotoImage(badge, master=self.root)
                changed = True
        if changed and not self.watching:
            self.render()
        self.root.after(100, self.poll_logos)

    def label(self, parent, text, size, color=WHITE, bold=False):
        return tk.Label(parent, text=text, bg=BG, fg=color, font=('DejaVu Sans', int(size*self.scale), 'bold' if bold else 'normal'), justify='left')

    def small_button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, font=('DejaVu Sans', int(17*self.scale)),
                         bg=TILE, fg=WHITE, activebackground='#304a5a', activeforeground=WHITE,
                         bd=0, padx=18, pady=10, takefocus=False)

    def refresh(self):
        old_id = self.channels[self.selected]['id'] if self.channels and self.selected < len(self.channels) else None
        self.channels = self.store.favorites(playback=True)
        self.selected = next((i for i,c in enumerate(self.channels) if c['id']==old_id), min(self.selected, max(0,len(self.channels)-1)))
        self.page = self.selected // 10
        if not self.watching:
            self.render()
        if self.watching and not self.playing_recording:
            self.update_programme()
        if self.panel_mode == 'recordings':
            old_recording = self.panel_items[self.panel_selected]['id'] if self.panel_items else None
            self.panel_items = self.recordings.list()
            self.panel_selected = next((i for i, item in enumerate(self.panel_items) if item['id'] == old_recording),
                                       min(self.panel_selected, max(0, len(self.panel_items)-1)))
            self.render_panel()
        self.root.after(5000, self.refresh)

    def programme_text(self):
        if not self.channels:
            return 'Selecteer een zender en druk op OK.'
        guide = self.channels[self.selected].get('epg') or {}
        lines = []
        for key, label in [('now', 'Nu'), ('next', 'Straks')]:
            programme = guide.get(key)
            if programme:
                start = time.strftime('%H:%M', time.localtime(programme['start']))
                end = time.strftime('%H:%M', time.localtime(programme['end']))
                title = programme['title']
                title = title[:97] + '…' if len(title) > 100 else title
                lines.append(f'{label}: {start}–{end}  {title}')
            elif key == 'now':
                lines.append('Nu: geen programmagegevens beschikbaar')
        return '\n'.join(lines)

    def update_programme(self):
        text = self.programme_text()
        width = max(200, int(self.screen_width * (1-self.display_settings['margin']/50))-120)
        self.subtitle.configure(text=text, wraplength=width)
        self.banner_programme.configure(text=text, wraplength=max(200, width-350))

    def render(self):
        self.update_programme()
        for i,b in enumerate(self.buttons):
            index = self.page*10+i
            if index < len(self.channels):
                channel = self.channels[index]
                url = channel.get('logo') or ''
                image = self.logo_images.get(url)
                if (url and image is None and url not in self.logo_pending and
                        time.monotonic() >= self.logo_retry.get(url, 0)):
                    self.logo_pending.add(url)
                    self.logo_requests.put(url)
                b.configure(text=f'{index+1:02d} · {channel["label"]}' + ('' if channel['available'] else '\nNiet beschikbaar'),
                            image=image or '', compound='top', padx=8, pady=10,
                            wraplength=max(80, int(((self.content.winfo_width() if self.content.winfo_width() > 1
                                                  else self.screen_width * (1-self.display_settings['margin']/50))-100)/5-46)),
                            state='normal', highlightbackground=AMBER if index==self.selected else TILE,
                            bg='#304a5a' if index==self.selected else TILE)
                b.grid()
            else:
                b.grid_remove()
        pages = max(1, math.ceil(len(self.channels)/10))
        self.page_label.configure(text=f'Pagina {self.page+1} van {pages}')
        self.previous.configure(state='normal' if self.page else 'disabled')
        self.next.configure(state='normal' if self.page+1<pages else 'disabled')
        if not self.channels:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                    connection.connect(('192.0.2.1', 80))
                    address = connection.getsockname()[0]
            except OSError:
                address = '<IP-adres van deze pc>'
            text = f'Stel je zenders in via http://{address}:{self.port}/admin\nGebruikersnaam: admin'
            if self.bootstrap:
                text += f'\nEerste beheerwachtwoord: {self.bootstrap}'
            else:
                text += '\nGebruik je ingestelde beheerwachtwoord.'
            self.setup.configure(text=text)
            self.setup.pack(before=self.tiles, anchor='w', padx=60, pady=15)
        else:
            self.setup.pack_forget()

    def render_header(self):
        for index, button in enumerate((self.library_button, self.guide_button)):
            button.configure(bg='#304a5a' if self.header_selected == index else TILE,
                             fg=AMBER if self.header_selected == index else WHITE)

    def record_current(self):
        if not self.recordings or not self.channels or self.playing_recording:
            return
        try:
            self.recordings.schedule(self.channels[self.selected]['id'])
            self.show_error('Opname ingepland. Bekijk de status bij Opnames.')
            self.root.after(3000, self.error.place_forget)
        except (ValueError, OSError) as error:
            self.show_error(str(error) if isinstance(error, ValueError) else 'Opslag niet bereikbaar.')

    def open_panel(self, mode):
        if not self.recordings:
            return
        if mode == 'guide' and not self.channels:
            self.show_error('Voeg eerst zenders toe via de beheerpagina.')
            return
        self.go_home()
        self.header_selected = None
        self.render_header()
        self.panel_mode, self.panel_selected = mode, 0
        try:
            if mode == 'guide':
                if not self.channels:
                    return
                self.panel_channel = self.channels[self.selected]['id']
                guide = self.recordings.guide(self.panel_channel)
                self.panel_items = guide['programmes']
                heading = 'Programmagids · ' + guide['channel']
                hint = 'Pijltjes: programma kiezen · OK: opname plannen · Terug: zenders'
            else:
                self.panel_items = self.recordings.list()
                heading = 'Mijn opnames'
                hint = 'Pijltjes: kiezen · OK: afspelen · Terug: zenders. Beheer opnames via de beheerpagina.'
            self.panel_title.configure(text=heading)
            self.panel_hint.configure(text=hint)
            self.panel.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.panel.lift()
            self.render_panel()
        except ValueError as error:
            self.show_error(str(error))

    def render_panel(self):
        for widget in self.panel_rows.winfo_children():
            widget.destroy()
        if not self.panel_items:
            self.label(self.panel_rows, 'Nog geen opnames.' if self.panel_mode == 'recordings' else 'Geen programmagids beschikbaar voor deze zender.', 22).pack(pady=30)
        page = self.panel_selected // 6
        for index in range(page*6, min(len(self.panel_items), page*6+6)):
            item = self.panel_items[index]
            stamp = time.strftime('%d-%m %H:%M', time.localtime(item['start']))
            subtitle = item['channel'] + ' · ' + item['status'] if self.panel_mode == 'recordings' else time.strftime('Tot %H:%M', time.localtime(item['end']))
            button = self.small_button(self.panel_rows, stamp + ' · ' + item['title'] + '\n' + subtitle,
                                       lambda index=index: self.panel_choose(index))
            button.configure(anchor='w', justify='left', wraplength=max(300, self.content.winfo_width()-180),
                             bg='#304a5a' if index == self.panel_selected else TILE,
                             fg=AMBER if index == self.panel_selected else WHITE)
            button.pack(fill='x', pady=5)

    def panel_choose(self, index=None):
        if index is not None:
            self.panel_selected = index
        if not self.panel_items:
            return
        item = self.panel_items[self.panel_selected]
        try:
            if self.panel_mode == 'guide':
                self.recordings.schedule(self.panel_channel, item['start'])
                self.show_error('Opname ingepland. Bekijk de status bij Opnames.')
                self.root.after(3000, self.error.place_forget)
            elif item['playable']:
                path = self.recordings.playback(item['id'])
                self.panel.place_forget()
                self.panel_mode = None
                self.home.place_forget()
                self.watching = True
                self.playing_recording = item['id']
                self.banner_label.configure(text=item['title'])
                self.banner_programme.configure(text='OK: pauze · Links/rechts: 30 seconden terug/vooruit · Terug: opnames')
                self.show_banner(60)
                self.start_stream({'url': str(path)})
            else:
                self.show_error(item['error'] or 'Deze opname is nog niet beschikbaar om af te spelen.')
        except (ValueError, OSError) as error:
            self.show_error(str(error) if isinstance(error, ValueError) else 'Opname niet bereikbaar.')

    def change_page(self, delta):
        if not self.channels:
            return
        page = max(0, min(math.ceil(len(self.channels)/10)-1, self.page+delta))
        self.selected = min(page*10+self.selected%10, len(self.channels)-1)
        self.page = page
        self.render()

    def choose(self, index):
        if index >= len(self.channels):
            return
        channel = self.channels[index]
        if not channel['available']:
            self.show_error('Deze zender is niet beschikbaar. Kies een andere zender.')
            return
        self.selected, self.page = index, index//10
        self.watching = True
        self.playing_recording = None
        self.error.place_forget()
        self.home.place_forget()
        self.banner.lift()
        self.banner_label.configure(text=f'{index+1} · {channel["label"]}   —   Verbinden…')
        self.update_programme()
        self.show_banner(60)
        if self.pending_zap:
            self.root.after_cancel(self.pending_zap)
        self.pending_zap = self.root.after(200, lambda: self.start_stream(channel))

    def start_stream(self, channel):
        self.pending_zap = None
        try:
            if self.player and self.player.process.poll() is not None:
                self.player.close()
                self.player = None
            if not self.player:
                self.player = Player(self.video.winfo_id())
                self.player.command('set_property', 'volume', self.volume)
                self.player.command('set_property', 'mute', self.muted)
            self.player.play(channel['url'])
            self.root.focus_force()
            self.loading_started = time.monotonic()
            self.loading = True
        except (RuntimeError, OSError):
            self.show_error('De zender kon niet starten. Kies een andere zender of druk op Terug.')

    def zap(self, delta):
        available = [i for i,c in enumerate(self.channels) if c['available']]
        if not available:
            self.go_home()
            return
        if self.selected in available:
            i = (available.index(self.selected)+delta) % len(available)
        else:
            i = 0
        self.choose(available[i])

    def go_home(self):
        if self.pending_zap:
            self.root.after_cancel(self.pending_zap)
            self.pending_zap = None
        self.watching = False
        self.playing_recording = None
        self.panel_mode = None
        self.header_selected = None
        self.render_header()
        self.panel.place_forget()
        self.loading = False
        if self.player:
            try:
                self.player.stop()
            except RuntimeError:
                pass
        self.banner.place_forget()
        self.error.place_forget()
        self.home.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.home.lift()
        self.render()

    def show_banner(self, seconds=4):
        self.banner.place(relx=0, rely=1, anchor='sw', relwidth=1)
        self.banner.lift()
        self.message_until = time.monotonic()+seconds

    def show_error(self, text):
        self.error.configure(text=text, wraplength=self.root.winfo_width()-100)
        self.error.place(relx=.5, rely=.5, anchor='center')
        self.error.lift()

    def poll_player(self):
        if self.player:
            while True:
                try:
                    event = self.player.events.get_nowait()
                except queue.Empty:
                    break
                if not self.watching or self.pending_zap:
                    continue
                if event['event']=='file-loaded':
                    self.loading = False
                    self.root.focus_force()
                    self.error.place_forget()
                    if self.playing_recording:
                        self.banner_label.configure(text='Opname afspelen — Terug: opnames')
                    elif self.channels:
                        self.banner_label.configure(text=f'{self.selected+1} · {self.channels[self.selected]["label"]}   —   Pijltjes: zappen')
                    self.show_banner()
                elif (event['event']=='end-file' and event.get('reason') in ('error','eof')) or event['event']=='disconnected':
                    self.loading = False
                    if self.playing_recording:
                        self.open_panel('recordings')
                        continue
                    self.show_error('Geen uitzending beschikbaar. Kies een andere zender of druk op Terug.')
        if self.watching and getattr(self,'loading',False) and time.monotonic()-self.loading_started>50:
            self.loading = False
            self.show_error('Verbinden duurt te lang. Kies een andere zender of druk op Terug.')
        if self.watching and time.monotonic()>self.message_until:
            self.banner.place_forget()
        self.root.after(100, self.poll_player)

    def tick(self):
        self.clock.configure(text=time.strftime('%H:%M'))
        self.root.after(1000, self.tick)

    def change_volume(self, delta=None):
        if delta is None:
            self.muted = not self.muted
        else:
            self.volume = max(0, min(100, self.volume + delta))
            self.muted = False
            self.store.set_setting('volume', str(self.volume))
        if self.player:
            try:
                self.player.command('set_property', 'volume', self.volume)
                self.player.command('set_property', 'mute', self.muted)
            except RuntimeError:
                pass
        self.volume_label.configure(text='Geluid uit' if self.muted else f'Volume {self.volume}%')
        self.volume_label.place(relx=.5, rely=.06, anchor='n')
        self.volume_label.lift()
        if self.volume_timer:
            self.root.after_cancel(self.volume_timer)
        self.volume_timer = self.root.after(2000, self.hide_volume)

    def hide_volume(self):
        self.volume_timer = None
        self.volume_label.place_forget()

    def key(self, event):
        key = event.keysym
        if key in ('XF86AudioRaiseVolume', 'XF86AudioLowerVolume', 'XF86AudioMute'):
            self.change_volume({'XF86AudioRaiseVolume':5, 'XF86AudioLowerVolume':-5}.get(key))
            return 'break'
        if key in ('XF86Record', 'r', 'R'):
            self.record_current()
            return 'break'
        if key in ('Menu', 'XF86MenuKB', 'F2', 'o', 'O'):
            self.open_panel('recordings')
            return 'break'
        if key in ('Escape','BackSpace','XF86Back','XF86Stop') and getattr(self, 'playing_recording', None):
            self.open_panel('recordings')
            return 'break'
        if key in ('Escape','BackSpace','XF86Back','XF86Stop'):
            self.go_home()
        elif getattr(self, 'panel_mode', None):
            if key in ('Return', 'KP_Enter', 'space'):
                self.panel_choose()
            elif key in ('Up', 'Down', 'Prior', 'Next'):
                delta = {'Up':-1, 'Down':1, 'Prior':-6, 'Next':6}[key]
                self.panel_selected = max(0, min(len(self.panel_items)-1, self.panel_selected+delta))
                self.render_panel()
        elif self.watching:
            if getattr(self, 'playing_recording', None):
                if key in ('Left', 'Right') and self.player:
                    self.player.command('seek', -30 if key == 'Left' else 30, 'relative')
                elif key in ('Return', 'KP_Enter', 'space') and self.player:
                    self.player.command('cycle', 'pause')
                return 'break'
            if key in ('Right','Up','Prior','XF86AudioPrev'):
                self.zap(1)
            elif key in ('Left','Down','Next','XF86AudioNext'):
                self.zap(-1)
            elif key in ('Return','KP_Enter','space'):
                self.show_banner()
        elif getattr(self, 'header_selected', None) is not None:
            if key in ('Return', 'KP_Enter', 'space'):
                self.open_panel('recordings' if self.header_selected == 0 else 'guide')
            elif key in ('Left', 'Right'):
                self.header_selected = 0 if key == 'Left' else 1
                self.render_header()
            elif key == 'Down':
                self.header_selected = None
                self.render_header()
                self.render()
        elif self.channels:
            if key == 'Up' and self.selected % 10 < 5:
                self.header_selected = 0
                self.render_header()
                return 'break'
            if key in ('Return','KP_Enter','space'):
                self.choose(self.selected)
            elif key in ('Next','Prior'):
                self.change_page(1 if key=='Next' else -1)
            elif key in ('Left','Right','Up','Down'):
                delta = {'Left':-1,'Right':1,'Up':-5,'Down':5}[key]
                self.selected = max(0,min(len(self.channels)-1,self.selected+delta))
                self.page = self.selected//10
                self.render()
        return 'break' if key in ('Left','Right','Up','Down','Next','Prior','Return','KP_Enter','space','Escape','BackSpace') else None

    def toggle_fullscreen(self, event=None):
        self.root.attributes('-fullscreen', not self.root.attributes('-fullscreen'))
        return 'break'

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.logo_requests.put(None)
            if self.player:
                self.player.close()
