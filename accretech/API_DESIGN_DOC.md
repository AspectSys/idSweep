To extract the Accretech Wafer Prober functionality from the `SweepMe!` ecosystem and create a robust, standalone Python API, you will need to decouple the hardware communication from the SweepMe! GUI/Sequencer lifecycle. 



Here is a sophisticated, step-by-step strategy to architecture and refactor the code into a standalone library.



\---



\### Phase 1: Architecture \& Package Structure

Instead of having two files tightly coupled to SweepMe!, split the functionality into a well-structured Python package with distinct layers:



1\. \*\*`communication.py` (Transport Layer):\*\* Handles pure PyVISA interactions (read/write/SRQ events).

2\. \*\*`core.py` (Low-Level API):\*\* The translation layer (formerly `accretech\_uf.py`). Maps Python methods to machine commands (e.g., `z\_up()`, `move\_specified\_die()`).

3\. \*\*`controller.py` (High-Level Workflow):\*\* The logic extracted from `main.py`. Manages state (current die/wafer), file parsing, and complex movements.

4\. \*\*`parsers.py` (Utility):\*\* Handles reading the `.mdf` probe plan files.

5\. \*\*`exceptions.py` (Error Handling):\*\* Custom Python exceptions to replace GUI message boxes.



\---



\### Phase 2: Decoupling Hardware Communication (Refactoring `accretech\_uf.py`)

Currently, `AccretechProber` relies heavily on `pysweepme.Ports.Port`. You need to standardize this to use native `pyvisa`.



\*   \*\*Remove SweepMe Imports:\*\* Strip out `pysweepme.Ports` and `pysweepme.UserInterface`.

\*   \*\*Direct PyVISA Integration:\*\* Modify the constructor to accept a standard `pyvisa.resources.MessageBasedResource` or simply a VISA address string, handling the connection internally.

\*   \*\*Fix Event Handling (SRQ):\*\* The current code handles Service Requests (SRQ) using `self.port.port.wait\_on\_event`. You need to ensure you set up the PyVISA event queue and enable `constants.EventType.service\_request` cleanly via standard PyVISA.

\*   \*\*Replace User Prompts:\*\* The driver currently uses `get\_input` to pause execution when a recoverable error occurs (status byte 76). In a standalone API, hardware drivers shouldn't use `input()`. Instead:

&#x20;   \*   Raise a custom exception (e.g., `AccretechRecoverableError`).

&#x20;   \*   Alternatively, accept an optional `error\_callback` function in the class initialization that the user can define to trigger a GUI popup in their own app.



\---



\### Phase 3: Extracting the Workflow Logic (Refactoring `main.py`)

`main.py` is essentially a state machine mapped to SweepMe's `initialize`, `apply`, and `unconfigure` steps. This needs to be transformed into a scriptable workflow class (e.g., `ProberController`).



\*   \*\*State Management:\*\* Extract the variables initialized in `configure()` (`self.last\_wafer`, `self.last\_die`, `self.last\_sub`) into the `\_\_init\_\_` of your new `ProberController` class.

\*   \*\*Refactoring `apply()`:\*\* The `apply()` method currently contains the entire logic for navigating wafers, dies, and subsites. Break this monolithic function into atomic API calls:

&#x20;   \*   `load\_wafer(cassette, slot)`

&#x20;   \*   `move\_to\_die(x, y)`

&#x20;   \*   `move\_to\_subsite(dx, dy)`

\*   \*\*Handling the Probe Plan (.mdf):\*\* Move the `read\_controlmap` function into your `parsers.py` file. Create a function that yields the next die coordinates as an iterator/generator, making it easy to write a simple `for die in probe\_plan:` loop in standard Python.



\---



\### Phase 4: Modernizing Logging and Error Handling

SweepMe! uses `message\_box` and `print` for debugging and user info. This is bad practice for a standalone library.



\*   \*\*Implement Python `logging`:\*\* Replace `self.\_verbose` and `print()` statements with Python's standard `logging` module. Use `logger.debug()` for low-level STB byte polling and `logger.info()` for workflow steps (e.g., "Moving to die (1, 3)").

\*   \*\*Custom Exceptions:\*\* Build a hierarchy of exceptions:

&#x20;   \*   `AccretechError(Exception)` -> Base class

&#x20;   \*   `CommunicationTimeout(AccretechError)` -> For PyVISA timeouts.

&#x20;   \*   `HardwareAlarm(AccretechError)` -> For Prober alarms.

&#x20;   \*   `WaferNotLoadedError(AccretechError)` -> When trying to probe an empty chuck.



\---



\### Phase 5: Designing the Target API Interface (The End Goal)

Before writing the code, define how you want a user (or yourself) to write a test script with your new API. It should look clean and Pythonic, like this:



```python

import pyvisa

from accretech\_prober import AccretechProber, ProberController

from accretech\_prober.parsers import read\_mdf\_probe\_plan



\# 1. Initialize VISA and Low-level driver

rm = pyvisa.ResourceManager()

visa\_resource = rm.open\_resource("GPIB0::1::INSTR")

prober\_hw = AccretechProber(visa\_resource)



\# 2. Initialize High-Level Controller

controller = ProberController(prober\_hw)



\# 3. Execution logic (Replacing SweepMe Sequencer)

try:

&#x20;   controller.initialize\_system()

&#x20;   controller.check\_and\_sense\_wafers()

&#x20;   

&#x20;   # Load specific wafer

&#x20;   controller.load\_wafer(cassette=1, slot=1)

&#x20;   

&#x20;   # Read plan and iterate

&#x20;   dies = read\_mdf\_probe\_plan("plan.mdf")

&#x20;   for die\_x, die\_y in dies:

&#x20;       controller.move\_to\_die(die\_x, die\_y)

&#x20;       

&#x20;       # Do your SMU electrical measurements here!

&#x20;       # measure\_current()

&#x20;       

&#x20;   controller.unload\_wafer()



except Exception as e:

&#x20;   controller.abort\_and\_safe\_state()

&#x20;   print(f"Error: {e}")

```



\### Summary of Next Steps for Implementation:

1\. Setup a fresh Python environment and install `pyvisa`.

2\. Create the `AccretechProber` class first, replacing SweepMe's port with `pyvisa.ResourceManager().open\_resource(...)`. Test basic queries like requesting the prober ID (`B` command) to ensure communication works.

3\. Once basic STB/SRQ polling works over pure PyVISA, copy the logic from `main.py` into a new controller class.

4\. Finally, decouple the `.mdf` file reading logic.

