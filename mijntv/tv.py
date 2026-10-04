"""Large-button TV interface for an X11 Linux session."""
import math
import queue
import socket
import time
import tkinter as tk

from .player import Player

BG, TILE, WHITE, MUTED, AMBER = '#0c1722', '#1c2c3a', '#f5f7fa', '#b6c7d4', '#ffda83'


class TV:
    def __init__(self, store, port, bootstrap=None, fullscreen=True):
        self.store, self.port, self.bootstrap = store, port, bootstrap
        self.root = tk.Tk()
        self.root.title('TPTV thuis')
        self.root.configure(bg=BG)
        if fullscreen:
            self.root.configure(cursor='none')
        self.root.geometry(f'{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}' if fullscreen else '1280x720')
        self.root.attributes('-fullscreen', fullscreen)
        self.root.update_idletasks()
        self.scale = max(.65, self.root.winfo_height() / 900)
        self.channels, self.selected, self.page = [], 0, 0
        self.watching, self.player, self.pending_zap = False, None, None
        self.message_until = 0
        self.video = tk.Frame(self.root, bg='black')
        self.video.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.video.update_idletasks()
        self.home = tk.Frame(self.root, bg=BG)
        self.home.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.home.lift()
        header = tk.Frame(self.home, bg=BG)
        header.pack(fill='x', padx=60, pady=(35, 20))
        self.label(header, '▣  TPTV thuis', 25, bold=True).pack(side='left')
        self.clock = self.label(header, '', 23, color=MUTED)
        self.clock.pack(side='right')
        self.label(self.home, 'Kies een zender', 42, bold=True).pack(anchor='w', padx=60)
        self.subtitle = self.label(self.home, 'Selecteer een zender en druk op OK.', 21, color=MUTED)
        self.subtitle.pack(anchor='w', padx=60, pady=(10, 25))
        self.tiles = tk.Frame(self.home, bg=BG)
        self.tiles.pack(fill='both', expand=True, padx=50)
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
        self.banner = tk.Frame(self.root, bg=BG)
        self.banner_label = self.label(self.banner, '', 24, bold=True)
        self.banner_label.pack(side='left', padx=30, pady=20)
        self.small_button(self.banner, '← Zenderlijst', self.go_home).pack(side='right', padx=30, pady=14)
        self.error = self.label(self.root, '', 25, color=AMBER)
        self.root.bind('<Key>', self.key)
        self.root.bind('<F11>', self.toggle_fullscreen)
        self.root.bind('<Control-q>', lambda e: self.root.destroy())
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)
        self.refresh()
        self.tick()
        self.root.after(100, self.poll_player)
        self.root.focus_force()

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
        self.root.after(5000, self.refresh)

    def render(self):
        for i,b in enumerate(self.buttons):
            index = self.page*10+i
            if index < len(self.channels):
                channel = self.channels[index]
                b.configure(text=f'{index+1:02d}\n\n{channel["label"]}' + ('' if channel['available'] else '\nNiet beschikbaar'),
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
        self.error.place_forget()
        self.home.place_forget()
        self.banner.lift()
        self.banner_label.configure(text=f'{index+1} · {channel["label"]}   —   Verbinden…')
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
                    if self.channels:
                        self.banner_label.configure(text=f'{self.selected+1} · {self.channels[self.selected]["label"]}   —   Pijltjes: zappen')
                    self.show_banner()
                elif (event['event']=='end-file' and event.get('reason') in ('error','eof')) or event['event']=='disconnected':
                    self.loading = False
                    self.show_error('Geen uitzending beschikbaar. Kies een andere zender of druk op Terug.')
        if self.watching and getattr(self,'loading',False) and time.monotonic()-self.loading_started>25:
            self.loading = False
            self.show_error('Verbinden duurt te lang. Kies een andere zender of druk op Terug.')
        if self.watching and time.monotonic()>self.message_until:
            self.banner.place_forget()
        self.root.after(100, self.poll_player)

    def tick(self):
        self.clock.configure(text=time.strftime('%H:%M'))
        self.root.after(1000, self.tick)

    def key(self, event):
        key = event.keysym
        if key in ('Escape','BackSpace','XF86Back','XF86Stop'):
            self.go_home()
        elif self.watching:
            if key in ('Right','Up','Next','XF86AudioNext'):
                self.zap(1)
            elif key in ('Left','Down','Prior','XF86AudioPrev'):
                self.zap(-1)
            elif key in ('Return','KP_Enter','space'):
                self.show_banner()
        elif self.channels:
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
            if self.player:
                self.player.close()
