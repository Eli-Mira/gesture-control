# Draft 1: CUA Integration for Gesture Control

## Overview
Integrate CUA Driver into the existing gesture control app to add background automation capabilities without rewriting the core hand-tracking system.

## What Changes

### Current Architecture
```
Camera → Hand Landmarks → Gesture Engine → Mouse/Keyboard Events → Target App
```

### New Architecture (with CUA)
```
Camera → Hand Landmarks → Gesture Engine → 
  ├─ Immediate actions (mouse/keyboard) → Direct
  └─ Complex sequences → CUA Driver MCP → Background automation
```

## Integration Points

### 1. CUA Driver Layer
```python
# gesture_control/cua_backend.py
import asyncio
from cua_driver import Driver

class CUAController:
    def __init__(self):
        self.driver = None
    
    async def start(self):
        """Start CUA background driver"""
        self.driver = await Driver.start()
    
    async def execute_sequence(self, steps: list[dict]):
        """Execute multi-step automation sequence"""
        for step in steps:
            action = step["action"]
            if action == "click":
                await self.driver.mouse.click(step["x"], step["y"])
            elif action == "type":
                await self.driver.keyboard.type(step["text"])
            elif action == "screenshot":
                await self.driver.screenshot(step.get("path", "/tmp/screen.png"))
    
    async def close(self):
        await self.driver.stop()
```

### 2. Gesture Mapping Extension
```python
# gesture_control/gesture_map.py
GESTURE_TO_ACTION = {
    # Existing direct mappings
    "point": ("click",),
    "swipe": ("drag",),
    
    # New CUA-enhanced mappings
    "double_tap": ("double_click",),
    "pinch": ("scroll",),
    "open_hand_hold": ("screenshot",),  # Triggers CUA screenshot
    "wave": ("close_app",),  # Via CUA accessibility API
}

ACTION_ROUTER = {
    "direct": ["click", "drag", "move"],  # Mouse/keyboard directly
    "cua": ["screenshot", "close_app", "sequence"],  # Via CUA
}
```

### 3. MCP Bridge (for AI control)
```python
# gesture_control/mcp_bridge.py
from cua_driver import MCPClient

class GestureMCPBridge:
    """Bridge between Hermes and CUA for AI-controlled gestures"""
    
    def __init__(self):
        self.mcp = MCPClient()
    
    async def handle_ai_command(self, command: str):
        """Convert natural language to CUA actions"""
        # Parse: "scroll down 3 times" → [scroll, scroll, scroll]
        # Parse: "click settings icon" → find_by_accessibility("settings")
        actions = await self.parse_command(command)
        return await self.mcp.execute(actions)
```

## Benefits

| Before | After |
|--------|-------|
| Only works in foreground | Background automation possible |
| Limited to mouse/keyboard | Full app control via accessibility APIs |
| Single gesture → single action | Complex sequences from one gesture |
| No screen context | Can take screenshots for AI decision making |

## Implementation Phases

### Phase 1: Core Integration (2-3 days)
- [ ] Install cua-driver via pip
- [ ] Create CUAController wrapper
- [ ] Test basic click/type operations
- [ ] Add configuration UI for gesture mappings

### Phase 2: Gesture Extension (3-4 days)
- [ ] Add new gesture types (double_tap, pinch, hold)
- [ ] Implement action router
- [ ] Test cross-application workflows

### Phase 3: AI Bridge (3-5 days)
- [ ] Set up MCP connection to Hermes
- [ ] Implement command parsing
- [ ] Add screenshot-based decision making

## Dependencies
```yaml
cua-driver: ^0.5.0
opencv-python: ^4.9.0
mediapipe: ^0.10.0
mcp-client: ^0.2.0
```

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| CUA requires accessibility permissions | Guide user through Windows security settings |
| AGPL perception extension license | Use only MIT core driver, not perception |
| Background input blocking | Implement focus detection, warn user |
| Performance overhead | Run CUA on separate thread, throttle screenshots |

## Next Steps
1. Install cua-driver and test basic operations
2. Create proof-of-concept: swipe gesture → open Notepad → type text → save file
3. Evaluate performance impact on hand tracking

---
*Created: 2026-09-30*
*Status: Draft - Ready for review*
