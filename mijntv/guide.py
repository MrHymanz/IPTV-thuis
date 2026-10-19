"""Remote-first native programme timeline; no network work runs in this widget."""
import math
import time
import tkinter as tk
from datetime import datetime
from zoneinfo import ZoneInfo

BG, TILE, WHITE, MUTED, AMBER, GREEN = '#0c1722', '#1c2c3a', '#f5f7fa', '#b6c7d4', '#ffda83', '#92d4b9'
ZONE = ZoneInfo('Europe/Amsterdam')


def stamp(value, pattern='%H:%M'):
    date = datetime.fromtimestamp(value, ZONE)
    pattern = pattern.replace('%A', ('maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag')[date.weekday()])
    return date.strftime(pattern)


def phase(item, now=None):
    now = time.time() if now is None else now
    return 'past' if item['end'] <= now else 'future' if item['start'] > now else 'current'


def progress(item, now=None):
    now = time.time() if now is None else now
    total = max(1, item['end']-item['start'])
    elapsed = max(0, min(total, now-item['start']))
    return elapsed/total, int(elapsed//60), math.ceil((total-elapsed)/60)


class GuideView(tk.Frame):
    def __init__(self, parent, tv):
        super().__init__(parent, bg=BG)
        self.tv = tv
        self.items, self.selected, self.rows = [], 0, []
        self.plans = []
        self.notice = ''
        self.label(self, 'Terugkijken', 32, bold=True).pack(anchor='w', padx=42, pady=(24, 5))
        self.hint = self.label(self, '↑ ↓ Bladeren    OK: terugkijken / opnemen    Terug: zenders', 15, color=MUTED)
        self.hint.pack(anchor='w', padx=42, pady=(0, 12))
        self.message = self.label(self, '', 13, color=AMBER)
        self.message.pack(anchor='w', padx=42, pady=(0, 9))
        columns = tk.Frame(self, bg=BG)
        columns.pack(fill='both', expand=True, padx=40, pady=(0, 22))
        columns.columnconfigure(0, weight=61, uniform='guide')
        columns.columnconfigure(1, weight=39, uniform='guide')
        columns.rowconfigure(0, weight=1)
        left = tk.Frame(columns, bg=BG)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 22))
        self.day = self.label(left, '', 18)
        self.day.pack(anchor='w', pady=(0, 10))
        self.canvas = tk.Canvas(left, bg=BG, highlightthickness=0, yscrollincrement=25)
        self.scrollbar = tk.Scrollbar(left, orient='vertical', command=self.canvas.yview,
                                      bg=TILE, troughcolor=BG, width=10)
        self.scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize)
        self.canvas.bind('<MouseWheel>', lambda event: self.move(-1 if event.delta > 0 else 1))
        self.canvas.bind('<Button-4>', lambda event: self.move(-1))
        self.canvas.bind('<Button-5>', lambda event: self.move(1))
        self.info = tk.Frame(columns, bg=TILE, highlightthickness=1, highlightbackground='#31495a', padx=24, pady=22)
        self.info.grid(row=0, column=1, sticky='nsew', pady=(35, 0))
        # The allocated column controls geometry, never the programme text.
        self.info.pack_propagate(False)
        self.state = self.label(self.info, '', 13, color=AMBER, bold=True)
        self.state.pack(anchor='w')
        self.when = self.label(self.info, '', 14, color=MUTED)
        self.when.pack(anchor='w', pady=(12, 14))
        self.title = self.label(self.info, '', 25, bold=True)
        self.title.pack(anchor='w', fill='x')
        self.description = self.label(self.info, '', 17, color=MUTED)
        self.description.pack(anchor='w', fill='x', pady=(15, 14))
        self.live = tk.Frame(self.info, bg='#18373c', padx=15, pady=12)
        self.live_text = self.label(self.live, '', 16, color=GREEN)
        self.live_text.configure(bg='#18373c')
        self.live_text.pack(anchor='w')
        self.bar = tk.Canvas(self.live, bg='#2b4654', height=9, highlightthickness=0)
        self.bar.pack(fill='x', pady=12)
        self.remaining = self.label(self.live, '', 13, color=MUTED)
        self.remaining.configure(bg='#18373c')
        self.remaining.pack(anchor='w')
        self.action = self.label(self.info, '', 16, color=AMBER)
        self.action.pack(anchor='w', fill='x', pady=(18, 8))
        self.record_hint = self.label(self.info, '', 14, color=MUTED)
        # All programme actions are available through OK; no recording key is required.
        self.info.bind('<Configure>', self.resize_info)
        self.tv.root.after(1000, self.tick)

    def label(self, parent, value, size, color=WHITE, bold=False):
        return tk.Label(parent, text=value, bg=parent.cget('bg'), fg=color, anchor='w', justify='left',
                        font=('DejaVu Sans', max(10, int(size*self.tv.scale)), 'bold' if bold else 'normal'))

    def resize(self, event):
        self.canvas.itemconfigure(self.window, width=max(1, event.width-2))
        for frame, title, meta, badge, bar in self.rows:
            title.configure(wraplength=max(100, int(event.width*.54)))
        self.tv.root.after(30, self.reveal)

    def resize_info(self, event):
        width = max(100, event.width-54)
        for label in (self.title, self.description, self.when, self.action, self.record_hint):
            if int(float(str(label.cget('wraplength')))) != width:
                label.configure(wraplength=width)
        # Long descriptions never push playback/record controls below a Full HD screen.
        lines = max(2, min(7, int((event.height-390*self.tv.scale)/(27*self.tv.scale))))
        if int(self.description.cget('height')) != lines:
            self.description.configure(height=lines)

    def plan(self, item):
        return next((plan for plan in self.plans if plan['state'] in ('queued', 'recording')
                     and item['start'] <= plan['start'] < item['end']), None)

    def action_text(self, item):
        state = phase(item)
        if state == 'past':
            return 'OK · Terugkijken' if item.get('archive') else 'Niet beschikbaar'
        if state == 'current':
            return ('OK · Opname stoppen' if planned['state'] == 'recording' else 'OK · Opname annuleren') if (planned := self.plan(item)) else 'OK · Resterend deel opnemen'
        return '● Gepland · OK annuleren' if self.plan(item) else 'OK · Opname plannen'

    def set_items(self, items, selected=0, plans=(), notice=''):
        self.items, self.plans, self.notice = items, list(plans), notice
        self.selected = max(0, min(selected, len(items)-1))
        self.message.configure(text=notice)
        for widget in self.inner.winfo_children():
            widget.destroy()
        self.rows = []
        previous = None
        for index, item in enumerate(items):
            day = stamp(item['start'], '%d-%m-%Y')
            if day != previous:
                self.label(self.inner, stamp(item['start'], '%A %d-%m'), 14, color=MUTED).pack(anchor='w', pady=(13, 8))
                previous = day
            frame = tk.Frame(self.inner, bg=TILE, highlightthickness=2, highlightbackground='#293f50', padx=14, pady=12)
            frame.pack(fill='x', pady=5, padx=3)
            frame.columnconfigure(1, weight=1)
            clock = self.label(frame, stamp(item['start']), 19, color=MUTED)
            clock.grid(row=0, column=0, sticky='nw', padx=(0, 16))
            title = self.label(frame, item['title'], 20)
            title.configure(wraplength=max(100, int(self.canvas.winfo_width()*.54)))
            title.grid(row=0, column=1, sticky='nw')
            meta = self.label(frame, f"{stamp(item['end'])} · {max(1, (item['end']-item['start'])//60)} min", 13, color=MUTED)
            meta.grid(row=1, column=1, sticky='w', pady=(6, 0))
            badge = self.label(frame, self.action_text(item), 12, color=GREEN)
            badge.grid(row=0, column=2, rowspan=2, sticky='e', padx=(9, 0))
            bar = tk.Canvas(frame, bg='#2b4654', height=5, highlightthickness=0)
            if phase(item) == 'current':
                bar.grid(row=2, column=1, columnspan=2, sticky='ew', pady=(9, 0))
            self.rows.append((frame, title, meta, badge, bar))
            for widget in (frame, clock, title, meta, badge, bar):
                widget.bind('<Button-1>', lambda event, i=index: self.activate(i))
                widget.bind('<Button-4>', lambda event: self.move(-1))
                widget.bind('<Button-5>', lambda event: self.move(1))
        if not items:
            self.label(self.inner, 'Geen programma’s beschikbaar.', 20, color=MUTED).pack(pady=25)
        self.highlight()
        self.tv.root.after(50, self.center_selected)

    def activate(self, index):
        self.selected = self.tv.panel_selected = index
        self.highlight()
        self.tv.panel_choose(index)

    def move(self, delta):
        if self.items:
            self.selected = self.tv.panel_selected = max(0, min(len(self.items)-1, self.selected+delta))
            self.highlight()
            self.reveal()
        return 'break'

    def center_selected(self):
        if not self.rows:
            return
        frame = self.rows[self.selected][0]
        total = max(1, self.inner.winfo_height())
        self.canvas.yview_moveto(max(0, frame.winfo_y()-self.canvas.winfo_height()*.35)/total)

    def reveal(self):
        if not self.rows:
            return
        frame = self.rows[self.selected][0]
        top, height = frame.winfo_y(), frame.winfo_height()
        total = max(1, self.inner.winfo_height())
        visible = max(1, self.canvas.winfo_height())
        current = self.canvas.canvasy(0)
        if top < current+35:
            self.canvas.yview_moveto(max(0, top-40)/total)
        elif top+height > current+visible:
            self.canvas.yview_moveto(max(0, top+height-visible+10)/total)

    def highlight(self):
        for index, (frame, title, meta, badge, bar) in enumerate(self.rows):
            selected = index == self.selected
            color = '#304453' if selected else TILE
            frame.configure(bg=color, highlightbackground=AMBER if selected else '#293f50')
            for widget in frame.winfo_children():
                if isinstance(widget, tk.Label):
                    widget.configure(bg=color)
            badge.configure(text=self.action_text(self.items[index]), fg=AMBER if selected else GREEN)
        self.update_info()

    def update_info(self):
        if not self.items:
            self.title.configure(text='Geen programmagegevens')
            self.description.configure(text='')
            self.live.pack_forget()
            self.action.configure(text='')
            self.record_hint.configure(text='')
            return
        item = self.items[self.selected]
        state, planned = phase(item), self.plan(item)
        self.day.configure(text=stamp(item['start'], '%A %d-%m-%Y'))
        self.state.configure(text='OPNAME GEPLAND' if planned else {'past': 'TERUGKIJKEN', 'current': 'NU BEZIG', 'future': 'BINNENKORT'}[state])
        self.when.configure(text=f"{stamp(item['start'], '%d-%m')} · {stamp(item['start'])} – {stamp(item['end'])} · {max(1, (item['end']-item['start'])//60)} min")
        self.title.configure(text=item['title'])
        self.description.configure(text=item.get('description', ''))
        self.action.configure(text=self.action_text(item))
        self.record_hint.configure(text='')
        if state == 'current':
            if not self.live.winfo_manager():
                self.live.pack(fill='x', before=self.action, pady=(10, 0))
            ratio, elapsed, remaining = progress(item)
            self.live_text.configure(text=f'● NU BEZIG · {int(ratio*100)}%')
            self.remaining.configure(text=f'{elapsed} min verstreken · nog {remaining} min')
            self.bar.delete('all')
            self.bar.create_rectangle(0, 0, self.bar.winfo_width()*ratio, 9, fill=GREEN, outline='')
        else:
            self.live.pack_forget()

    def tick(self):
        if self.winfo_ismapped():
            self.highlight()
            for item, (_, _, _, _, bar) in zip(self.items, self.rows):
                if phase(item) == 'current':
                    bar.grid(row=2, column=1, columnspan=2, sticky='ew', pady=(9, 0))
                    bar.delete('all')
                    bar.create_rectangle(0, 0, bar.winfo_width()*progress(item)[0], 5, fill=GREEN, outline='')
                else:
                    bar.grid_remove()
        self.tv.root.after(1000, self.tick)
