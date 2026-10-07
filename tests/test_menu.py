"""Remote menu behavior, without requiring a graphical display."""
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    from mijntv.tv import TV
except ModuleNotFoundError as error:
    if error.name != 'tkinter':
        raise
    # Only navigation is exercised; no Tk widgets are constructed here.
    with patch.dict(sys.modules, {'tkinter': ModuleType('tkinter')}):
        TV = importlib.import_module('mijntv.tv').TV


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.tv = TV.__new__(TV)
        self.tv.scale = 1
        self.tv.menu_open = False
        self.tv.menu_selected = 0
        self.tv.panel_mode = None
        self.tv.watching = False
        self.tv.playing_recording = None
        self.tv.channels = [{'label': f'Zender {i}', 'id': str(i)} for i in range(30)]
        self.tv.selected, self.tv.page = 17, 1
        self.tv.menu = Mock()
        self.tv.menu_buttons = [Mock(), Mock(), Mock()]
        self.tv.menu_hint = Mock()
        self.tv.render = Mock()
        self.tv.layout_home = Mock()
        self.tv.draw_menu_card = Mock()
        self.tv.open_panel = Mock()
        self.tv.go_home = Mock()
        self.tv.zap = Mock()
        self.tv.player = Mock()

    def key(self, name):
        return self.tv.key(SimpleNamespace(keysym=name))

    def test_cards_draw_labels_icons_and_selected_outline(self):
        card = Mock()
        card.winfo_width.return_value = 500
        card.winfo_height.return_value = 115
        for index, title in enumerate(('Zenders', 'Opnames', 'Programmagids /\nopnemen')):
            card.reset_mock()
            self.tv.menu_color = '#0e1e32'
            self.tv.menu_selected = index
            TV.draw_menu_card(self.tv, card, index)
            self.assertEqual(card.create_text.call_args.kwargs['text'], title)
            self.assertTrue(any(call.kwargs.get('outline') == '#ffce66'
                                for call in card.create_polygon.call_args_list))

    def test_menu_from_lower_row_preserves_channel_and_page(self):
        self.key('Menu')
        self.assertTrue(self.tv.menu_open)
        self.key('Down'); self.key('Down')
        self.assertEqual(self.tv.menu_selected, 2)
        self.assertIn('Zender 17', self.tv.menu_hint.configure.call_args.kwargs['text'])
        self.key('Escape')
        self.assertFalse(self.tv.menu_open)
        self.assertEqual((self.tv.selected, self.tv.page), (17, 1))
        self.tv.go_home.assert_not_called()

    def test_guide_uses_selected_channel(self):
        self.key('F2'); self.key('Down'); self.key('Down'); self.key('Return')
        self.tv.open_panel.assert_called_once_with('guide')
        self.assertEqual(self.tv.selected, 17)
        self.assertFalse(self.tv.menu_open)

    def test_recordings_and_channels_actions(self):
        self.key('Menu'); self.key('Down'); self.key('Return')
        self.tv.open_panel.assert_called_once_with('recordings')
        self.key('Menu'); self.key('Return')
        self.tv.go_home.assert_called_once()

    def test_menu_does_not_zap_or_stop_video(self):
        self.tv.watching = True
        self.key('Menu'); self.key('Down'); self.key('Up'); self.key('Right')
        self.assertTrue(self.tv.watching)
        self.tv.player.stop.assert_not_called()
        self.tv.zap.assert_not_called()
        self.tv.go_home.assert_not_called()

    def test_menu_toggle_and_navigation_bounds(self):
        self.key('o'); self.key('Up')
        self.assertEqual(self.tv.menu_selected, 0)
        for _ in range(5): self.key('Down')
        self.assertEqual(self.tv.menu_selected, 2)
        self.key('o')
        self.assertFalse(self.tv.menu_open)

    def test_left_opens_menu_from_both_rows(self):
        for selected in (10, 15):
            self.tv.selected = selected
            self.key('Left')
            self.assertTrue(self.tv.menu_open)
            self.key('BackSpace')
            self.assertEqual(self.tv.selected, selected)

    def test_empty_channels_still_allow_recordings(self):
        self.tv.channels = []
        self.key('Menu'); self.key('Down'); self.key('Return')
        self.tv.open_panel.assert_called_once_with('recordings')

    def test_background_mouse_actions_do_not_change_selection(self):
        self.key('Menu')
        self.tv.change_page(1)
        self.tv.choose(0)
        self.assertEqual((self.tv.selected, self.tv.page), (17, 1))
        self.assertFalse(self.tv.watching)

    def test_panel_selection_restored_after_dismissal(self):
        self.tv.panel_mode = 'guide'
        self.tv.panel_selected = 4
        self.key('Menu')
        self.assertEqual(self.tv.menu_selected, 2)
        self.key('Escape')
        self.assertEqual(self.tv.panel_mode, 'guide')
        self.assertEqual(self.tv.panel_selected, 4)


if __name__ == '__main__':
    unittest.main()
