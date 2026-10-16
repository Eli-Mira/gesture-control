# Draft 2: AI Assistant Project - "Lain's Interface"

## Vision
A gorgeous, professional desktop AI assistant with:
- **C# WPF/WinUI 3 UI** — Modern, glassmorphism design, matches Lain's aesthetic
- **Python backend** — Local LLM inference via Ollama, CUA Driver integration
- **Local-first** — All models run on Mira, no cloud dependency
- **Professional polish** — Smooth animations, keyboard shortcuts, system tray

## Tech Stack

### Frontend (C#)
```
Framework: WinUI 3 (Windows App SDK)
Design: Fluent System Icons + Custom glassmorphism
Language: C# 12
Build: MSBuild
```

### Backend (Python)
```
Runtime: Python 3.12+
LLM: Ollama (MiniCPM, Gemma, Spark)
Automation: CUA Driver
Communication: HTTP REST + WebSocket
```

### Architecture
```
┌─────────────────────────────────────────────────────┐
│                    C# Frontend                      │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐  │
│  │  Chat UI    │ │  Quick Cmd  │ │  Settings   │  │
│  └─────────────┘ └─────────────┘ └─────────────┘  │
│         ↓ HTTP / WebSocket ↓                        │
│  ┌─────────────────────────────────────────────┐   │
│  │           Python Backend Service            │   │
│  │  ┌─────────┐ ┌──────────┐ ┌─────────────┐  │   │
│  │  │ Ollama  │ │ CUA      │ │ Memory /    │  │   │
│  │  │ Router  │ │ Driver   │ │ Context     │  │   │
│  │  └─────────┘ └──────────┘ └─────────────┘  │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## UI Design (The "Gorgeous" Part)

### Color Palette (Lain-themed)
```css
/* Main */
--primary-bg: #0d0d12
--secondary-bg: #1a1a2e
--accent: #6b5ce7 (purple)
--accent-glow: #8b7cf7

/* Glassmorphism */
--glass-bg: rgba(26, 26, 46, 0.7)
--glass-border: rgba(107, 92, 231, 0.3)
--glass-blur: 20px

/* Text */
--text-primary: #ffffff
--text-secondary: #a0a0b0
--text-muted: #606070
```

### Key UI Components
1. **Main Window**
   - Floating glassmorphism panel
   - Auto-hide to system tray
   - Keyboard shortcut activation (Ctrl+Alt+L)
   - Drag to reposition

2. **Chat Interface**
   - Message bubbles with typing indicators
   - Markdown rendering (code blocks, tables)
   - Model switcher dropdown (MiniCPM/Gemma/Spark)
   - Context window slider

3. **Command Panel** (Quicksilver-style)
   - Cmd+P to open
   - Type to search commands
   - Quick actions: "Take screenshot", "Click X,Y", "Type text"

4. **Settings Panel**
   - Model selection
   - CUA Driver status
   - Voice settings
   - Theme customization

## Backend Services

### 1. LLM Router (Python)
```python
# backend/llm_router.py
from ollama import Client

class ModelRouter:
    MODELS = {
        "daily": "minicpm5-2b-q8",      # Fast, 26 tok/s
        "heavy": "gemma4-e4b-unsloth",  # Quality, 87.9%
        "archive": "spark-x2.5-4b-external",  # Long context
    }
    
    def __init__(self):
        self.client = Client(host="http://localhost:11434")
        self.current_model = "daily"
    
    async def generate(self, prompt: str, stream: bool = True):
        response = self.client.chat(
            model=self.MODELS[self.current_model],
            messages=[{"role": "user", "content": prompt}],
            stream=stream,
            options={"num_ctx": 16384}
        )
        return response
    
    def switch_model(self, model_name: str):
        self.current_model = model_name
```

### 2. CUA Bridge (Python)
```python
# backend/cua_bridge.py
from cua_driver import Driver

class CUAService:
    def __init__(self):
        self.driver = None
    
    async def connect(self):
        self.driver = await Driver.start()
    
    async def click(self, x: int, y: int):
        await self.driver.mouse.click(x, y)
    
    async def type_text(self, text: str):
        await self.driver.keyboard.type(text)
    
    async def screenshot(self, path: str = None):
        if path is None:
            path = f"/tmp/screenshot_{int(time.time())}.png"
        await self.driver.screenshot(path)
        return path
    
    async def get_accessible_tree(self):
        """Get UI tree for AI to understand screens"""
        return await self.driver.accessibility.get_tree()
