import html
import re

import sublime
import sublime_plugin


ATX_PATTERN = re.compile(r'^(?:>\s*)?(#{1,6})\s+(.+)$')
#                              ^ Blockquotes can contain other Markdown elements, including headers
SETEXT_H1_PATTERN = re.compile(r'^=+\s*$')
SETEXT_H2_PATTERN = re.compile(r'^-+\s*$')


class Heading:
    def __init__(self, title, level, line_number):
        self.title = title
        self.level = level
        self.line_number = line_number


class FormattedHeading:
    def __init__(self, indentation, title, line_number):
        self.indentation = indentation
        self.title = title
        self.line_number = line_number


phantoms = {}
phantom_positions = {}
prev_html = {}

class MdTocToggleCommand(sublime_plugin.TextCommand):
    def run(self, _):
        view = self.view
        if not is_markdown_file(view):
            sublime.status_message("This command only works with Markdown files")
            return

        view_id = view.id()
        if view_id in phantoms and len(phantoms[view_id].phantoms) > 0:
            hide_phantom(view)
        else:
            headings = parse_headings(view)
            if not headings:
                sublime.status_message("No headings found")
                return
            show_phantom(view, headings)


class MdTocNavigateCommand(sublime_plugin.TextCommand):
    def run(self, _, line_number):
        view = self.view

        point = view.text_point(line_number, 0)
        view.sel().clear()
        view.sel().add(sublime.Region(point, point))

        view.show(point, False)
        # 🩼 waiting for layout to be updated
        sublime.set_timeout(lambda: view.show_at_center(point), 30)


class MdTocListener(sublime_plugin.EventListener):
    def on_load_async(self, view):
        settings = sublime.load_settings("mdtoc.sublime-settings")
        show_on_open = settings.get("show_on_open")

        if not show_on_open:
            return

        if is_markdown_file(view):
            headings = parse_headings(view)
            if headings:
                show_phantom(view, headings)

    def on_modified(self, view):
        # TODO: the function is kinda laggy on really large files
        settings = sublime.load_settings("mdtoc.sublime-settings")
        auto_update = settings.get("auto_update")

        if not auto_update:
            return

        view_id = view.id()

        if view_id in phantoms and len(phantoms[view_id].phantoms) > 0:
            headings = parse_headings(view)
            show_phantom(view, headings, is_reposition_needed=False)

    def on_selection_modified(self, view):
        settings = sublime.load_settings("mdtoc.sublime-settings")
        hide_toc = settings.get("hide_toc")

        if hide_toc == "after_cursor_move":
            view_id = view.id()
            if view_id in phantoms and len(phantoms[view_id].phantoms) > 0:
                hide_phantom(view)

    def on_close(self, view):
        view_id = view.id()
        phantoms.pop(view_id, None)
        phantom_positions.pop(view_id, None)
        prev_html.pop(view_id, None)


def parse_headings(view):
    # the function doesn't handle ```code blocks``` correctly - according to the specification, all headers
    # inside code blocks should be ignored (but nobody cares, lol)
    headings = []
    ignored_lines = []

    def get_setext_heading(setext_region, level):
        row, _ = view.rowcol(setext_region.begin())
        if row > 0 and row - 1 not in ignored_lines:
            line_region = view.line(view.text_point(row - 1, 0))
            ignored_lines.append(row)
            # a line can't be both a text and directive - we need to skip it when processing
            # the next line (it might be a setext header too)
            line_content = view.substr(line_region)
            if line_content.strip():
                # if not, the next line should be treated as a horizontal rule, not a heading
                return Heading(line_content, level, row - 1)
        return None

    combined_pattern = ATX_PATTERN.pattern + "|" + SETEXT_H1_PATTERN.pattern + "|" + SETEXT_H2_PATTERN.pattern
    for region in view.find_all(combined_pattern):
        text = view.substr(region)
        match = ATX_PATTERN.search(text)
        if match:
            row, _ = view.rowcol(region.begin())
            headings.append(Heading(match.group(2), len(match.group(1)), row))
        match = SETEXT_H1_PATTERN.search(text)
        if match:
            headings.append(get_setext_heading(region, 1))
        match = SETEXT_H2_PATTERN.search(text)
        if match:
            headings.append(get_setext_heading(region, 2))

    return list(filter(None, headings))


def is_markdown_file(view):
    syntax = view.settings().get('syntax', '')
    file_name = view.file_name() or ''
    return 'markdown' in syntax.lower() or file_name.endswith(('.md', '.markdown'))


def make_indentation(heading, indent_style, indent_width):
    spaces = "\u00A0" * indent_width * max(heading.level - 1, 0)
    if indent_style == "spaces":
        return spaces
    elif indent_style == "bullets":
        bullets = ["", "•\u00A0", "◦\u00A0", "▪\u00A0", "♦\u00A0"]
        bullet = bullets[heading.level - 1]
        return spaces[:-2] + bullet
    return " "


def make_numbers_indentation(headings, indent_width):
    formatted_headings = []

    levels = [-1, 0, 0, 0, 0, 0, 0]
    for index, heading in enumerate(headings):
        indentation = ""

        levels[heading.level] += 1

        has_prev = index > 0
        if has_prev and heading.level < headings[index - 1].level:
            for i in range(heading.level + 1, len(levels)):
                levels[i] = 0

        for i in range(1, heading.level + 1):
            indentation += "{}.".format(levels[i])
        indentation = "\u00A0" * indent_width * max(heading.level - 1, 0) + indentation + "\u00A0"

        formatted_headings.append(FormattedHeading(
            html.escape(indentation),
            html.escape(heading.title),
            heading.line_number
        ))
    return formatted_headings


