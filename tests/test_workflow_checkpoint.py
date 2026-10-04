from scripts.verify_lec0_segments import checkpoint_matches


def test_resume_requires_matching_explicit_version():
    good = dict(model='m', prompt_version='v', pages=[1, 6], test_only=True)
    assert checkpoint_matches(good, model='m', prompt_version='v')
    for changed in ({}, {**good, 'model': 'other'}, {**good, 'prompt_version': 'old'},
                    {**good, 'pages': [1, 9]}, {**good, 'test_only': False}):
        assert not checkpoint_matches(changed, model='m', prompt_version='v')
