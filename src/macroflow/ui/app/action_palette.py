"""Action categories: a few common buttons, with the rest expandable."""
ACTION_CATEGORIES = (
    ('键鼠', ('add_click', 'add_key', 'add_mouse_move'),
     ('add_text', 'add_repeat_click', 'add_key_sequence', 'add_scroll',
      'add_turn', 'add_scroll_sequence')),
    ('识别', ('add_module', 'add_ocr_compare'),
     ('add_multi_condition_click', 'add_row_list_condition_click')),
    ('流程', ('add_delay', 'add_jump'),
     ('add_block', 'add_python_script', 'add_notice')),
    ('窗口/软件', ('add_open_app', 'add_close_app', 'add_activate_window'),
     ('add_rebind_window', 'add_set_resolution')),
    ('录制/脚本', ('_toggle_record_from_toolbar', '_insert_script_reference'),
     ('_insert_script_inline', '_insert_script_range')),
)


def action_category_specs(specs):
    """Group the editor's available commands and retain their labels/styles."""
    buttons = {item[1]: item for item in specs}
    return tuple((label, tuple(buttons[key] for key in primary if key in buttons),
                  tuple(buttons[key] for key in extra if key in buttons))
                 for label, primary, extra in ACTION_CATEGORIES)
