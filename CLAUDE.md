# CLAUDE.md - Sorcerous Development Guide

## Project Overview
Sorcerous is a Python-based virtual tabletop application for playtesting Magic: The Gathering decks. It provides an interactive canvas for dragging and organizing cards with virtual camera streaming support for OBS integration.

## Project Purpose
- **Primary Use Case**: Solo playtesting of Magic: The Gathering decks
- **Key Features**: Interactive card manipulation, zone-based organization, virtual camera streaming
- **Target Audience**: Magic players who want to playtest decks digitally

## Technology Stack

### Core Framework
- **PySide6** (Qt6 for Python)
  - QtCore: Event system, signals/slots, geometry primitives
  - QtGui: Graphics rendering, painting, undo/redo commands
  - QtWidgets: Application window, graphics view/scene architecture

### Required Dependencies
```python
PySide6          # GUI framework (Qt6 bindings)
NumPy            # Array operations for image processing
Requests         # HTTP client for Scryfall API
PyVirtualCam     # Virtual camera device creation
```

### External APIs
- **Scryfall API** (`https://api.scryfall.com`)
  - Rate limit: ~10 requests/second
  - Card data endpoint: `GET /cards/{set}/{number}`
  - Image endpoint: `GET /cards/{id}?format=image&version=png`

### Streaming Integration
- **OBS Virtual Camera** - Target for virtual camera output
- **Default Stream Settings**: 1280x720 @ 60 FPS, RGB format

## Project Structure
```
sorcerous/
├── project/
│   ├── __main__.py              # Application entry point
│   ├── card/
│   │   └── card.py              # Card graphics object (draggable, selectable)
│   ├── zones/
│   │   └── zone.py              # Zone container (card organization areas)
│   ├── model/
│   │   └── board.py             # Data model (game state)
│   ├── scene/
│   │   └── board.py             # Graphics scene and view (rendering)
│   ├── commands/
│   │   └── commands.py          # Undo/redo command implementations
│   ├── camera/
│   │   └── camera.py            # Virtual camera streaming thread
│   ├── cache/
│   │   └── cache.py             # Scryfall image caching system
│   ├── tests/
│   │   └── cache_test.py        # Cache functionality tests
│   ├── ui/                       # (Reserved for future UI components)
│   └── scryfall-cache/          # Local disk cache for card images
│       └── {SET}/{NUMBER}/{front|back}.png
├── README.md
└── .venv/                        # Virtual environment (recommended)
```

## Architecture Overview

### Component Responsibilities

#### 1. **MainWindow** ([\_\_main\_\_.py](project/__main__.py))
- Application orchestrator and entry point
- Manages BoardModel (data), BoardScene (graphics), BoardView (viewport)
- Handles virtual camera thread lifecycle
- Implements frame capture with 60 FPS rate limiting
- Coordinates user interactions and event handling

**Key Methods**:
- `capture_and_queue_frame()`: Captures viewport, converts to RGB, queues for streaming
- `_on_card_action()`: Triggered when cards move/release
- `closeEvent()`: Graceful shutdown of camera thread

#### 2. **Card** ([card/card.py](project/card/card.py))
- `QGraphicsObject` representing individual Magic cards
- Features:
  - Image rendering from cache or fallback colored rectangle
  - Drag-and-drop support (single or multi-select)
  - Selection state with visual highlighting
  - Signals: `moved` (emitted on position change)
- Performance: Implements rendering cache

#### 3. **Zone** ([zones/zone.py](project/zones/zone.py))
- `QGraphicsObject` container for organized card storage
- Features:
  - Slot-based layout system
  - Hover detection and visual feedback
  - Position calculation for card placement
  - Index detection for card insertion

**Key Methods**:
- `index_at(pos)`: Determines slot index for a given position
- `pos_for(index)`: Calculates scene position for card at index

#### 4. **BoardModel** ([model/board.py](project/model/board.py))
- Canonical data structure for game state
- Maintains:
  - Card positions: `{card_id: {"pos": QPointF}}`
  - Card containers: `{card_id: "zone_id" or "table"}`
  - Zone ordering: `{zone_id: {"order": [card_ids]}}`
- Supports undo/redo state persistence

