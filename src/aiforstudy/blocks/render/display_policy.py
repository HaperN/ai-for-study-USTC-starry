"""Host-owned reader geometry, shared by publication and visual evidence."""
from copy import deepcopy

_POLICY = {
    'schema_version': 'ReaderDisplayPolicyV1',
    'layout_version': 'desktop-comparison-v1',
    'profiles': [
        {'profile_id': 'wide', 'width': 1280, 'height': 900, 'min_font_px': 14},
    ],
    'maximum_media_width_px': 780,
    'layout_max_width_px': 1770,
    'desktop_sidebar_width_px': 254,
    'desktop_content_padding_x_px': 42,
    'comparison_gap_px': 32,
    'comparison_text_weight': 1,
    'comparison_media_weight': 1.02,
    'media_border_width_px': 1,
    'scrollbar_allowance_px': 15,
    'minimum_label_css_px': 14,
    'surface_radius_px': 12,
    'details_padding_x_px': 14,
    'details_padding_y_px': 12,
    'viewport_gutter_px': 16,
}
def _comparison_media_width(viewport_width, scrollbar):
    available = (min(viewport_width - scrollbar, _POLICY['layout_max_width_px'])
                 - _POLICY['desktop_sidebar_width_px']
                 - 2 * _POLICY['desktop_content_padding_x_px'] - _POLICY['comparison_gap_px'])
    fraction = _POLICY['comparison_media_weight'] / (_POLICY['comparison_text_weight'] + _POLICY['comparison_media_weight'])
    return round(min(_POLICY['maximum_media_width_px'], available * fraction) - 2 * _POLICY['media_border_width_px'])


# Review the published desktop comparison column. At 1280px with a 15px
# scrollbar the media column is 451.94px; the video inside its border is
# 449.94px. Round to CSS pixels for capture and use that conservative image
# width for static media too. The 780px limit still serves single-column views.
# Size and font recommendations produce appearance warnings; inaccessible or
# clipped content is a functional defect regardless of these target values.
for _profile in _POLICY['profiles']:
    _profile['layout_mode'] = 'desktop_comparison'
    _profile['content_width'] = _comparison_media_width(_profile['width'], _POLICY['scrollbar_allowance_px'])
    _profile['max_content_width'] = _comparison_media_width(_profile['width'], 0)


def reader_display_policy() -> dict:
    return deepcopy(_POLICY)


def reader_display_css() -> str:
    """Only trusted numerical policy values reach the CSS variable boundary."""
    p = reader_display_policy()
    values = {
        'media-max-width': p['maximum_media_width_px'],
        'media-min-label': p['minimum_label_css_px'],
        'media-radius': p['surface_radius_px'],
        'media-inset-x': p['details_padding_x_px'],
        'media-inset-y': p['details_padding_y_px'],
        'media-viewport-gutter': p['viewport_gutter_px'],
        'reader-layout-max-width': p['layout_max_width_px'],
        'reader-desktop-sidebar-width': p['desktop_sidebar_width_px'],
        'reader-desktop-content-inset': p['desktop_content_padding_x_px'],
        'reader-comparison-gap': p['comparison_gap_px'],
        'reader-media-border': p['media_border_width_px'],
    }
    return (':root{' + ''.join('--' + key + ':' + str(value) + 'px;' for key, value in values.items())
            + '--reader-comparison-text-track:' + str(p['comparison_text_weight']) + 'fr;'
            + '--reader-comparison-media-track:' + str(p['comparison_media_weight']) + 'fr;}')