```

### 3. API Server (Python/FastAPI)
```python
# backend/api.py
from fastapi import FastAPI
from pydantic import BaseModel
import asyncio

app = FastAPI()

class ChatRequest(BaseModel):
    message: str
    model: str = "daily"
    stream: bool = False

class CommandRequest(BaseModel):
    action: str  # "click", "type", "screenshot"
    params: dict = {}

@app.post("/chat")
async def chat(req: ChatRequest):
    response = await llm_router.generate(req.message)
    return {"response": response}

@app.post("/command")
async def command(req: CommandRequest):
    result = await cua_service.execute(req.action, req.params)
    return {"status": "ok", "result": result}
```

## C# Frontend Code Structure

```
LainsInterface/
├── MainWindow.xaml          # Main glassmorphism window
├── ChatView.xaml           # Chat interface
├── CommandPalette.xaml     # Quicksilver-style command UI
├── SettingsView.xaml       # Configuration panel
├── Services/
│   ├── ApiClient.cs        # HTTP client to Python backend
│   ├── ModelManager.cs     # Model switching logic
│   └── ThemeManager.cs     # Glassmorphism styles
├── Assets/
│   ├── Icons/              # Fluent icons
│   └── Themes/             # CSS-like style resources
└── App.xaml                # Application entry point
```

### Example: ChatView.xaml
```xml
<Window x:Class="LainsInterface.ChatView"
        xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Background="Transparent"
        AllowsTransparency="True"
        WindowStyle="None">
    
    <Border CornerRadius="16" 
            Background="{StaticResource GlassBrush}"
            BorderBrush="{StaticResource AccentBrush}"
            BorderThickness="1">
        <Border.Effect>
            <BlurEffect Radius="20"/>
        </Border.Effect>
        
        <Grid>
            <!-- Chat messages area -->
            <ListView x:Name="MessageList" 
                      ItemsSource="{Binding Messages}">
                <ListView.ItemTemplate>
                    <DataTemplate>
                        <Border Background="{StaticResource BubbleBrush}"
                                CornerRadius="12"
                                Margin="8">
                            <TextBlock Text="{Binding Content}" 
                                       TextWrapping="Wrap"/>
                        </Border>
                    </DataTemplate>
                </ListView.ItemTemplate>
            </ListView>
            
            <!-- Input area -->
            <TextBox x:Name="InputBox" 
                     PlaceholderText="Ask Lain..."
                     KeyDown="InputBox_KeyDown"/>
        </Grid>
    </Border>
</Window>
```

## Features Checklist

### MVP (Week 1-2)
- [ ] Basic chat UI with glassmorphism
- [ ] Connect to Ollama backend
- [ ] Switch between MiniCPM/Gemma/Spark
- [ ] System tray icon with quick chat
- [ ] Keyboard shortcut (Ctrl+Alt+L)

### Phase 2 (Week 3-4)
- [ ] CUA Driver integration for background control
- [ ] Screenshot capture and display
- [ ] Command palette (Cmd+P)
- [ ] Voice input/output (optional)
- [ ] Conversation history

### Phase 3 (Week 5-6)
- [ ] Settings panel with all configurations
- [ ] Theme customization (dark/light/purple)
- [ ] Export/import conversations
- [ ] Mobile companion (optional)
- [ ] Auto-start on login

## Installation & Distribution

### Build Process
```powershell
# Backend (Python)
cd backend
pip install -r requirements.txt
python main.py

# Frontend (C#)
cd LainsInterface
dotnet build
.\bin\Debug\net8.0-windows\LainsInterface.exe
```

### Installer
- Use Inno Setup or NSIS for .exe installer
- Bundle Python runtime + Ollama + CUA Driver
- One-click install with all dependencies

## Why This Design

1. **Professional Polish**: Glassmorphism, smooth animations, Fluent Design principles
2. **Performance**: C# UI is native, Python backend handles heavy lifting
3. **Privacy**: All local, no cloud needed
4. **Extensible**: Easy to add new features, models, integrations
5. **Lain-themed**: Purple accents, dark mode, elegant feel

## Next Steps

1. Set up WinUI 3 project structure
2. Create basic glassmorphism window
3. Implement Python backend API
4. Connect frontend to backend
5. Integrate Ollama model switching
6. Add CUA Driver support

---
*Created: 2026-09-30*
*Status: Draft - Awaiting your green light*