#### 5. **BoardScene & BoardView** ([scene/board.py](project/scene/board.py))
**BoardScene**:
- Manages graphics scene with cards and zones
- Implements card-to-zone interaction logic
- Executes undo/redo commands via QUndoStack
- Zone hover state detection

**BoardView**:
- Custom QGraphicsView with zoom support (Ctrl + Mouse Wheel)
- Anchors zoom to mouse cursor position
- Disabled default drag mode for custom card interactions

#### 6. **Commands** ([commands/commands.py](project/commands/commands.py))
- Qt command pattern implementation for undo/redo
- Command classes:
  - `MoveCardsCommand`: Move card(s) between positions
  - `InsertIntoZoneCommand`: Add card to zone
  - `RemoveFromZoneCommand`: Remove card from zone
- All commands update both model and scene state

#### 7. **ScryfallImageCache** ([cache/cache.py](project/cache/cache.py))
- Three-tier caching architecture:
  1. **Memory Cache**: LRU cache (default 256 items)
  2. **Disk Cache**: `scryfall-cache/{SET}/{NUMBER}/{face}.png`
  3. **API Fetch**: Scryfall REST API

**Features**:
- Rate limiting: ~10 requests/second (100ms minimum between calls)
- Retry-After header compliance (HTTP 429 responses)
- PNG validation (magic bytes verification)
- Support for multi-face cards (front/back, modal)
- Thread-safe disk I/O with atomic writes
- Automatic cache directory creation

#### 8. **VirtualCamThread** ([camera/camera.py](project/camera/camera.py))
- Background daemon thread for camera streaming
- Features:
  - PyVirtualCam lifecycle management
  - Frame queue processing at target FPS
  - Auto-recovery on camera errors
  - Runtime parameter updates (resolution/FPS)
  - Queue size: 1 (drops old frames if backed up)

## Development Guidelines

### Running the Application
```bash
# From project root
cd c:\Users\Johnny\Documents\Python\sorcerous
python -m project

# Or from project directory
cd project
python __main__.py
```

### Running Tests
```bash
# From project root
python -m project.tests.cache_test

# Or from project directory
cd project
python tests/cache_test.py
```

### Code Style and Architecture

#### Qt Architecture Patterns
- **Model-View Separation**: BoardModel (data) separate from BoardScene/BoardView (graphics)
- **Command Pattern**: All state mutations through QUndoCommand subclasses
- **Signals/Slots**: Use Qt's signal/slot mechanism for loose coupling
- **Graphics Object Hierarchy**: Inherit from QGraphicsObject for interactive items

#### Threading Considerations
- **UI Thread**: All Qt graphics operations must run on main thread
- **Camera Thread**: Background daemon thread for streaming (no UI access)
- **Thread Safety**:
  - Disk cache uses `threading.Lock` for file I/O
  - Frame queue uses `queue.Queue` (thread-safe)
  - API rate limiter uses thread-local state

#### Performance Best Practices
- **Frame Capture Rate Limiting**: 60 FPS maximum (16.67ms between captures)
- **Scryfall API Rate Limiting**: 100ms minimum between requests
- **Rendering Cache**: Card implements `setCacheMode(DeviceCoordinateCache)`
- **Frame Queue**: Size 1 to drop old frames if camera can't keep up

### Adding New Features

#### Adding a New Card Action
1. Create QUndoCommand subclass in [commands.py](project/commands/commands.py)
2. Implement `redo()` and `undo()` methods
3. Update both BoardModel and BoardScene state
4. Push command to scene's undo stack

Example:
```python
class RotateCardCommand(QUndoCommand):
    def __init__(self, model, scene, card_id, new_rotation):
        super().__init__()
        self.model = model
        self.scene = scene
        self.card_id = card_id
        self.new_rotation = new_rotation
        self.old_rotation = model.cards[card_id].get("rotation", 0)

    def redo(self):
        self.model.cards[self.card_id]["rotation"] = self.new_rotation
        self.scene.cards[self.card_id].setRotation(self.new_rotation)

    def undo(self):
        self.model.cards[self.card_id]["rotation"] = self.old_rotation
        self.scene.cards[self.card_id].setRotation(self.old_rotation)
```

