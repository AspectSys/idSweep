import pyvisa
import time

rm = pyvisa.ResourceManager()

print("1. Taking control of the main GPIB Controller...")
try:
    # Notice we are opening the PC's GPIB board directly, NOT the instrument
    gpib_board = rm.open_resource("GPIB0::INTFC")
    
    print("2. Sending hardware IFC (Interface Clear) pulse...")
    gpib_board.send_ifc()
    time.sleep(2) # Give the Keithley 2 seconds to reboot its GPIB chip
    
    print("   GPIB Bus Reset Complete.")
except Exception as e:
    print(f"   Failed to reset bus: {e}")

print("\n3. Attempting to wake 4200-SCS...")
try:
    keithley = rm.open_resource("GPIB0::17::INSTR")
    keithley.timeout = 3000
    keithley.write_termination = '\r\n'
    keithley.read_termination = None
    
    # Send a quick Device Clear just to be doubly safe
    keithley.clear()
    time.sleep(0.5)

    print("4. Sending legacy 'ID' ping...")
    keithley.write("ID")
    
    response = keithley.read_raw()
    print(f"\n[SUCCESS] The beast is awake: {response.decode('ascii', errors='ignore').strip()}")

except Exception as e:
    print(f"\n[FATAL ERROR] {e}")