def make_tree_indentation(headings, indent_width):
    formatted_headings = []
    ind = max(indent_width, 0)

    active_levels = [False] * 7

    for index, heading in enumerate(headings):
        indentation = ""

        level = heading.level
        prev = index - 1
        next = index + 1
        prev_level = headings[prev].level if index > 0 else 0
        next_level = headings[next].level if next < len(headings) else 0

        if prev_level < level - 1:
            for level in range(prev_level + 1, level):
                active_levels[level] = True

        for l in range(2, level):
            if active_levels[l]:
                indentation += "│\u00A0" + "\u00A0" * ind
            else:
                indentation += "\u00A0\u00A0" + "\u00A0" * ind

        if level > 1:
            has_siblings = False
            for next_index in range(next, len(headings)):
                # no fucks were given while making this O(n²)
                if headings[next_index].level < level:
                    break
                if headings[next_index].level == level:
                    has_siblings = True
                    break

            if has_siblings:
                indentation += "├" + "─" * ind + "\u00A0"
                active_levels[level] = True
            else:
                indentation += "└" + "─" * ind + "\u00A0"
                active_levels[level] = False

        if next_level < level:
            for i in range(next_level + 1, 7):
                active_levels[i] = False

        formatted_headings.append(FormattedHeading(
            html.escape(indentation),
            html.escape(heading.title),
            heading.line_number
        ))

    return formatted_headings


def format_headings(headings):
    settings = sublime.load_settings("mdtoc.sublime-settings")
    indent_width = settings.get("indent_width")
    indent_style = settings.get("indent_style")

    if indent_style == "tree":
        return make_tree_indentation(headings, indent_width)
    if indent_style == "numbers":
        return make_numbers_indentation(headings, indent_width)
    else:
        return [
            FormattedHeading(
                make_indentation(heading, indent_style, indent_width),
                html.escape(heading.title),
                heading.line_number
            )
            for heading in headings
        ]


def generate_toc_html(_, headings):
    settings = sublime.load_settings("mdtoc.sublime-settings")
    link_color = settings.get("link_color")

    html_parts = []

    dynamic_style = '''
            .toc-link {{
                text-decoration: none;
                color: {color};
            }}'''.format(color='var(--foreground)' if link_color == "text" else 'var(--bluish)')

    html_parts.append('''
        <style>
            .toc-container {{
                border: 1px solid color(var(--foreground) alpha(0.5));
                border-radius: 0.5em;
                padding: 0.75em 0.75em;
                margin: 0.75em 1em 0.75em 0;
            }}
            .toc-title {{
                font-weight: bold;
                color: var(--foreground);
                padding-bottom: 0.5em;
                border-bottom: 1px solid color(var(--foreground) alpha(0.5));
                margin-bottom: 0.5em;
            }}
            .toc-indent {{
                font-family: monospace;
                white-space: pre-wrap;
            }}
            {dynamic_style}
        </style>
    '''.format(dynamic_style=dynamic_style))
    html_parts.append(
        '<div class="toc-container"><div class="toc-title">Table of Contents</div><div class="toc-content">')

    formatted_headings = format_headings(headings)
    for heading in formatted_headings:
        html_parts.append('<span class="toc-indent">{}</span><a class="toc-link" href="{}">{}</a><br/>'.format(
            heading.indentation, heading.line_number, heading.title))

    html_parts.append('</div></div>')

    return ''.join(html_parts)


def calc_phantom_position(view, headings):
    settings = sublime.load_settings("mdtoc.sublime-settings")
    position = settings.get("toc_position")

    if position == "document_end":
        return view.size()
    elif position == "after_first_heading" and headings:
        first_heading = headings[0]
        line_region = view.line(view.text_point(first_heading.line_number, 0))
        return line_region.end()
    elif position == "at_cursor":
        return view.sel()[0].begin() if len(view.sel()) > 0 else 0
    else:
        return 0


def show_phantom(view, headings, is_reposition_needed=True):
    view_id = view.id()

    if view_id not in phantoms:
        phantoms[view_id] = sublime.PhantomSet(view, 'md_toc')
    phantom_set = phantoms[view_id]

    html_content = generate_toc_html(view, headings)
    if not is_reposition_needed and prev_html[view_id] == html_content:
        return
    prev_html[view_id] = html_content

    if is_reposition_needed or view_id not in phantom_positions:
        position = calc_phantom_position(view, headings)
        phantom_positions[view_id] = position
    else:
        position = phantom_positions[view_id]

    phantom = sublime.Phantom(
        sublime.Region(position, position),
        html_content,
        sublime.LAYOUT_BLOCK,
        on_navigate=lambda href: handle_toc_click(view, href)
    )
    phantom_set.update([phantom])


def hide_phantom(view):
    view_id = view.id()
    if view_id in phantoms:
        phantoms[view_id].update([])
    phantom_positions.pop(view_id, None)


def handle_toc_click(view, href):
    settings = sublime.load_settings("mdtoc.sublime-settings")
    hide_toc = settings.get("hide_toc")

    try:
        line_number = int(href)
        view.run_command('md_toc_navigate', {'line_number': line_number})

        if hide_toc != "never":
            hide_phantom(view)
    except ValueError:
        pass  # ¯\_(ツ)_/¯