#### Adding a New Zone Type
1. Subclass Zone in [zone.py](project/zones/zone.py) or create new class
2. Override `paint()` for custom appearance
3. Override `pos_for()` for custom layout logic
4. Register in BoardScene during initialization
5. Update BoardModel to track new zone type

#### Extending the Cache System
- **Cache Location**: Modify `ScryfallImageCache(root="custom-path")`
- **Memory Cache Size**: Adjust `capacity` parameter in `_LRU` initialization
- **Rate Limit**: Modify `_RateLimiter` min interval (currently 0.1 seconds)
- **Image Format**: Currently PNG only; extend `_fetch_from_scryfall()` for other formats

### Common Pitfalls and Solutions

#### 1. Circular Import Errors
**Problem**: Importing commands from scene and scene from commands creates cycle

**Solution**:
```python
# Test for circular dependencies
python -c "import sys; sys.path.insert(0, '..'); from commands.commands import InsertIntoZoneCommand, RemoveFromZoneCommand; from scene.board import BoardScene; print('No circular dependency - imports successful!')"
```

Current architecture avoids this by having commands only import from model, not scene.

#### 2. Virtual Camera Not Starting
**Possible Causes**:
- OBS Virtual Camera plugin not installed
- Another application using virtual camera
- PyVirtualCam not installed correctly

**Debug Steps**:
```python
# Test camera availability
import pyvirtualcam
print(pyvirtualcam.Camera())  # Should not raise exception
```

#### 3. Card Images Not Loading
**Check**:
1. Disk cache directory exists: `scryfall-cache/{SET}/{NUMBER}/`
2. PNG files are valid (check magic bytes: `89 50 4E 47`)
3. Scryfall API accessibility: `curl https://api.scryfall.com/cards/9ED/100`
4. Rate limit not exceeded (check console for 429 errors)

**Manual Cache Test**:
```python
from cache.cache import ScryfallImageCache
cache = ScryfallImageCache()
path = cache.get("9ED", "100", face="front")
print(path)  # Should print path to PNG file
```

#### 4. Undo/Redo Not Working
**Common Issues**:
- Forgetting to push command to undo stack
- Modifying state outside of commands
- Not implementing both `redo()` and `undo()`

**Debug**:
```python
# Check undo stack state
print(scene.undo_stack.count())  # Number of commands
print(scene.undo_stack.canUndo())  # Should be True if actions performed
```

## Current Implementation Status

### Implemented Features
- [x] Interactive card dragging on canvas
- [x] Multi-select card support (Shift + Click, drag multiple)
- [x] Zone-based card organization
- [x] Card-to-zone drop detection and insertion
- [x] Zone-to-table card removal
- [x] Full undo/redo system for card movements
- [x] Viewport zoom (Ctrl + Mouse Wheel)
- [x] Virtual camera streaming to OBS
- [x] Frame capture at 60 FPS
- [x] Three-tier Scryfall image caching (memory/disk/API)
- [x] Multi-face card support (transform/modal)
- [x] Rate limiting for API requests
- [x] Graceful error handling

### Not Yet Implemented
- [ ] Card search/filtering UI
- [ ] Deck import/export (e.g., from MTG Arena, MTGO)
- [ ] Game rules enforcement (phases, priority, stack)
- [ ] Tap/untap card rotation
- [ ] Card counter/token support
- [ ] Life total tracking
- [ ] Graveyard/exile zone UI
- [ ] Multiplayer support (local or networked)
- [ ] Save/load game state
- [ ] Keyboard shortcuts (beyond Ctrl+Scroll zoom)
- [ ] Card text display/zoom on hover
- [ ] Deck building interface
- [ ] Settings/preferences UI

## Testing and Quality Assurance

### Manual Testing Checklist
- [ ] Cards drag smoothly without stuttering
- [ ] Multi-select works (Shift + Click on cards)
- [ ] Cards snap to zone slots when dropped
- [ ] Undo/redo restores exact previous state
- [ ] Zoom centers on mouse cursor position
- [ ] Virtual camera stream visible in OBS
- [ ] Frame rate maintains 60 FPS during card movement
- [ ] Card images load from cache/Scryfall
- [ ] Application closes cleanly (camera thread stops)

### Unit Testing
Currently minimal test coverage. Recommended additions:
- BoardModel state mutations
- Command redo/undo correctness
- Cache tier fallback logic
- Zone position calculations
- Card collision detection

