# wincua

Public façade: `Agent`, `Session`, and re-exports of providers / HITL / driver.

```python
from wincua import Agent, Session
from wincua.providers import OllamaProvider, NullProvider
from wincua.hitl import LocalUI

agent = Agent(
    task="Open Calculator and compute 17 + 25",
    provider=NullProvider(),
    hitl=LocalUI(port=8765),
    allow=["CalculatorApp.exe"],
)
# result = await agent.run()  # host Windows
```
