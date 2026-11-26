"""``{how} cli tui``: a directive placed under a heading that tags the heading with the tools that do the job.

The tags are added to the heading's title after the page is read, so the heading's anchor (made from its text) does not change.
``cli``: a command you type; ``tui``: the review screen (`bdnex review`); both: either one does it. The legend is on the page that uses it.
"""

from docutils import nodes
from docutils.parsers.rst import Directive

LABELS = {"cli": ("CLI", "A command you type in the terminal"), "tui": ("TUI", "In the review screen (bdnex review)")}


class How(Directive):
    required_arguments = 0
    optional_arguments = 2
    final_argument_whitespace = True
    has_content = False

    def run(self):
        wanted = [word.lower() for word in " ".join(self.arguments).split()]
        for word in wanted:
            if word not in LABELS:
                raise self.error(f"how: {word!r} is not cli or tui")
        node = how_tags()
        node["tools"] = wanted
        return [node]


class how_tags(nodes.General, nodes.Element):
    pass


def attach(app, doctree):
    """Move each tag into the title of its section; the tag node itself is removed."""
    for tags in list(doctree.findall(how_tags)):
        section = tags.parent
        title = next((child for child in section.children if isinstance(child, nodes.title)), None)
        if title is not None:
            for tool in tags["tools"]:
                text, tip = LABELS[tool]
                badge = nodes.inline(text, text, classes=["how-tag", f"how-{tool}"])
                badge["title"] = tip
                title += nodes.Text(" ")
                title += badge
        tags.parent.remove(tags)


def visit_nothing(self, node):
    raise nodes.SkipNode


def setup(app):
    app.add_directive("how", How)
    app.add_node(how_tags, html=(visit_nothing, None), latex=(visit_nothing, None), text=(visit_nothing, None))
    app.connect("doctree-read", attach)
    app.add_css_file("how.css")
    return {"parallel_read_safe": True}