### Integration Testing
- Scryfall API interaction (requires network)
- Virtual camera initialization (requires OBS plugin)
- Disk cache read/write operations

## Deployment Notes

### Installation Requirements
1. Python 3.9+ (project uses type hints, dataclasses)
2. Virtual environment recommended:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   source .venv/bin/activate  # Linux/Mac
   ```
3. Install dependencies:
   ```bash
   pip install PySide6 numpy requests pyvirtualcam
   ```
4. Install OBS Virtual Camera plugin (for streaming feature)

### File System Requirements
- **Write Access**: `scryfall-cache/` directory for image storage
- **Disk Space**: ~10 MB per 100 cached cards (PNG images)
- **Network Access**: Scryfall API at `https://api.scryfall.com`

### Platform Considerations
- **Windows**: Tested on Windows (working directory uses backslashes)
- **Linux/Mac**: Should work but may need OBS Virtual Camera alternatives
- **PyVirtualCam Backend**: Currently uses OBS; other backends may work

## Maintenance and Extension

### Code Organization Principles
1. **Separation of Concerns**: Model (data) vs. Scene (graphics) vs. View (viewport)
2. **Command Pattern**: All state changes through QUndoCommand subclasses
3. **Minimal Dependencies**: Only essential third-party libraries
4. **Type Hints**: Use Python type annotations for clarity
5. **Docstrings**: Document public methods and non-obvious logic

### Future Refactoring Opportunities
- **Configuration File**: Extract hardcoded values (FPS, resolution, cache size)
- **Dependency Injection**: Pass cache instance to cards instead of global
- **Event System**: Centralized event dispatcher instead of signal chains
- **Plugin Architecture**: Support for custom zones, card actions, game modes
- **Data Persistence**: Save/load board state to JSON or database

### Performance Optimization Opportunities
- **Frame Capture**: Only capture on card movement, not continuous
- **Image Loading**: Lazy load images only when cards enter viewport
- **Zone Rendering**: Cache zone backgrounds instead of repainting each frame
- **Multi-Face Cards**: Preload both faces when first fetched

## Known Issues and Limitations

### Current Limitations
1. **No Network Play**: Single-user application only
2. **No Rules Engine**: No automatic game rule enforcement
3. **Limited Card Data**: Only images loaded, no card text/rules data
4. **Hardcoded Sample Data**: 2 cards + 1 zone created at startup
5. **No Persistence**: Game state lost on application close
6. **No Deck Loading**: Cards must be added programmatically

### Known Bugs
- None currently documented (project is in early development)

### Performance Considerations
- **Frame Capture Overhead**: Grabbing viewport at 60 FPS may impact low-end systems
- **Memory Usage**: LRU cache (256 items) + loaded card images in scene
- **API Rate Limiting**: 10 requests/second may be slow for loading large decks

## External Resources

### Documentation
- **PySide6 Docs**: https://doc.qt.io/qtforpython/
- **Qt Graphics View Framework**: https://doc.qt.io/qt-6/graphicsview.html
- **Scryfall API Docs**: https://scryfall.com/docs/api
- **PyVirtualCam Docs**: https://github.com/letmaik/pyvirtualcam

### Magic: The Gathering Resources
- **Scryfall**: Card database and search (https://scryfall.com)
- **MTGJSON**: Card data in JSON format (https://mtgjson.com)
- **MTG Gatherer**: Official card database (https://gatherer.wizards.com)

## Contributing Guidelines

### Code Contributions
1. Follow existing architecture patterns (Model-View-Command)
2. Add type hints to all new functions
3. Update this CLAUDE.md file when adding major features
4. Test undo/redo for any new state-changing commands
5. Document public APIs with docstrings

### Adding New Features
1. Check "Not Yet Implemented" list above for planned features
2. Create issue/discussion for major architectural changes
3. Ensure backward compatibility with existing code
4. Add tests for new functionality

---

**Last Updated**: 2025-11-02
**Project Status**: Early development / Proof of concept
**Primary Maintainer**: Johnny

*This project prioritizes simplicity, maintainability, and extensibility over feature completeness. The architecture is designed to support future enhancements while keeping the codebase approachable.*
