# Zone Visibility Rules

## Card State Flags
- **`face_down`** – card still paints in the main scene, but when the render target switches to the virtual camera the card draws its back art. Zones that want cards to look face-down (e.g., hand) should set this flag to `True`.
- **`_zone_hidden`** – card is removed from the QGraphics scene entirely (no painting on the board or camera). Only zones that are meant to be invisible, such as the library, should enable this.

## Zone Behaviour
- **Hand Zone**
  - Fully interactive: cards paint and can be dragged.
  - When a card enters the hand, set `face_down=True` but leave `_zone_hidden=False`.
  - When the card leaves the hand, reset `face_down=False`.
- **Library Zone**
  - Non-interactive placeholder; cards should not paint on the board at all.
  - When a card enters the library, set both `face_down=True` (for camera rendering) and `_zone_hidden=True` (to hide the graphics item).
  - When the card leaves the library, reset both flags to `False`.
- **Preview Zone**
  - Displays a single card next to the deck view. It should never hide cards; it simply shows the selected library card while keeping the model state consistent.
- **Other Zones (table, battlefield, etc.)**
  - Typically keep both flags `False` so cards paint normally on both screen and camera, unless there is zone-specific behaviour (e.g., a secret zone that intentionally hides cards).

## Implementation Notes
- Zone constructors should specify whether they hide cards (`hide_cards=True`) and whether they should suppress painting entirely. Use `hide_cards=True` for hand/library so cards get `face_down=True`. Only library (or other invisible zones) should additionally set `_zone_hidden=True`.
- Card movement needs to call `set_face_down`/`set_zone_hidden` appropriately when inserting or removing from these zones to keep rendering consistent.
