import unittest
from mijntv.matching import RecordingCatalog


def source(identifier, name, epg='', group='NL| TV'):
    return dict(id=identifier,name=name,tvg_id=epg,group_name=group,url='http://example.test/private')


class MatchingTests(unittest.TestCase):
    def favorite(self, name='NL| ESPN 2 HD', epg='ESPN2.nl', group='NL| ORIGINAL'):
        return dict(id='favorite',label='ESPN 2',name=name,epg_id=epg,group_name=group)

    def test_same_guide_quality_variants_choose_one_source(self):
        rows=[source('a','NL: ESPN 2 HD','espn2.nl'),
              source('b','NL: ESPN 2 FHD','ESPN2.NL')]
        self.assertEqual(RecordingCatalog(rows).match(self.favorite())['id'],'b')

    def test_missing_guide_matches_country_and_normalized_name(self):
        rows=[source('one','NL: ESPN 1 HD'),source('two','NL: ESPN 2 ᴿᴬᵂ ◉'),
              source('foreign','BE: ESPN 2 HD',group='BE| TV')]
        self.assertEqual(RecordingCatalog(rows).match(self.favorite())['id'],'two')

    def test_different_guide_identities_with_same_name_are_ambiguous(self):
        rows=[source('a','NL: ESPN 2 HD','schedule-a'),
              source('b','NL: ESPN 2 FHD','schedule-b')]
        self.assertIsNone(RecordingCatalog(rows).match(self.favorite()))

    def test_original_exact_variant_is_kept(self):
        rows=[source('a','NL| ESPN 2 HD','espn2.nl','NL| ORIGINAL'),
              source('b','NL: ESPN 2 FHD','espn2.nl')]
        self.assertEqual(RecordingCatalog(rows).match(self.favorite())['id'],'a')

    def test_wrong_country_is_not_matched_by_name(self):
        self.assertIsNone(RecordingCatalog([source('b','BE: ESPN 2 HD',group='BE| TV')]).match(self.favorite()))

    def test_matching_does_not_fall_back_to_viewing_url(self):
        self.assertIsNone(RecordingCatalog([]).match(self.favorite()))
