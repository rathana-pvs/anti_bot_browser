from replay_evidence_corpus import entry_plan_field


def test_comment_entry_plan_uses_recorded_stage_not_shared_label():
    metadata = {'12_before_first_comment': {'label': 'before_first_comment'}}
    assert entry_plan_field('13_caption_entry_plan', {'reason': 'link_or_comment'}, metadata) == 'comment'


def test_caption_containing_link_is_not_assumed_to_be_comment():
    metadata = {'04_media_state': {'label': 'media_state'},
                '12_before_first_comment': {'label': 'before_first_comment'}}
    assert entry_plan_field('05_caption_entry_plan', {'reason': 'link_or_comment'}, metadata) == 'caption'


def test_explicit_field_metadata_takes_precedence():
    assert entry_plan_field('05_caption_entry_plan', {'field': 'comment'}, {}) == 'comment'


def test_unordered_frame_does_not_guess_field():
    assert entry_plan_field('caption_entry_plan', {}, {}) == 'unknown'
