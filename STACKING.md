Q Stack Feature

Adds a reusable shortcut system in project/scene/board.py (lines 131-168) so the view can register key handlers; Qt.Key_Q now triggers stacking logic.
Main window wiring lives in project/__main__.py:
Construction sets up the scene with a drag callback, registers the Q shortcut, and keeps focus on the view.
_handle_stack_shortcut gathers the selected table cards, sorts/rotates them, computes a persistent anchor (median X, lowest Y), and offsets each card into a cascading stack. It updates the model positions, bumps Z-values, and reanchors any ongoing drag.
Anchor state is cached (_stack_selection_key, _stack_anchor_point, _last_stack_cycle_ids) so repeated presses rotate the order without moving the stack. _clear_stack_anchor resets the cache, and _selection_median_point implements the median/lower-edge rule.
Drag detection lives inside project/card/card.py: cards track _dragged_by_user, emit scene.notify_manual_drag() on the first user move, and expose reanchor_drag, reset_user_drag_state, and consume_user_drag helpers so the main window can distinguish manual motion from programmatic moves. BoardScene forwards manual-drag notifications back to MainWindow.
_on_card_manual_drag in project/__main__.py clears the stack cache immediately, so any user movement—whether the mouse is still down or just released—forces the next Q press to recompute the anchor from the cards’ new positions.
Affected files: project/__main__.py, project/scene/board.py, project/card/card.py.