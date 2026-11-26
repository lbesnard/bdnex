"""The base of the popups of the review screen: one that can be closed only once.

A key held down, or a click and an Enter together, can send two events to a popup before the first has closed it; the second ``dismiss``
then finds no screen to remove and Textual raises ``ScreenStackError`` (the whole review screen came down). Here the second one is ignored.
"""

from textual.screen import ModalScreen


class Modal(ModalScreen):
    _left = False

    def dismiss(self, result=None):
        if self._left:
            return None
        self._left = True
        return super().dismiss(result)
